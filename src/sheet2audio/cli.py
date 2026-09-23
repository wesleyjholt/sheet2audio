"""sheet2audio: sheet music PDF -> MusicXML -> MIDI -> audio, video and a play-along page.

Every program involved is free/open-source software:
  Audiveris  (AGPL-3.0)   optical music recognition, PDF/image -> MusicXML
  Verovio    (LGPL-3.0)   MusicXML -> engraved SVG, MIDI and a note timemap
  FluidSynth (LGPL-2.1)   MIDI -> audio with a SoundFont
  FFmpeg     (LGPL/GPL)   audio and video encoding
  librsvg    (LGPL-2.1+)  video frames
"""

from __future__ import annotations

import argparse
import functools
import hashlib
import http.server
import json
import math
import os
import re
import shlex
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path

from . import musicxml, omr, synth, tools
from .render import MovementJob, RenderError, combine_midi, process_movements
from .video import VideoError, VideoMovement, render_video
from .viewer import write_viewer

MOVEMENT_GAP_S = 2.0
TAIL_S = 2.0  # audio kept after the last note ends
INPUT_SUFFIXES = (omr.PDF_SUFFIXES | omr.IMAGE_SUFFIXES | omr.MUSICXML_SUFFIXES
                  | {omr.BOOK_SUFFIX})


class UsageError(Exception):
    pass


def _positive_finite(name: str, lo: float, hi: float):
    def conv(text: str) -> float:
        try:
            v = float(text)
        except ValueError:
            raise argparse.ArgumentTypeError(f"{name} must be a number") from None
        if not math.isfinite(v) or not lo <= v <= hi:
            raise argparse.ArgumentTypeError(f"{name} must be between {lo:g} and {hi:g}")
        return v
    return conv


def _time_sig(text: str) -> tuple[int, int]:
    m = re.fullmatch(r"\s*(\d{1,2})\s*/\s*(1|2|4|8|16|32)\s*", text)
    if not m or int(m.group(1)) == 0:
        raise argparse.ArgumentTypeError("use a time signature like 3/4 or 6/8")
    return int(m.group(1)), int(m.group(2))


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="sheet2audio",
        description=(
            "Turn a PDF or image of sheet music into audio, a score-following video (plays on "
            "phones) and a play-along web page that highlights each note as it sounds. Also "
            "accepts MusicXML (.mxl/.musicxml/.xml) or an Audiveris book (.omr) you corrected."
        ),
    )
    p.add_argument("input", type=Path, help="PDF, image, MusicXML or .omr file")
    p.add_argument("-o", "--outdir", type=Path,
                   help="output folder (default: <input name>_sheet2audio next to the input)")
    tempo = p.add_mutually_exclusive_group()
    tempo.add_argument("--bpm", type=_positive_finite("--bpm", 10, 600),
                       help="starting tempo in quarter notes per minute; later tempo changes "
                            "keep their ratio (default: the score's tempo mark, else 120)")
    tempo.add_argument("--tempo-scale", type=_positive_finite("--tempo-scale", 0.05, 10),
                       default=1.0, help="multiply every tempo by this (e.g. 0.8 = slower)")
    p.add_argument("--time", type=_time_sig, metavar="N/D",
                   help="time signature to use if OMR read none, e.g. 3/4")
    p.add_argument("--soundfont", help="SoundFont (.sf2/.sf3) to play the music with")
    p.add_argument("--formats", default="mp3,wav",
                   help=f"audio formats, comma-separated from {','.join(synth.FORMATS)} "
                        "(default: mp3,wav)")
    p.add_argument("--no-video", action="store_true",
                   help="skip the MP4 score video (for phones, TVs and video players)")
    p.add_argument("--no-viewer", action="store_true", help="skip the HTML play-along page")
    p.add_argument("--no-repair", action="store_true",
                   help="report bars that play too short, but do not pad them")
    p.add_argument("--layout", choices=["encoded", "auto"], default="encoded",
                   help="'encoded' keeps the line breaks of the original page (default); "
                        "'auto' lets Verovio re-flow the music")
    p.add_argument("--link-audio", action="store_true",
                   help="make the HTML page load the MP3 next to it instead of embedding it")
    p.add_argument("--sheets", help="only these pages, e.g. '1 3-4' (PDF/image input)")
    p.add_argument("--audiveris", help="path to the Audiveris executable or Audiveris.app")
    p.add_argument("--open", action="store_true", help="open the play-along page when done")
    p.add_argument("--musescore", action="store_true",
                   help="also open the MusicXML in MuseScore (free notation editor that "
                        "highlights notes during playback and lets you fix OMR mistakes)")
    p.add_argument("--serve", nargs="?", const=8000, type=int, metavar="PORT",
                   help="afterwards, share the output folder on your Wi-Fi so a phone can open "
                        "the play-along page (default port 8000; Ctrl-C to stop)")
    p.add_argument("-q", "--quiet", action="store_true")
    p.add_argument("--debug", action="store_true", help="show Python tracebacks on errors")
    a = p.parse_args(argv)
    fmts = [f.strip().lower().lstrip(".") for f in a.formats.split(",") if f.strip()]
    bad = [f for f in fmts if f not in synth.FORMATS]
    if bad or not fmts:
        p.error(f"unknown audio format(s): {', '.join(bad) or '(none)'}; "
                f"choose from {', '.join(synth.FORMATS)}")
    a.formats = list(dict.fromkeys(fmts))
    if a.sheets:
        try:
            a.sheets = omr.parse_sheets(a.sheets)
        except ValueError as e:
            p.error(f"--sheets: {e}")
    if a.serve is not None and not 1 <= a.serve <= 65535:
        p.error("--serve: port must be between 1 and 65535")
    return a


class _Log:
    def __init__(self, quiet: bool):
        self.quiet = quiet
        self.t0 = time.monotonic()

    def step(self, msg: str) -> None:
        if not self.quiet:
            print(f"[{time.monotonic() - self.t0:5.1f}s] {msg}", file=sys.stderr, flush=True)


def _same(a: Path, b: Path) -> bool:
    """Same file on disk (also on case-insensitive file systems)."""
    try:
        return a.exists() and b.exists() and os.path.samefile(a, b)
    except OSError:
        return False


def _sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _read_report(folder: Path) -> dict:
    try:
        rep = json.loads((folder / "report.json").read_text())
        return rep if isinstance(rep, dict) else {}
    except (OSError, ValueError):
        return {}


def _book_run_dir(src: Path) -> Path | None:
    """The folder of the earlier sheet2audio run that wrote this Audiveris
    book (<name>_sheet2audio/omr/score.omr), if it is one."""
    if src.suffix.lower() != omr.BOOK_SUFFIX or src.parent.name != "omr":
        return None
    run_dir = src.parent.parent
    rep = _read_report(run_dir)
    book = rep.get("audiveris_book")
    if isinstance(rep.get("stem"), str) and isinstance(book, str) and _same(Path(book), src):
        return run_dir
    return None


def _default_outdir(src: Path) -> Path:
    return _book_run_dir(src) or src.with_name(f"{src.stem}_sheet2audio")


def _stem_for(src: Path) -> str:
    """Output file names. A book from an earlier run keeps that run's names."""
    run_dir = _book_run_dir(src)
    if run_dir is not None:
        stem = _read_report(run_dir)["stem"]
        if stem and "/" not in stem and "\\" not in stem:
            return stem
    return src.stem


def _clean_stale(outdir: Path, keep: Path, will_omr: bool, notes: list[str]) -> None:
    """Remove what the previous run into this folder produced (as listed in
    its report.json), so old files cannot be mistaken for this run's results.
    Never removes `keep` (the input), nor anything the report does not list,
    nor an Audiveris book that was changed after it was written."""
    prev = _read_report(outdir)
    doomed: list[Path] = [outdir / "report.json"]
    for key in ("midi", "viewer", "video", "audiveris_log"):
        if isinstance(prev.get(key), str):
            doomed.append(Path(prev[key]))
    audio = prev.get("audio")
    if isinstance(audio, dict):
        doomed += [Path(v) for v in audio.values() if isinstance(v, str)]
    if isinstance(prev.get("musicxml"), list):
        doomed += [Path(v) for v in prev["musicxml"] if isinstance(v, str)]
    if will_omr:  # run_audiveris writes these afresh
        doomed += list((outdir / "omr").glob("score*.mxl")) + [outdir / "omr" / "audiveris.log"]
        book = outdir / "omr" / "score.omr"
        if book.is_file() and not _same(book, keep):
            recorded = prev.get("audiveris_book_sha256")
            if recorded and _same(Path(prev.get("audiveris_book", "")), book) \
                    and recorded == _sha256(book):
                doomed.append(book)
            else:
                kept = book.with_name(f"score.{time.strftime('%Y%m%d-%H%M%S')}.omr")
                book.rename(kept)
                notes.append(f"The earlier Audiveris book had been changed, so it was kept as "
                             f"omr/{kept.name}.")
    root = outdir.resolve()
    for p in doomed:
        try:
            if p.is_file() and root in p.resolve().parents and not _same(p, keep):
                p.unlink()
        except OSError:
            pass


def run(argv: list[str] | None = None) -> dict:
    a = _parse_args(argv)
    log = _Log(a.quiet)
    src = a.input.expanduser().absolute()  # not resolve(): keep a symlink's own name
    if src.is_dir():
        raise UsageError(f"{a.input} is a folder; give a file.")
    if not src.is_file():
        raise UsageError(f"no such file: {a.input}")
    suffix = src.suffix.lower()
    if suffix not in INPUT_SUFFIXES:
        raise UsageError(
            f"don't know how to read '{src.suffix or src.name}' files. Give a PDF, an image "
            "(PNG/JPEG/TIFF/BMP/GIF/WebP), MusicXML (.mxl/.musicxml/.xml) or an Audiveris .omr book.")
    if src.stat().st_size == 0:
        raise UsageError(f"{a.input} is empty.")
    if a.sheets and suffix not in omr.PDF_SUFFIXES | omr.IMAGE_SUFFIXES:
        raise UsageError("--sheets only applies to PDF and image input.")

    outdir = (a.outdir.expanduser().absolute() if a.outdir else _default_outdir(src))
    if outdir.exists() and not outdir.is_dir():
        raise UsageError(f"the output path {outdir} exists and is not a folder.")
    stem = _stem_for(src)

    # Resolve every tool before any slow work, so a missing one fails fast.
    fluidsynth = tools.find_fluidsynth()
    ffmpeg = tools.find_ffmpeg()
    soundfont = tools.find_soundfont(a.soundfont)
    codecs = synth.plan_codecs(ffmpeg, a.formats + (["mp3"] if not a.no_viewer else []))
    audiveris = tools.find_audiveris(a.audiveris) if suffix not in omr.MUSICXML_SUFFIXES else None
    notes: list[str] = []
    rsvg = None if a.no_video else tools.find_rsvg()
    if not a.no_video and rsvg is None:
        notes.append("No MP4 video: install librsvg (brew install librsvg) to get one.")
    elif rsvg is not None and not {"libx264", "aac"} <= synth.encoders(ffmpeg):
        rsvg = None
        notes.append("No MP4 video: this FFmpeg has no H.264 (libx264) or AAC encoder.")

    try:
        outdir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise UsageError(f"cannot create the output folder {outdir}: {e.strerror}") from None
    _clean_stale(outdir, src, audiveris is not None, notes)
    report: dict = {"input": str(src), "outdir": str(outdir), "stem": stem,
                    "soundfont": str(soundfont), "status": "running"}
    _write_report(outdir, report)
    try:
        _run(a, log, src, suffix, outdir, stem, fluidsynth, ffmpeg, soundfont, codecs,
             audiveris, rsvg, notes, report)
    except BaseException as e:
        interrupted = isinstance(e, (KeyboardInterrupt, SystemExit))
        report["status"] = "interrupted" if interrupted else "failed"
        if not interrupted:
            report["error"] = str(e)
        report["notes"] = notes
        _write_report(outdir, report)
        raise
    return report


def _run(a, log, src, suffix, outdir, stem, fluidsynth, ffmpeg, soundfont, codecs, audiveris,
         rsvg, notes, report) -> None:
    # 1. Optical music recognition
    pages = None
    if audiveris is None:
        movement_files = [src]
        log.step(f"Reading MusicXML {src.name}")
    else:
        log.step(f"Recognising the music with Audiveris ({src.name}); about 5-15 s per page")
        res = omr.run_audiveris(audiveris, src, outdir, sheets=a.sheets)
        movement_files = res.movements
        pages = res.pages
        notes += res.notes
        report["audiveris_log"] = str(res.log)
        book = res.book or (src if suffix == omr.BOOK_SUFFIX else None)
        if book:
            report["audiveris_book"] = str(book)
            report["audiveris_book_sha256"] = _sha256(book)
        report["audiveris_warnings"] = omr.log_warnings(res.log)

    # 2. Clean up, split merged pieces
    roots, file_notes = [], []
    for mf in movement_files:
        root = musicxml.read_musicxml(mf)
        musicxml.tag_measures(root)
        sn = musicxml.sanitize(root, src.name)
        if a.time and musicxml.set_time(root, *a.time):
            notes.append(f"No time signature was read; using {a.time[0]}/{a.time[1]} as asked.")
        pieces = musicxml.split_movements(root)
        if len(pieces) > 1:
            notes.append(f"Found {len(pieces)} separate pieces on the same page(s); each gets its "
                         "own tempo, with a short pause between them.")
        for piece in pieces:
            sn += musicxml.close_octave_lines(piece)
        file_notes.append((sn, len(roots), pieces))
        roots += pieces
    multi = len(roots) > 1
    titles = []
    for i, root in enumerate(roots, 1):
        t = musicxml.title_of(root) if multi else None
        titles.append(t if t and t not in titles else (f"Movement {i}" if multi else stem))
    for sn, first, pieces in file_notes:
        notes += musicxml.resolve_notes(sn, pieces, titles[first:first + len(pieces)])
    for root in roots:
        musicxml.untag(root)

    # 3. Repair and engrave each movement (in child processes)
    log.step(f"Engraving {len(roots)} movement(s) with Verovio")
    jobs = [MovementJob(xml=musicxml.to_bytes(r), title=t, repair=not a.no_repair, bpm=a.bpm,
                        tempo_scale=a.tempo_scale, layout=a.layout, video=rsvg is not None)
            for r, t in zip(roots, titles)]
    results = process_movements(jobs)
    rendered = [r.rendered for r in results]
    xml_paths = []
    for i, (res_i, title) in enumerate(zip(results, titles), 1):
        prefix = f"{title}: " if multi else ""

        def note(text: str) -> str:
            return prefix + text if prefix else text[:1].upper() + text[1:]

        notes += [note(n) for n in res_i.notes + res_i.rendered.warnings]
        name = f"{stem}.mvt{i}.musicxml" if multi else f"{stem}.musicxml"
        path = outdir / name
        if _same(path, src):
            path = outdir / (name[: -len(".musicxml")] + ".fixed.musicxml")
        path.write_bytes(res_i.xml)
        xml_paths.append(path)
        r = res_i.rendered
        log.step(f"{title}: {r.note_count} notes, {len(r.svgs)} page(s), {r.duration_s:.1f} s "
                 f"at {r.bpm:g} BPM")
        if r.note_count == 0:
            notes.append(note("no notes were recognised."))
        if not r.has_tempo and a.bpm is None:
            notes.append(note(f"no tempo mark was read, so it plays at {r.bpm:g} BPM; "
                              "use --bpm to change it."))
    report["musicxml"] = [str(p) for p in xml_paths]
    if sum(r.note_count for r in rendered) == 0:
        hint = (f", or open {outdir / 'omr' / 'score.omr'} in Audiveris to see what it found"
                if audiveris is not None else "")
        raise UsageError("no notes were recognised. If this is a scan, try a cleaner or "
                         f"higher-resolution one (300 dpi){hint}.")
    measures = sum(len(musicxml.read_musicxml(p).find("part").findall("measure")) for p in xml_paths)
    if pages and measures < 3 * pages:
        notes.append(f"Only {measures} bars were recognised on {pages} page(s); parts of the "
                     "music may have been missed (a cleaner or 300 dpi scan may help).")

    # 4. One MIDI for the whole piece; movements follow each other with a short gap.
    offsets, t = [], 0.0
    for r in rendered:
        offsets.append(t)
        t += max(r.duration_s, r.ring_s) + MOVEMENT_GAP_S
    end_s = offsets[-1] + max(rendered[-1].duration_s, rendered[-1].ring_s) + TAIL_S
    midi_path = outdir / f"{stem}.mid"
    midi_path.write_bytes(combine_midi([(r.midi, off, r.tempo_factor)
                                        for r, off in zip(rendered, offsets)]))
    report["midi"] = str(midi_path)
    report["movements"] = [
        {"title": r.title, "notes": r.note_count, "pages": len(r.svgs),
         "duration_s": round(r.duration_s, 3), "offset_s": round(off, 3),
         "start_bpm": round(r.bpm, 3), "tempo_mark_read": r.has_tempo}
        for r, off in zip(rendered, offsets)]

    # 5. Audio, video, play-along page
    log.step(f"Synthesizing audio with FluidSynth ({soundfont.name})")
    with tempfile.TemporaryDirectory(prefix="sheet2audio-") as tmp:
        raw = Path(tmp) / "raw.wav"
        synth.render_wav(fluidsynth, soundfont, midi_path, raw)
        outputs = {fmt: outdir / f"{stem}.{fmt}" for fmt in a.formats}
        report["audio"] = {k: str(v) for k, v in outputs.items()}
        viewer_audio = outputs.get("mp3")
        if not a.no_viewer and viewer_audio is None:
            viewer_audio = Path(tmp) / f"{stem}.mp3"
        gain = synth.encode(ffmpeg, raw, {**outputs, **({"mp3": viewer_audio} if viewer_audio else {})},
                            codecs, end_s)
        if rsvg is not None:
            log.step("Drawing the score video")
            mp4 = outdir / f"{stem}.mp4"
            try:
                render_video([VideoMovement(r.svgs_video, r.timemap, off)
                              for r, off in zip(rendered, offsets)],
                             raw, synth.audio_filter(gain, end_s), mp4, ffmpeg, rsvg, end_s)
                report["video"] = str(mp4)
            except VideoError as e:
                notes.append(f"No MP4 video: {str(e).splitlines()[0]}")
        if not a.no_viewer:
            html_path = outdir / f"{stem}.html"
            link = a.link_audio and "mp3" in outputs
            if a.link_audio and not link:
                notes.append("--link-audio needs mp3 in --formats; the audio was embedded instead.")
            write_viewer(html_path, stem, list(zip(rendered, offsets)), viewer_audio, notes,
                         embed_audio=not link)
            report["viewer"] = str(html_path)

    report["notes"] = notes
    report["status"] = "done"
    _write_report(outdir, report)

    if not a.quiet:
        _print_summary(report, notes, xml_paths)
    if a.open and "viewer" in report:
        _launch(["open" if sys.platform == "darwin" else "xdg-open", report["viewer"]], "the page")
    if a.musescore:
        ms = tools.find_musescore()
        if ms is None:
            print("MuseScore was not found (free download: https://musescore.org).", file=sys.stderr)
        else:
            _launch([str(ms), *map(str, xml_paths)], "MuseScore")
    if a.serve is not None:
        _serve(outdir, a.serve, Path(report.get("viewer", "")).name,
               Path(report.get("video", "")).name)


def _write_report(outdir: Path, report: dict) -> None:
    (outdir / "report.json").write_text(json.dumps(report, indent=2, allow_nan=False))


def _print_summary(report: dict, notes: list[str], xml_paths: list[Path]) -> None:
    print(f"\nDone. Output folder: {report['outdir']}")
    rows = [("viewer", report.get("viewer")), ("video", report.get("video")),
            ("midi", report.get("midi"))] + list(report["audio"].items())
    for key, path in rows:
        if path:
            print(f"  {key:8s} {Path(path).name}")
    if "video" in report:
        print("\n  The .mp4 plays on an iPhone/iPad in Photos or Files (AirDrop it over).")
    if notes:
        print("\nThings to check (OMR is not perfect):")
        for n in notes:
            print(f"  - {n}")
    if "audiveris_book" in report:
        book = shlex.quote(report["audiveris_book"])
        print(f"\nTo fix recognition mistakes: open {book} in Audiveris, correct it, save, then run\n"
              f"  sheet2audio {book}\n"
              f"or fix {shlex.quote(xml_paths[0].name)} in MuseScore and run sheet2audio on it.")


def _launch(cmd: list[str], what: str) -> None:
    try:
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as e:
        print(f"warning: could not open {what}: {e.strerror or e}", file=sys.stderr)


def _lan_address() -> str | None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))  # no packet is sent; this only picks the outgoing interface
            return s.getsockname()[0]
    except OSError:
        return None


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args) -> None:
        pass


class _QuietServer(http.server.ThreadingHTTPServer):
    def handle_error(self, request, client_address) -> None:
        pass  # a phone dropping a connection is not worth a traceback


def _serve(outdir: Path, port: int, page: str, video: str) -> None:
    handler = functools.partial(_QuietHandler, directory=str(outdir))
    try:
        httpd = _QuietServer(("0.0.0.0", port), handler)
    except OSError as e:
        raise UsageError(f"cannot share on port {port}: {e.strerror}. Try --serve 8080.") from None
    ip = _lan_address()
    host = socket.gethostname()
    page_q, video_q = urllib.parse.quote(page), urllib.parse.quote(video)
    print(f"\nSharing {outdir} on your local network (Ctrl-C to stop).")
    print("Anyone on this Wi-Fi can open these files while this runs. On your phone, open:")
    for h in dict.fromkeys(x for x in (ip, host if host.endswith(".local") else f"{host}.local") if x):
        print(f"  http://{h}:{port}/{page_q}"
              + (f"   (video: http://{h}:{port}/{video_q})" if video else ""))
    sys.stdout.flush()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


def _on_signal(signum, frame):
    raise SystemExit(128 + signum)


def main(argv: list[str] | None = None) -> None:
    # Turn termination signals into exceptions, so temporary folders get cleaned up.
    for sig in (signal.SIGTERM, getattr(signal, "SIGHUP", None)):
        if sig is not None:
            signal.signal(sig, _on_signal)
    debug = "--debug" in (argv if argv is not None else sys.argv[1:])
    try:
        run(argv)
    except (UsageError, tools.MissingTool, omr.OMRError, synth.SynthError, RenderError,
            musicxml.MusicXMLError, VideoError) as e:
        if debug:
            raise
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as e:  # noqa: BLE001 - last resort: no raw traceback for users
        if debug:
            raise
        print(f"error: unexpected {type(e).__name__}: {e}\n(run again with --debug for details)",
              file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
