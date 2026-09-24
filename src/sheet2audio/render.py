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
import signal
import xml.etree.ElementTree as ET
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

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
class TrackAudio:
    """One part (e.g. Alto, or Piano) of a rendered movement."""

    name: str
    kind: str  # "voice", "accompaniment" or "instrument"
    program: int  # General MIDI program written in the score
    ids: list[str]  # drawn notes (SVG data-id) it plays
    midi: bytes  # this part alone, at the score tempo (apply tempo_factor)


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
    ring_s: float  # when the last sound stops: later than duration_s if the pedal holds it
    note_count: int
    warnings: list[str] = field(default_factory=list)
    tracks: list[TrackAudio] = field(default_factory=list)
    xml: bytes = b""  # the MusicXML as engraved (staves named after the parts found)

    @property
    def bpm(self) -> float:
        return self.base_tempo * self.tempo_factor


_RESOURCES = str(Path(verovio.__file__).with_name("data"))


def _toolkit(options: dict, text: str) -> verovio.toolkit:
    verovio.enableLog(verovio.LOG_OFF)
    tk = verovio.toolkit()
    # Outside the main thread the binding's default resource path is a stale
    # build-time path, and every load fails; set it explicitly.
    tk.setResourcePath(_RESOURCES)
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


def _last_note_on(data: bytes) -> float:
    t = last = 0.0
    for msg in mido.MidiFile(file=io.BytesIO(data)):
        t += msg.time
        if msg.type == "note_on" and msg.velocity > 0:
            last = t
    return last


PEDAL_RING_MAX_S = 6.0


def _midi_extent(data: bytes) -> tuple[float, float, int]:
    """(last note-off, end of ringing, number of notes), in seconds at the
    score tempo. A sustain pedal still down at the last note-off keeps the
    sound going until it is lifted (at most PEDAL_RING_MAX_S longer)."""
    t = last_off = 0.0
    notes = 0
    pedal: dict[int, bool] = {}
    pedal_at_end: set[int] = set()
    lifted: dict[int, float] = {}
    for msg in mido.MidiFile(file=io.BytesIO(data)):
        t += msg.time
        if msg.type == "note_on" and msg.velocity > 0:
            notes += 1
            last_off = max(last_off, t)
        elif msg.type in ("note_off", "note_on"):
            if t >= last_off:
                last_off = t
                pedal_at_end = {c for c, down in pedal.items() if down}
                lifted = {}
        elif msg.type == "control_change" and msg.control == 64:
            down = msg.value >= 64
            if pedal.get(msg.channel) and not down and msg.channel in pedal_at_end:
                lifted.setdefault(msg.channel, t)
            pedal[msg.channel] = down
    ring = last_off
    for c in pedal_at_end:
        ring = max(ring, min(lifted.get(c, t), last_off + PEDAL_RING_MAX_S))
    return last_off, ring, notes


def render_musicxml(xml: bytes, title: str, bpm: float | None = None, tempo_scale: float = 1.0,
                    layout: str = "encoded", video: bool = False,
                    parts_names: list[str] | bool | None = False, hands: bool = True) -> Rendered:
    """Engrave and time `xml`. With `bpm`, the score's first tempo becomes
    `bpm` and later tempo changes keep their ratio; otherwise every tempo is
    multiplied by `tempo_scale`. The tempo is applied here, not by Verovio,
    whose own adjustment silently ignores factors outside 0.2-4."""
    text = xml.decode("utf-8")
    warnings: list[str] = []
    tk = _toolkit(_options(DESKTOP, layout), text)
    part_entries: list[str] | None = None
    if parts_names is not False:
        from . import parts as _parts

        plans = _parts.plan_groups(ET.fromstring(tk.getMEI()), parts_names or None, warnings)
        part_entries = [p.entry for p in plans]
        relabeled = _label_parts(text, plans)
        if relabeled != text:
            text = relabeled
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

    tracks = []
    if parts_names is not False:
        tracks = _tracks(tk.getMEI(), part_entries, hands, warnings)
    duration, ring, notes = _midi_extent(midi)
    # The last note starts at the same moment in the MIDI and in the timemap,
    # unless something (e.g. an absurd tempo) broke the MIDI timing.
    tm_last = max((e["tstamp"] for e in raw_timemap if e.get("on")), default=0.0) / 1000.0
    midi_last = _last_note_on(midi)
    if notes and abs(tm_last - midi_last) > max(0.05, 0.01 * midi_last):
        warnings.append(f"The audio and the note highlighting disagree ({midi_last:.1f} s vs "
                        f"{tm_last:.1f} s for the last note); a tempo mark may have been misread. "
                        "Try --bpm.")
    return Rendered(
        title=title, svgs=svgs, svgs_narrow=svgs_narrow, svgs_video=svgs_video, midi=midi,
        timemap=timemap, base_tempo=base_tempo, tempo_factor=factor,
        has_tempo=musicxml.has_tempo_mark(xml), duration_s=duration / factor,
        ring_s=ring / factor, note_count=notes, warnings=warnings, tracks=tracks,
        xml=text.encode("utf-8"),
    )


def _label_parts(text: str, plans) -> str:
    """Name the staves after the parts found (e.g. 'Soprano/Alto' instead of
    the 'Voice' OMR wrote), where the score's own names are generic."""
    from . import parts

    root = ET.fromstring(text.encode("utf-8"))
    score_parts = [root.find(f".//score-part[@id='{p.get('id')}']") for p in root.findall("part")]
    changed = False
    for sp, plan in zip(score_parts, plans):
        if sp is None or plan.kind != "voice":
            continue
        current = (sp.findtext("part-name") or "").strip()
        if current and not parts.is_generic_name(current):
            continue
        label = "/".join(n for s in plan.group.staves for n in plan.names.get(s, []))
        if not label:
            continue
        for tag, value in (("part-name", label),
                           ("part-abbreviation", "/".join(x[:1] for x in label.split("/")))):
            el = sp.find(tag)
            if el is None:
                el = ET.SubElement(sp, tag)
            el.text = value
        changed = True
    if not changed:
        return text
    return ET.tostring(root, encoding="unicode", xml_declaration=True)


def _tracks(mei: str, names: list[str] | None, hands: bool,
            warnings: list[str]) -> list[TrackAudio]:
    """Each part's own MIDI, rendered from the MEI with the other parts' notes
    replaced by silence (so all parts share the score's time line)."""
    from . import parts

    out = []
    for t in parts.find_tracks(mei, names, hands=hands):
        tk = _toolkit(_options(DESKTOP, "auto"), parts.isolate(mei, t))
        out.append(TrackAudio(t.name, t.kind, t.program, sorted(t.ids),
                              base64.b64decode(tk.renderToMIDI())))
    return out


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
    parts: list[str] | None = None  # part names, one entry per staff group
    hands: bool = True  # a piano-only piece may be split into right and left hand


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
                        layout=job.layout, video=job.video, parts_names=job.parts or None,
                        hands=job.hands)
    # After rendering, so the parts carry the names found for them.
    gaps = musicxml.staff_gaps(musicxml.parse(r.xml or xml, job.title))
    return MovementResult(xml=r.xml or xml, rendered=r, notes=report.notes + gaps)


def _ignore_sigint() -> None:
    signal.signal(signal.SIGINT, signal.SIG_IGN)


def describe_parts(xml: bytes) -> list[tuple[str, str, bool]]:
    """(--parts entry, kind, has notes) for each staff group of a movement."""
    from . import parts

    tk = _toolkit(_options(DESKTOP, "auto"), xml.decode("utf-8"))
    return parts.describe_groups(tk.getMEI())


def in_children(fn, args: list, what: str = "reading the score") -> list:
    """Run fn over args in child processes (Verovio crashes cannot kill us)."""
    ctx = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(max_workers=max(1, min(4, len(args))), mp_context=ctx,
                             initializer=_ignore_sigint) as pool:
        old = signal.signal(signal.SIGINT, signal.SIG_IGN)
        try:
            futures = [pool.submit(fn, a) for a in args]
        finally:
            signal.signal(signal.SIGINT, old)
        try:
            return [f.result() for f in futures]
        except BrokenProcessPool:
            raise RenderError(f"The engraving step stopped unexpectedly while {what} (Verovio "
                              "probably crashed on something in the MusicXML).") from None


def process_movements(jobs: list[MovementJob], workers: int = 4) -> list[MovementResult]:
    """Run `process_movement` for each job in child processes. Ctrl-C is left
    to the parent: workers ignore it (they are started while it is ignored,
    so this holds even before they finish importing)."""
    ctx = multiprocessing.get_context("spawn")
    results: list[MovementResult] = []
    with ProcessPoolExecutor(max_workers=max(1, min(workers, len(jobs))), mp_context=ctx,
                             initializer=_ignore_sigint) as pool:
        old = signal.signal(signal.SIGINT, signal.SIG_IGN)
        try:
            futures = [pool.submit(process_movement, j) for j in jobs]  # starts the workers
        finally:
            signal.signal(signal.SIGINT, old)
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


def _events(data: bytes, offset: float, factor: float):
    """(absolute seconds, message) for every channel message of a MIDI file."""
    t = 0.0
    for msg in mido.MidiFile(file=io.BytesIO(data)):
        t += msg.time
        if not msg.is_meta and msg.type != "sysex":
            yield offset + t / factor, msg


def _write(events: list[tuple[int, int, int, mido.Message]], tail_s: float) -> bytes:
    """Serialise (tick, order, seq, message) events on a fixed 120 BPM grid.

    Where two voices play the same key at once, the key is struck again and
    released only when the last of them ends (a synthesizer releases every
    voice on a key at its first note-off)."""
    tpb = 960
    events.sort(key=lambda e: e[:3])
    out = mido.MidiFile(type=0, ticks_per_beat=tpb)
    track = mido.MidiTrack()
    out.tracks.append(track)
    track.append(mido.MetaMessage("set_tempo", tempo=500000, time=0))
    now = 0
    held: dict[tuple[int, int], int] = {}

    def emit(tick: int, msg: mido.Message) -> None:
        nonlocal now
        track.append(msg.copy(time=tick - now))
        now = tick

    for tick, _, _, msg in events:
        if msg.type in ("note_on", "note_off"):
            key = (msg.channel, msg.note)
            if msg.type == "note_on" and msg.velocity > 0:
                if held.get(key, 0) > 0:
                    emit(tick, mido.Message("note_off", channel=msg.channel, note=msg.note))
                held[key] = held.get(key, 0) + 1
            else:
                held[key] = max(0, held.get(key, 0) - 1)
                if held[key] > 0:
                    continue
        elif msg.type == "control_change" and msg.control == 123:
            held = {k: (0 if k[0] == msg.channel else v) for k, v in held.items()}
        emit(tick, msg)
    track.append(mido.MetaMessage("end_of_track", time=int(tail_s * TICKS_PER_S)))
    buf = io.BytesIO()
    out.save(file=buf)
    return buf.getvalue()


TICKS_PER_S = 1920  # 960 ticks per quarter at 120 BPM


def _is_off(msg: mido.Message) -> bool:
    return msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0)


def combine_midi(parts: list[tuple[bytes, float, float]], tail_s: float = 2.0) -> bytes:
    """Concatenate MIDI files: (data, start offset in seconds, tempo factor).

    Every event is re-timed on a fixed 120 BPM grid after dividing its time by
    the tempo factor, so the tempo maps of the sources are baked in. Before
    each next movement the sustain pedal is lifted and sounding notes are
    stopped. An end-of-track `tail_s` after the last event lets notes finish.
    """
    events: list[tuple[int, int, int, mido.Message]] = []
    seq = 0
    for k, (data, offset, factor) in enumerate(parts):
        channels = set()
        for t, msg in _events(data, offset, factor):
            seq += 1
            channels.add(getattr(msg, "channel", 0))
            events.append((int(round(t * TICKS_PER_S)), 0 if _is_off(msg) else 1, seq, msg))
        if k + 1 < len(parts):
            release = int(round((parts[k + 1][1] - 0.01) * TICKS_PER_S))
            for ch in sorted(channels):
                for ctl in (64, 123):  # pedal up, all notes off
                    seq += 1
                    events.append((release, 0, seq, mido.Message("control_change", channel=ch,
                                                                 control=ctl, value=0)))
    return _write(events, tail_s)


_CHANNELS = [c for c in range(16) if c != 9]  # channel 10 (index 9) is General MIDI drums


def track_names(movements: list[Rendered]) -> list[str]:
    """Every part name, in order of first appearance across movements."""
    return list(dict.fromkeys(t.name for r in movements for t in r.tracks))


def mix_tracks(movements: list[tuple[Rendered, float]], programs: dict[str, int],
               gains: dict[str, float], tail_s: float = 2.0) -> bytes:
    """One MIDI file with every part on its own channel, with the given General
    MIDI program and volume (0-1, as amplitude; 0 leaves the part out)."""
    names = [n for n in track_names([r for r, _ in movements]) if gains.get(n, 1.0) > 0]
    if len(names) <= len(_CHANNELS):
        channel = {n: _CHANNELS[i] for i, n in enumerate(names)}
    else:
        # More parts than MIDI channels: parts that sound the same (program
        # and volume) share a channel; nothing else ever does unless forced.
        keys = list(dict.fromkeys((programs.get(n, 0), gains.get(n, 1.0)) for n in names))
        channel = {n: _CHANNELS[keys.index((programs.get(n, 0), gains.get(n, 1.0)))
                                % len(_CHANNELS)] for n in names}
    events: list[tuple[int, int, int, mido.Message]] = []
    seq = 0
    for n, ch in dict(reversed(list(channel.items()))).items():  # first part wins a shared channel
        # MIDI volume is a power curve: amplitude g -> value 127 * sqrt(g).
        vol = max(0, min(127, round(127 * gains.get(n, 1.0) ** 0.5)))
        for msg in (mido.Message("program_change", channel=ch, program=programs.get(n, 0)),
                    mido.Message("control_change", channel=ch, control=7, value=vol),
                    mido.Message("control_change", channel=ch, control=10, value=64)):
            seq += 1
            events.append((0, -1, seq, msg))
    for k, (r, offset) in enumerate(movements):
        for tr in r.tracks:
            if tr.name not in channel:
                continue
            ch = channel[tr.name]
            for t, msg in _events(tr.midi, offset, r.tempo_factor):
                if msg.type in ("note_on", "note_off") or (
                        msg.type == "control_change" and msg.control in (64, 66, 67)):
                    seq += 1
                    events.append((int(round(t * TICKS_PER_S)), 0 if _is_off(msg) else 1, seq,
                                   msg.copy(channel=ch)))
        if k + 1 < len(movements):
            release = int(round((movements[k + 1][1] - 0.01) * TICKS_PER_S))
            for ch in channel.values():
                for ctl in (64, 123):
                    seq += 1
                    events.append((release, 0, seq, mido.Message("control_change", channel=ch,
                                                                 control=ctl, value=0)))
    return _write(events, tail_s)


def track_notes(movements: list[tuple[Rendered, float]]) -> dict[str, list[list[float]]]:
    """Per part, its notes as [start ms, release ms, MIDI pitch, velocity] on
    the final time line. A note held by the sustain pedal is released when the
    pedal is lifted (at most PEDAL_RING_MAX_S later)."""
    out: dict[str, list[list[float]]] = {}
    for r, offset in movements:
        for tr in r.tracks:
            notes = out.setdefault(tr.name, [])
            sounding: dict[int, list[tuple[float, int]]] = {}
            pedal_down = False
            held: list[list[float]] = []
            for t, msg in _events(tr.midi, offset, r.tempo_factor):
                if msg.type == "note_on" and msg.velocity > 0:
                    sounding.setdefault(msg.note, []).append((t, msg.velocity))
                elif _is_off(msg) and sounding.get(msg.note):
                    start, vel = sounding[msg.note].pop(0)
                    n = [round(start * 1000, 1), round(t * 1000, 1), msg.note, vel]
                    notes.append(n)
                    if pedal_down:
                        held.append(n)
                elif msg.type == "control_change" and msg.control == 64:
                    down = msg.value >= 64
                    if pedal_down and not down:
                        for n in held:
                            n[1] = round(min(t * 1000, n[1] + PEDAL_RING_MAX_S * 1000), 1)
                        held = []
                    pedal_down = down
            notes.sort()
    for name, notes in out.items():
        # The same pitch twice at the same moment in one part (e.g. a chord
        # that doubles a note) sounds once, as it does in the MIDI.
        merged: dict[tuple[float, float, int], list[float]] = {}
        for n in notes:
            key = (n[0], n[1], n[2])
            if key in merged:
                merged[key][3] = max(merged[key][3], n[3])
            else:
                merged[key] = n
        out[name] = sorted(merged.values())
    return out


def channels_needed(movements: list[Rendered], programs: dict[str, int]) -> int:
    """How many distinct MIDI channels the full mix would need."""
    names = track_names(movements)
    if len(names) <= len(_CHANNELS):
        return len(names)
    return len({programs.get(n, 0) for n in names})
