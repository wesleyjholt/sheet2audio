"""MusicXML -> engraved SVG pages, MIDI and a note timemap via Verovio (LGPL-3.0).

The SVG, the MIDI and the timemap all come from the same Verovio layout, so
the note ids the timemap lists are exactly the ids in the SVG and the times
match the MIDI we synthesize. That is what keeps the viewer's highlighting in
sync with the audio.
"""

from __future__ import annotations

import base64
import io
from dataclasses import dataclass, field

import mido
import verovio

DEFAULT_TEMPO = 120.0  # Verovio's default when the score has no tempo mark


@dataclass
class Rendered:
    title: str
    svgs: list[str]
    midi: bytes
    timemap: list[dict]
    base_tempo: float  # quarter-note BPM Verovio read from the score (first tempo)
    tempo_scale: float
    duration_s: float  # musical end (last note-off), from the MIDI
    note_count: int
    warnings: list[str] = field(default_factory=list)


def _toolkit(options: dict, text: str) -> verovio.toolkit:
    verovio.enableLog(verovio.LOG_OFF)
    tk = verovio.toolkit()
    tk.setOptions(options)
    if not tk.loadData(text):
        raise RuntimeError("Verovio could not read the MusicXML.")
    return tk


def _base_options(layout: str) -> dict:
    return {
        "breaks": layout,  # "encoded" keeps the line/page breaks OMR found in the PDF
        "svgViewBox": True,
        "adjustPageHeight": True,
        "pageWidth": 2100,
        "pageHeight": 2970,
        "pageMarginLeft": 60,
        "pageMarginRight": 60,
        "pageMarginTop": 60,
        "pageMarginBottom": 60,
        "scale": 100,
        "footer": "none",
        "svgHtml5": True,  # ids as plain `id` attributes, usable from JavaScript
        "xmlIdSeed": 1,  # deterministic ids
    }


def render_musicxml(
    xml: bytes,
    title: str,
    bpm: float | None = None,
    tempo_scale: float = 1.0,
    layout: str = "encoded",
) -> Rendered:
    opts = _base_options(layout)
    text = xml.decode("utf-8", errors="replace")
    tk = _toolkit(opts, text)
    warnings: list[str] = []
    if layout == "encoded" and tk.getPageCount() == 0:
        warnings.append("Encoded page layout failed; fell back to automatic layout.")
        opts["breaks"] = "auto"
        tk = _toolkit(opts, text)

    # Verovio caches its timemap, so a tempo change needs a fresh toolkit.
    probe = tk.renderToTimemap({})
    base_tempo = next((float(e["tempo"]) for e in probe if "tempo" in e), DEFAULT_TEMPO)
    scale = tempo_scale
    if bpm:
        scale = bpm / base_tempo
    if abs(scale - 1.0) > 1e-9:
        opts["midiTempoAdjustment"] = scale
        tk = _toolkit(opts, text)

    svgs = [tk.renderToSVG(p) for p in range(1, tk.getPageCount() + 1)]
    midi = base64.b64decode(tk.renderToMIDI())
    timemap = tk.renderToTimemap({"includeMeasures": True})
    mf = mido.MidiFile(file=io.BytesIO(midi))
    duration, notes = _midi_extent(mf)
    return Rendered(
        title=title,
        svgs=svgs,
        midi=midi,
        timemap=timemap,
        base_tempo=base_tempo,
        tempo_scale=scale,
        duration_s=duration,
        note_count=notes,
        warnings=warnings,
    )


def _midi_extent(mf: mido.MidiFile) -> tuple[float, int]:
    t = 0.0
    last_off = 0.0
    notes = 0
    for msg in mf:  # merged tracks, `time` = seconds since previous message
        t += msg.time
        if msg.type == "note_on" and msg.velocity > 0:
            notes += 1
            last_off = max(last_off, t)
        elif msg.type in ("note_off", "note_on"):
            last_off = max(last_off, t)
    return last_off, notes


def combine_midi(parts: list[tuple[bytes, float]], tail_s: float = 2.0) -> bytes:
    """Concatenate MIDI files, each starting at its given offset in seconds.

    Every event is re-timed on a fixed 120 BPM grid (1 tick = 1/1920 s), so the
    tempo maps of the sources are baked in and cannot interfere. A final
    end-of-track `tail_s` after the last event lets the synthesizer finish
    ringing notes.
    """
    tpb = 960
    ticks_per_s = tpb * 2  # 120 BPM
    events: list[tuple[int, int, int, mido.Message]] = []
    seq = 0
    for data, offset in parts:
        t = offset
        for msg in mido.MidiFile(file=io.BytesIO(data)):
            t += msg.time
            if msg.is_meta or msg.type == "sysex":
                continue
            seq += 1
            # note-offs before note-ons on the same tick, so repeated notes retrigger
            is_off = msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0)
            events.append((int(round(t * ticks_per_s)), 0 if is_off else 1, seq, msg))
    events.sort(key=lambda e: e[:3])
    out = mido.MidiFile(type=0, ticks_per_beat=tpb)
    track = mido.MidiTrack()
    out.tracks.append(track)
    track.append(mido.MetaMessage("set_tempo", tempo=500000, time=0))
    now_ticks = 0
    for ticks, _, _, msg in events:
        track.append(msg.copy(time=ticks - now_ticks))
        now_ticks = ticks
    track.append(mido.MetaMessage("end_of_track", time=int(tail_s * ticks_per_s)))
    buf = io.BytesIO()
    out.save(file=buf)
    return buf.getvalue()
