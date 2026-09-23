"""sheet2audio: sheet music PDF -> MusicXML -> MIDI -> audio + play-along viewer.

Every program involved is free/open-source software:
  Audiveris (AGPL-3.0)  optical music recognition, PDF/image -> MusicXML
  Verovio   (LGPL-3.0)  MusicXML -> engraved SVG, MIDI and a note timemap
  FluidSynth (LGPL-2.1) MIDI -> audio with a SoundFont
  FFmpeg    (LGPL/GPL)  WAV -> MP3/FLAC/OGG
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from . import omr, synth, tools
from .musicxml import has_tempo_mark, read_musicxml_bytes, repair_underfull_measures
from .render import combine_midi, render_musicxml
from .viewer import write_viewer

MOVEMENT_GAP_S = 2.0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="sheet2audio",
        description=(
            "Turn a PDF or image of sheet music into audio and a play-along page that "
            "highlights each note as it sounds. Also accepts MusicXML (.mxl/.musicxml/.xml) "
            "or an Audiveris book (.omr) you corrected by hand."
        ),
    )
    p.add_argument("input", type=Path, help="PDF, image, MusicXML or .omr file")
    p.add_argument("-o", "--outdir", type=Path,
                   help="output folder (default: <input name>_sheet2audio next to the input)")
    tempo = p.add_mutually_exclusive_group()
    tempo.add_argument("--bpm", type=float,
                       help="playback tempo in quarter notes per minute (default: the score's "
                            "tempo mark if OMR read one, else 120)")
    tempo.add_argument("--tempo-scale", type=float, default=1.0,
                       help="multiply the score's tempo by this factor (e.g. 0.8 = slower)")
    p.add_argument("--soundfont", help="SoundFont (.sf2/.sf3) to play the MIDI with")
    p.add_argument("--formats", default="mp3,wav",
                   help="audio formats to write, comma-separated from mp3,wav,flac,ogg "
                        "(default: mp3,wav)")
    p.add_argument("--no-repair", action="store_true",
                   help="do not pad measures that OMR read too short")
    p.add_argument("--layout", choices=["encoded", "auto"], default="encoded",
                   help="'encoded' keeps the line breaks of the original page (default); "
                        "'auto' lets Verovio re-flow the music")
    p.add_argument("--no-viewer", action="store_true", help="skip the HTML play-along page")
    p.add_argument("--link-audio", action="store_true",
                   help="make the HTML page load the MP3 next to it instead of embedding it")
    p.add_argument("--sheets", help="only these pages, e.g. '1 3-4' (PDF/image input)")
    p.add_argument("--audiveris", help="path to the Audiveris executable or Audiveris.app")
    p.add_argument("--open", action="store_true", help="open the play-along page when done")
    p.add_argument("--musescore", action="store_true",
                   help="also open the MusicXML in MuseScore (free notation editor that "
                        "highlights notes during playback and lets you fix OMR mistakes)")
    p.add_argument("-q", "--quiet", action="store_true")
    a = p.parse_args(argv)
    fmts = [f.strip().lower() for f in a.formats.split(",") if f.strip()]
    bad = [f for f in fmts if f not in synth.AUDIO_CODECS]
    if bad or not fmts:
        p.error(f"unknown audio format(s): {', '.join(bad) or '(none)'}")
    a.formats = fmts
    if a.bpm is not None and a.bpm <= 0:
        p.error("--bpm must be positive")
    if a.tempo_scale <= 0:
        p.error("--tempo-scale must be positive")
    return a


class _Log:
    def __init__(self, quiet: bool):
        self.quiet = quiet
        self.t0 = time.monotonic()

    def step(self, msg: str) -> None:
        if not self.quiet:
            print(f"[{time.monotonic() - self.t0:5.1f}s] {msg}", file=sys.stderr, flush=True)


def run(argv: list[str] | None = None) -> dict:
    a = _parse_args(argv)
    log = _Log(a.quiet)
    src: Path = a.input.expanduser().resolve()
    if not src.is_file():
        raise SystemExit(f"error: no such file: {a.input}")
    suffix = src.suffix.lower()
    if suffix not in omr.IMAGE_SUFFIXES | omr.MUSICXML_SUFFIXES | {".omr"}:
        raise SystemExit(
            f"error: don't know how to read '{src.suffix}' files. Give a PDF, an image "
            "(PNG/JPEG/TIFF), MusicXML (.mxl/.musicxml/.xml) or an Audiveris .omr book."
        )
    outdir = (a.outdir or src.with_name(f"{src.stem}_sheet2audio")).expanduser().resolve()
    outdir.mkdir(parents=True, exist_ok=True)
    stem = src.stem

    # Resolve every tool before doing any slow work, so a missing one fails fast.
    fluidsynth = tools.find_fluidsynth()
    ffmpeg = tools.find_ffmpeg()
    soundfont = tools.find_soundfont(a.soundfont)
    notes: list[str] = []
    report: dict = {"input": str(src), "outdir": str(outdir), "soundfont": str(soundfont)}

    # 1. Optical music recognition
    if suffix in omr.MUSICXML_SUFFIXES:
        movement_files = [src]
        log.step(f"Reading MusicXML {src.name}")
    else:
        audiveris = tools.find_audiveris(a.audiveris)
        log.step(f"Recognising the music with Audiveris ({src.name}); about 5-15 s per page")
        res = omr.run_audiveris(audiveris, src, outdir, sheets=a.sheets)
        movement_files = res.movements
        report["audiveris_log"] = str(res.log)
        if res.book:
            report["audiveris_book"] = str(res.book)
        warns = omr.log_warnings(res.log)
        if warns:
            report["audiveris_warnings"] = warns
        log.step(f"Audiveris found {len(movement_files)} movement(s)")

    # 2. Repair + 3. render each movement
    rendered = []
    repaired_paths = []
    multi = len(movement_files) > 1
    for i, mf in enumerate(movement_files, 1):
        xml = read_musicxml_bytes(mf)
        label = f"Movement {i}" if multi else stem
        if not a.no_repair:
            xml, rep = repair_underfull_measures(xml)
            for line in rep.summary_lines():
                notes.append(f"{label}, {line}" if multi else line[0].upper() + line[1:])
        name = f"{stem}.mvt{i}.musicxml" if multi else f"{stem}.musicxml"
        (outdir / name).write_bytes(xml)
        repaired_paths.append(outdir / name)
        r = render_musicxml(xml, title=label, bpm=a.bpm, tempo_scale=a.tempo_scale,
                            layout=a.layout)
        notes.extend(r.warnings)
        if r.note_count == 0:
            notes.append(f"{label}: no notes were recognised.")
        rendered.append(r)
        log.step(f"{label}: {r.note_count} notes, {len(r.svgs)} page(s), "
                 f"{r.duration_s:.1f} s at {r.base_tempo * r.tempo_scale:g} BPM")
    if sum(r.note_count for r in rendered) == 0:
        raise SystemExit(
            "error: no notes were recognised. If this is a scan, try a cleaner/higher-resolution "
            f"one (300 dpi), or open {outdir / 'omr'} in Audiveris to see what it found."
        )
    if a.bpm is None and not any(has_tempo_mark(p.read_bytes()) for p in repaired_paths):
        notes.append(
            f"No tempo mark was read, so playback uses "
            f"{rendered[0].base_tempo * rendered[0].tempo_scale:g} BPM; use --bpm to change it."
        )

    # 4. One MIDI for the whole piece; movements follow each other with a short gap.
    offsets = []
    t = 0.0
    for r in rendered:
        offsets.append(t)
        t += r.duration_s + MOVEMENT_GAP_S
    midi_path = outdir / f"{stem}.mid"
    midi_path.write_bytes(combine_midi([(r.midi, off) for r, off in zip(rendered, offsets)]))

    # 5. Synthesize and encode
    log.step(f"Synthesizing audio with FluidSynth ({soundfont.name})")
    with tempfile.TemporaryDirectory(prefix="sheet2audio-") as tmp:
        raw = Path(tmp) / "raw.wav"
        synth.render_wav(fluidsynth, soundfont, midi_path, raw)
        outputs = {fmt: outdir / f"{stem}.{fmt}" for fmt in a.formats}
        synth.encode(ffmpeg, raw, outputs)
    report["audio"] = {k: str(v) for k, v in outputs.items()}
    report["midi"] = str(midi_path)
    report["musicxml"] = [str(p) for p in repaired_paths]
    report["movements"] = [
        {"title": r.title, "notes": r.note_count, "pages": len(r.svgs),
         "duration_s": round(r.duration_s, 3), "offset_s": round(off, 3),
         "bpm": round(r.base_tempo * r.tempo_scale, 3)}
        for r, off in zip(rendered, offsets)
    ]

    # 6. Play-along page
    if not a.no_viewer:
        viewer_audio = outputs.get("mp3") or outputs.get("ogg") or next(iter(outputs.values()))
        html_path = outdir / f"{stem}.html"
        write_viewer(html_path, stem, list(zip(rendered, offsets)), viewer_audio, notes,
                     embed_audio=not a.link_audio)
        report["viewer"] = str(html_path)
        log.step(f"Wrote play-along page {html_path.name}")

    report["notes"] = notes
    (outdir / "report.json").write_text(json.dumps(report, indent=2))

    if not a.quiet:
        print(f"\nDone. Output folder: {outdir}")
        for key in ("viewer", "midi"):
            if key in report:
                print(f"  {key:9s} {Path(report[key]).name}")
        for fmt, pth in report["audio"].items():
            print(f"  {fmt:9s} {Path(pth).name}")
        if notes:
            print("\nThings to check (OMR is not perfect):")
            for n in notes:
                print(f"  - {n}")
        if "audiveris_book" in report:
            print(f"\nTo fix recognition mistakes: open {report['audiveris_book']} in Audiveris,\n"
                  f"correct it, save, then run: sheet2audio {report['audiveris_book']}\n"
                  f"(or edit {repaired_paths[0].name} in MuseScore and run sheet2audio on it).")

    if a.open and "viewer" in report:
        _open(Path(report["viewer"]))
    if a.musescore:
        ms = tools.find_musescore()
        if ms is None:
            print("MuseScore was not found (free download: https://musescore.org).", file=sys.stderr)
        else:
            subprocess.Popen([str(ms), str(repaired_paths[0])],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return report


def _open(path: Path) -> None:
    opener = "open" if sys.platform == "darwin" else "xdg-open"
    subprocess.Popen([opener, str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main(argv: list[str] | None = None) -> None:
    try:
        run(argv)
    except (tools.MissingTool, omr.OMRError, synth.SynthError) as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
