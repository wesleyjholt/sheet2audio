"""MusicXML -> engraved SVG pages, MIDI and a note timemap via Verovio (LGPL-3.0).

The SVG, the MIDI and the timemap all come from the same Verovio document, so
the note ids in the timemap are the ids in the SVG and its times match the
MIDI we synthesize. That keeps the viewer's highlighting in sync with the
audio. Every movement is processed in a child process (`process_movement`):
Verovio is native code, and a crash there must not take the whole run down.
"""

from __future__ import annotations

import base64
import io
import multiprocessing
import re
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass, field
from fractions import Fraction

import mido
import verovio

from . import musicxml

DEFAULT_TEMPO = 120.0  # Verovio's tempo when the score has none

# Layouts, in Verovio units (1/10 mm at scale 100).
DESKTOP = {"pageWidth": 2100, "pageHeight": 2970, "adjustPageHeight": True}
NARROW = {"pageWidth": 900, "pageHeight": 2970, "adjustPageHeight": True, "breaks": "auto"}
VIDEO = {"pageWidth": 1920, "pageHeight": 1080, "adjustPageHeight": False, "breaks": "auto",
         "pageMarginTop": 40, "pageMarginBottom": 20, "pageMarginLeft": 50, "pageMarginRight": 50}


class RenderError(RuntimeError):
    pass


@dataclass
class Rendered:
    title: str
    svgs: list[str]  # page layout (desktop)
    svgs_narrow: list[str]  # re-flowed for phones
    svgs_video: list[str]  # 16:9 screens for the MP4
    midi: bytes  # as Verovio wrote it (score tempo); combine_midi applies tempo_factor
    timemap: list[dict]  # tstamp in ms at the final tempo; ids present in the SVGs
    base_tempo: float  # the score's first tempo (quarter notes per minute)
    tempo_factor: float  # playback speed relative to the score
    has_tempo: bool  # the score states a tempo (else Verovio's default is used)
    duration_s: float  # musical end (last note-off) at the final tempo
    note_count: int
    warnings: list[str] = field(default_factory=list)

    @property
    def bpm(self) -> float:
        return self.base_tempo * self.tempo_factor


def _toolkit(options: dict, text: str) -> verovio.toolkit:
    verovio.enableLog(verovio.LOG_OFF)
    tk = verovio.toolkit()
    tk.setOptions(options)
    if not tk.loadData(text):
        raise RenderError("Verovio could not read the MusicXML.")
    return tk


def _options(layout: dict, breaks: str = "encoded") -> dict:
    opts = {
        "breaks": breaks,  # "encoded" keeps the line/page breaks OMR found in the PDF
        "svgViewBox": True,
        "pageMarginLeft": 60,
        "pageMarginRight": 60,
        "pageMarginTop": 60,
        "pageMarginBottom": 60,
        "scale": 100,
        "footer": "none",
        "svgHtml5": True,  # ids as data-id attributes, usable from JavaScript
        "xmlIdSeed": 1,  # deterministic ids, identical across layouts
    }
    opts.update(layout)
    return opts


# ---------------------------------------------------------------- bar lengths


_MEI_MEASURE = re.compile(r'<measure\b[^>]*\bxml:id="([^"]+)"')


def heard_measure_lengths(xml: bytes) -> list[Fraction]:
    """Length of every measure in quarter notes, as Verovio plays it (repeats
    not expanded), in document order."""
    opts = _options({"pageWidth": 2100, "pageHeight": 2970}, "auto") | {"expandNever": True}
    tk = _toolkit(opts, xml.decode("utf-8"))
    timemap = tk.renderToTimemap({"includeMeasures": True, "includeRests": True})
    ids = _MEI_MEASURE.findall(tk.getMEI())
    if not ids:
        return []
    start: dict[str, Fraction] = {}
    end = Fraction(0)
    for e in timemap:
        q = Fraction(e.get("qstamp", 0)).limit_denominator(3840)
        end = max(end, q)
        if "measureOn" in e and e["measureOn"] not in start:
            start[e["measureOn"]] = q
    # A measure with no events (length 0) has no measureOn: it starts where the next one does.
    qs: list[Fraction | None] = [start.get(i) for i in ids]
    nxt = end
    for k in range(len(qs) - 1, -1, -1):
        if qs[k] is None:
            qs[k] = nxt
        nxt = qs[k]
    return [(qs[k + 1] if k + 1 < len(qs) else end) - qs[k] for k in range(len(qs))]


def _heard_from_root(root) -> list[Fraction]:
    return heard_measure_lengths(musicxml.to_bytes(root))


# ---------------------------------------------------------------- render


_DATA_ID = re.compile(r'data-id="([^"]+)"')
_REND = re.compile(r"(-rend\d+)+$")


def _normalize_ids(timemap: list[dict], svg_ids: set[str]) -> list[dict]:
    """Verovio plays repeats by expanding the score; notes on the second pass
    get ids like 'abc-rend2' that are not in the (unexpanded) SVG. Map them back
    to the drawn note so the viewer lights it on every pass."""

    def fix(i: str) -> str:
        if i in svg_ids:
            return i
        base = _REND.sub("", i)
        return base if base in svg_ids else i

    out = []
    for e in timemap:
        f = dict(e)
        for k in ("on", "off"):
            if k in f:
                f[k] = [fix(i) for i in f[k]]
        if "measureOn" in f:
            f["measureOn"] = fix(f["measureOn"])
        out.append(f)
    return out


def _midi_extent(data: bytes) -> tuple[float, int]:
    t = last_off = 0.0
    notes = 0
    for msg in mido.MidiFile(file=io.BytesIO(data)):
        t += msg.time
        if msg.type == "note_on" and msg.velocity > 0:
            notes += 1
            last_off = max(last_off, t)
        elif msg.type in ("note_off", "note_on"):
            last_off = max(last_off, t)
    return last_off, notes


def render_musicxml(xml: bytes, title: str, bpm: float | None = None, tempo_scale: float = 1.0,
                    layout: str = "encoded", video: bool = False) -> Rendered:
    """Engrave and time `xml`. With `bpm`, the score's first tempo becomes
    `bpm` and later tempo changes keep their ratio; otherwise every tempo is
    multiplied by `tempo_scale`. The tempo is applied here, not by Verovio,
    whose own adjustment silently ignores factors outside 0.2-4."""
    text = xml.decode("utf-8")
    warnings: list[str] = []
    tk = _toolkit(_options(DESKTOP, layout), text)
    if layout == "encoded" and tk.getPageCount() == 0:
        warnings.append("The page layout from the PDF could not be kept; the music was re-flowed.")
        tk = _toolkit(_options(DESKTOP, "auto"), text)

    svgs = [tk.renderToSVG(p) for p in range(1, tk.getPageCount() + 1)]
    midi = base64.b64decode(tk.renderToMIDI())
    raw_timemap = tk.renderToTimemap({"includeMeasures": True})
    base_tempo = next((float(e["tempo"]) for e in raw_timemap if "tempo" in e), DEFAULT_TEMPO)
    factor = bpm / base_tempo if bpm else tempo_scale

    svg_ids = {i for s in svgs for i in _DATA_ID.findall(s)}
    timemap = _normalize_ids(raw_timemap, svg_ids)
    for e in timemap:
        e["tstamp"] = e["tstamp"] / factor

    narrow_tk = _toolkit(_options(NARROW), text)
    svgs_narrow = [narrow_tk.renderToSVG(p) for p in range(1, narrow_tk.getPageCount() + 1)]
    svgs_video: list[str] = []
    if video:
        video_tk = _toolkit(_options(VIDEO), text)
        svgs_video = [video_tk.renderToSVG(p) for p in range(1, video_tk.getPageCount() + 1)]

    duration, notes = _midi_extent(midi)
    return Rendered(
        title=title, svgs=svgs, svgs_narrow=svgs_narrow, svgs_video=svgs_video, midi=midi,
        timemap=timemap, base_tempo=base_tempo, tempo_factor=factor,
        has_tempo=musicxml.has_tempo_mark(xml), duration_s=duration / factor,
        note_count=notes, warnings=warnings,
    )


# ---------------------------------------------------------------- per movement


@dataclass
class MovementJob:
    xml: bytes  # sanitized MusicXML
    title: str
    repair: bool
    bpm: float | None
    tempo_scale: float
    layout: str
    video: bool


@dataclass
class MovementResult:
    xml: bytes  # after repair
    rendered: Rendered
    notes: list[str]


def process_movement(job: MovementJob) -> MovementResult:
    root = musicxml.parse(job.xml, job.title)
    report = musicxml.repair(root, _heard_from_root, apply=job.repair)
    xml = musicxml.to_bytes(root)
    r = render_musicxml(xml, job.title, bpm=job.bpm, tempo_scale=job.tempo_scale,
                        layout=job.layout, video=job.video)
    return MovementResult(xml=xml, rendered=r, notes=report.notes)


def process_movements(jobs: list[MovementJob], workers: int = 4) -> list[MovementResult]:
    """Run `process_movement` for each job in child processes."""
    ctx = multiprocessing.get_context("spawn")
    results: list[MovementResult] = []
    with ProcessPoolExecutor(max_workers=max(1, min(workers, len(jobs))), mp_context=ctx) as pool:
        futures = [pool.submit(process_movement, j) for j in jobs]
        for job, fut in zip(jobs, futures):
            try:
                results.append(fut.result())
            except BrokenProcessPool:
                raise RenderError(
                    f"The engraving step stopped unexpectedly on '{job.title}' (Verovio probably "
                    "crashed on something in the MusicXML). Open the MusicXML in MuseScore, "
                    "re-save it, and run sheet2audio on the saved file.") from None
    return results


# ---------------------------------------------------------------- MIDI


def combine_midi(parts: list[tuple[bytes, float, float]], tail_s: float = 2.0) -> bytes:
    """Concatenate MIDI files: (data, start offset in seconds, tempo factor).

    Every event is re-timed on a fixed 120 BPM grid (1 tick = 1/1920 s) after
    dividing its time by the tempo factor, so the tempo maps of the sources
    are baked in. An end-of-track `tail_s` after the last event lets the
    synthesizer finish ringing notes.
    """
    tpb = 960
    ticks_per_s = tpb * 2  # 120 BPM
    events: list[tuple[int, int, int, mido.Message]] = []
    seq = 0
    for data, offset, factor in parts:
        t = 0.0
        for msg in mido.MidiFile(file=io.BytesIO(data)):
            t += msg.time
            if msg.is_meta or msg.type == "sysex":
                continue
            seq += 1
            # note-offs before note-ons on the same tick, so repeated notes retrigger
            is_off = msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0)
            tick = int(round((offset + t / factor) * ticks_per_s))
            events.append((tick, 0 if is_off else 1, seq, msg))
    events.sort(key=lambda e: e[:3])
    out = mido.MidiFile(type=0, ticks_per_beat=tpb)
    track = mido.MidiTrack()
    out.tracks.append(track)
    track.append(mido.MetaMessage("set_tempo", tempo=500000, time=0))
    now = 0
    for tick, _, _, msg in events:
        track.append(msg.copy(time=tick - now))
        now = tick
    track.append(mido.MetaMessage("end_of_track", time=int(tail_s * ticks_per_s)))
    buf = io.BytesIO()
    out.save(file=buf)
    return buf.getvalue()
