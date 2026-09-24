"""Choral fixtures engraved by Verovio and MuseScore instead of LilyPond.

    uv run python tests/fixtures/engravers/make.py [--only NAME] [--no-musescore]

Every other fixture is engraved by LilyPond, which Audiveris reads almost
perfectly. Real choral octavos come from Finale, Sibelius, Dorico or MuseScore:
they hide the staves of resting voices, change metre and key in mid-line, and
tie notes over line and page breaks. The pieces below (original music, built
here) put those hazards on the page with a known answer.

For each source this writes, next to this script:

    <source>.musicxml            the engraving source (built from SOURCES)
    <source>_vrv_<font>.pdf      Verovio (music font <font>), US letter; SVG
                                 pages -> rsvg-convert -> one PDF with pypdf
    <source>_mscore.pdf          MuseScore 4 command line, US letter
    <case>.midi                  ground truth for each <case>.pdf

The ground truth is written from the same note model as the MusicXML, with
repeats and endings played out as a musician would (|: A :|1 B :|2 C -> A B A C).
It is then checked note for note against Verovio's and MuseScore's own MIDI of
the MusicXML (both play repeats); a disagreement stops the script.

Line and page breaks are encoded in the MusicXML so each hazard lands where
it is meant to (a time signature in mid-line, a tie over a page turn, ...).
`--layout` prints, for every Verovio engraving, which bars and how many
staves each system holds.

Needs: the repo's Python env (verovio, pypdf, mido), rsvg-convert (librsvg),
and optionally MuseScore 4 (set MSCORE=/path/to/mscore to choose one).
"""

from __future__ import annotations

import argparse
import base64
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import mido
import verovio
from fontTools.ttLib import TTFont
from pypdf import PdfWriter

HERE = Path(__file__).resolve().parent
DIV = 4  # MusicXML divisions per quarter note; a 16th is 1
MSCORE_TIMEOUT = 120  # seconds
LETTER_MM = (215.9, 279.4)
STAFF_MM = 6.4  # staff height (choral octavos are usually 6-7 mm)
MARGIN_MM = 12.7

# ---------------------------------------------------------------- notation
#
# Voices are written like LilyPond in absolute octaves: c = C3, c' = middle C,
# is/es = sharp/flat, a number is the duration (4 = quarter, 8. = dotted
# eighth; omitted = as before), ~ ties to the next note of the voice, ( and )
# slur, <c' e' g'>4 is a chord, r4 a rest, R a whole-bar rest, \mf a dynamic
# for the next note. Bars are separated by |; a bar that is just - means the
# voice is silent there (a second voice that only appears in some bars).
#
# Lyrics are sung by voice 1 of a staff, one token per note that starts
# (tied continuations and rests are skipped): "lan--" is followed by a hyphen,
# "done__" by an extender line, "_" is a melisma note, "@12" skips to bar 12.

SEMI = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
ALTERS = {"isis": 2, "is": 1, "eses": -2, "es": -1, "s": -1, "": 0}
PITCH = re.compile(r"([a-g])(isis|eses|is|es|s)?([',]*)(~?)")
SUFFIX = re.compile(r"(\d*)(\.*)(~?)([()]*)")
TOKEN = re.compile(r"<[^>]*>\S*|\S+")
TYPES = {16: "whole", 8: "half", 4: "quarter", 2: "eighth", 1: "16th"}
ACCIDENTAL = {0: "natural", 1: "sharp", -1: "flat", 2: "double-sharp", -2: "flat-flat"}
SHARP_ORDER = "FCGDAEB"


@dataclass(frozen=True)
class Pitch:
    step: str
    alter: int
    octave: int

    @property
    def midi(self) -> int:
        return 12 * (self.octave + 1) + SEMI[self.step] + self.alter


@dataclass
class Ev:
    pitches: list[Pitch]  # empty for a rest
    dur: int  # divisions
    base: int = 0  # undotted value in divisions (16 whole ... 1 sixteenth)
    dots: int = 0
    ties: list[bool] = field(default_factory=list)  # a tie starts here (per pitch)
    tied_from: list[bool] = field(default_factory=list)  # a tie ends here (per pitch)
    whole_bar: bool = False
    slur: str = ""
    dyn: str | None = None
    onset: int = 0
    lyrics: dict[int, tuple[str, str, bool]] = field(default_factory=dict)
    beams: dict[int, str] = field(default_factory=dict)
    accidentals: list[str | None] = field(default_factory=list)
    stem: str | None = None


def parse_pitch(text: str) -> tuple[Pitch, bool]:
    m = PITCH.fullmatch(text)
    if not m:
        raise ValueError(f"bad pitch {text!r}")
    letter, acc, octs, tie = m.groups()
    return Pitch(letter.upper(), ALTERS[acc or ""],
                 3 + octs.count("'") - octs.count(",")), bool(tie)


def parse_bar(text: str, bar_len: int | None, last: list) -> list[Ev] | None:
    """One bar of one voice. `last` carries the previous duration."""
    text = text.strip()
    if text == "-":
        return None
    out: list[Ev] = []
    dyn = None
    for tok in TOKEN.findall(text):
        if tok.startswith("\\"):
            dyn = tok[1:]
            continue
        if tok == "R":
            if bar_len is None:
                raise ValueError("R in a pickup bar")
            out.append(Ev([], bar_len, whole_bar=True, dyn=dyn))
            dyn = None
            continue
        if tok.startswith("<"):
            inner, rest = tok[1:].split(">", 1)
            parsed = [parse_pitch(p) for p in inner.split()]
            pitches, ties = [p for p, _ in parsed], [t for _, t in parsed]
        elif tok[0] == "r":
            pitches, ties, rest = [], [], tok[1:]
        else:
            m = re.match(r"[a-g](?:isis|eses|is|es|s)?[',]*", tok)
            if not m:
                raise ValueError(f"bad token {tok!r}")
            p, _ = parse_pitch(m.group(0))
            pitches, ties, rest = [p], [False], tok[m.end():]
        sm = SUFFIX.fullmatch(rest)
        if not sm:
            raise ValueError(f"bad token {tok!r}")
        num, dots, tie, slur = sm.groups()
        if num:
            last[:] = [16 // int(num), len(dots)]
        elif dots:
            raise ValueError(f"dots without a duration in {tok!r}")
        base, ndots = last
        dur, add = base, base
        for _ in range(ndots):
            if add % 2:
                raise ValueError(f"{tok!r} is shorter than a 16th")
            add //= 2
            dur += add
        if tie:
            ties = [True] * len(pitches)
        out.append(Ev(pitches, dur, base, ndots, ties=ties, slur=slur, dyn=dyn))
        dyn = None
    return out


# ---------------------------------------------------------------- score model


@dataclass
class Staff:
    clef: str  # "G" or "F"
    voices: list[str]
    lyrics: list[str] = field(default_factory=list)  # one string per verse
    bars: list[list[list[Ev] | None]] = field(default_factory=list)  # [voice][bar]


@dataclass
class Part:
    name: str
    abbr: str
    staves: list[Staff]
    program: int  # General MIDI program, 1-based
    choir: bool = True


@dataclass
class Source:
    name: str
    title: str
    hazards: list[str]
    key: int  # fifths
    time: tuple[int, int]
    tempo: tuple[str, int]
    parts: list[Part]
    fonts: list[str]  # Verovio music fonts to engrave with
    bars: dict[int, dict] = field(default_factory=dict)  # printed bar number -> changes
    systems: list[int] = field(default_factory=list)  # bars that start a system
    pages: list[int] = field(default_factory=list)  # bars that start a page
    pickup: bool = False
    # filled in by build()
    n: int = 0
    first: int = 1
    bar_len: list[int] = field(default_factory=list)
    times: list[tuple[int, int]] = field(default_factory=list)
    keys: list[int] = field(default_factory=list)

    def attrs(self, i: int) -> dict:
        return self.bars.get(i + self.first, {})


def key_alter(fifths: int, step: str) -> int:
    if fifths > 0:
        return 1 if step in SHARP_ORDER[:fifths] else 0
    if fifths < 0:
        return -1 if step in SHARP_ORDER[::-1][:-fifths] else 0
    return 0


def build(src: Source) -> Source:
    src.first = 0 if src.pickup else 1
    counts = {len(v.split("|")) for p in src.parts for s in p.staves for v in s.voices}
    if len(counts) != 1:
        raise ValueError(f"{src.name}: voices have different bar counts {counts}")
    src.n = counts.pop()
    t, k = src.time, src.key
    src.times, src.keys = [], []
    for i in range(src.n):
        a = src.attrs(i)
        t, k = a.get("time", t), a.get("key", k)
        src.times.append(t)
        src.keys.append(k)
    src.bar_len = [DIV * 4 * b // bt for b, bt in src.times]
    if src.pickup:
        first_voice = src.parts[0].staves[0].voices[0].split("|")[0]
        src.bar_len[0] = sum(e.dur for e in parse_bar(first_voice, None, [4, 0]))

    for p in src.parts:
        for s in p.staves:
            s.bars = []
            for vi, text in enumerate(s.voices):
                last = [4, 0]
                bars = [parse_bar(b, None if (src.pickup and i == 0) else src.bar_len[i], last)
                        for i, b in enumerate(text.split("|"))]
                for i, evs in enumerate(bars):
                    if evs is None:
                        if vi == 0:
                            raise ValueError(f"{src.name} {p.name}: voice 1 silent in bar {i + src.first}")
                        continue
                    pos = 0
                    for e in evs:
                        e.onset = pos
                        pos += e.dur
                    if pos != src.bar_len[i]:
                        raise ValueError(f"{src.name} {p.name} voice {vi + 1} bar {i + src.first}: "
                                         f"{pos / DIV} quarters, expected {src.bar_len[i] / DIV}")
                link_ties(bars, f"{src.name} {p.name} voice {vi + 1}", src.first)
                s.bars.append(bars)
            for verse, text in enumerate(s.lyrics, 1):
                attach_lyrics(s.bars[0], verse, text, src)
            for i in range(src.n):
                voices = [b[i] for b in s.bars]
                two = sum(v is not None for v in voices) > 1
                beat = DIV * 4 // src.times[i][1] * (3 if src.times[i][1] == 8 else 1)
                for vi, evs in enumerate(voices):
                    if evs is None:
                        continue
                    for e in evs:
                        e.stem = ("up" if vi == 0 else "down") if two and e.pitches else None
                    beam(evs, beat)
                mark_accidentals(voices, src.keys[i])
    return src


def link_ties(bars: list[list[Ev] | None], where: str, first: int) -> None:
    prev: Ev | None = None
    for i, evs in enumerate(bars):
        if evs is None:
            if prev is not None and any(prev.ties):
                raise ValueError(f"{where}: tie into a silent bar {i + first}")
            prev = None
            continue
        for e in evs:
            e.tied_from = [False] * len(e.pitches)
            if prev is not None and any(prev.ties):
                for p, t in zip(prev.pitches, prev.ties):
                    if t:
                        if p not in e.pitches:
                            raise ValueError(f"{where} bar {i + first}: tie to a different note")
                        e.tied_from[e.pitches.index(p)] = True
            prev = e
    if prev is not None and any(prev.ties):
        raise ValueError(f"{where}: tie at the very end")


def attach_lyrics(bars: list[list[Ev] | None], verse: int, text: str, src: Source) -> None:
    sung = [(i, e) for i, evs in enumerate(bars) if evs for e in evs
            if e.pitches and not all(e.tied_from)]
    k, hyphen_before = 0, False
    for tok in text.split():
        if tok.startswith("@"):
            target = int(tok[1:]) - src.first
            while k < len(sung) and sung[k][0] < target:
                k += 1
            continue
        if k >= len(sung):
            raise ValueError(f"{src.name}: more syllables than notes at {tok!r}")
        _, e = sung[k]
        k += 1
        if tok == "_":
            continue
        hyphen, extend = tok.endswith("--"), tok.endswith("__")
        word = tok.removesuffix("--").removesuffix("__")
        if hyphen_before:
            syl = "middle" if hyphen else "end"
        else:
            syl = "begin" if hyphen else "single"
        e.lyrics[verse] = (syl, word, extend)
        hyphen_before = hyphen


def beam(evs: list[Ev], beat: int) -> None:
    """Beam eighths and shorter within each beat, as engraving programs do."""
    groups: list[list[Ev]] = []
    cur: list[Ev] = []
    for e in evs:
        b = e.onset // beat
        ok = bool(e.pitches) and e.base <= 2 and (e.onset + e.dur - 1) // beat == b
        if ok and cur and cur[-1].onset // beat == b:
            cur.append(e)
            continue
        if len(cur) > 1:
            groups.append(cur)
        cur = [e] if ok else []
    if len(cur) > 1:
        groups.append(cur)
    for g in groups:
        for i, e in enumerate(g):
            e.beams[1] = "begin" if i == 0 else "end" if i == len(g) - 1 else "continue"
        i = 0
        while i < len(g):
            if g[i].base != 1:
                i += 1
                continue
            j = i
            while j + 1 < len(g) and g[j + 1].base == 1:
                j += 1
            if j == i:
                g[i].beams[2] = "forward hook" if i == 0 else "backward hook"
            else:
                for m in range(i, j + 1):
                    g[m].beams[2] = "begin" if m == i else "end" if m == j else "continue"
            i = j + 1


def mark_accidentals(voices: list[list[Ev] | None], fifths: int) -> None:
    """Printed accidentals: an accidental holds for its line and space until
    the bar line; a tied-over note shows none."""
    state: dict[tuple[str, int], int] = {}
    items = sorted(((e.onset, vi, n, e) for vi, evs in enumerate(voices) if evs
                    for n, e in enumerate(evs)), key=lambda x: x[:3])
    for _, _, _, e in items:
        e.accidentals = []
        for p, tied in zip(e.pitches, e.tied_from):
            cur = state.get((p.step, p.octave), key_alter(fifths, p.step))
            if tied or p.alter == cur:
                e.accidentals.append(None)
            else:
                e.accidentals.append(ACCIDENTAL[p.alter])
                state[(p.step, p.octave)] = p.alter


# ---------------------------------------------------------------- MusicXML


def sub(parent: ET.Element, tag: str, text: str | int | None = None, **attrs) -> ET.Element:
    el = ET.SubElement(parent, tag, {k.replace("_", "-"): str(v) for k, v in attrs.items()})
    if text is not None:
        el.text = str(text)
    return el


def to_musicxml(src: Source) -> bytes:
    root = ET.Element("score-partwise", version="4.0")
    sub(sub(root, "work"), "work-title", src.title)
    ident = sub(root, "identification")
    sub(ident, "creator", "sheet2audio test corpus (original)", type="composer")
    sub(ident, "rights", "Original test material, MIT licensed with sheet2audio")
    sub(sub(ident, "encoding"), "software", "tests/fixtures/engravers/make.py")
    tenths = 40 / STAFF_MM
    defaults = sub(root, "defaults")
    scaling = sub(defaults, "scaling")
    sub(scaling, "millimeters", STAFF_MM)
    sub(scaling, "tenths", 40)
    page = sub(defaults, "page-layout")
    sub(page, "page-height", round(LETTER_MM[1] * tenths, 2))
    sub(page, "page-width", round(LETTER_MM[0] * tenths, 2))
    margins = sub(page, "page-margins", type="both")
    for side in ("left", "right", "top", "bottom"):
        sub(margins, f"{side}-margin", round(MARGIN_MM * tenths, 2))
    credit = sub(root, "credit", page=1)
    sub(credit, "credit-words", src.title, default_x=round(LETTER_MM[0] * tenths / 2),
        default_y=round((LETTER_MM[1] - MARGIN_MM) * tenths), justify="center", valign="top",
        font_size=20)

    plist = sub(root, "part-list")
    choir = [i for i, p in enumerate(src.parts) if p.choir]
    for i, p in enumerate(src.parts):
        if choir and i == choir[0] and len(choir) > 1:
            g = sub(plist, "part-group", type="start", number=1)
            sub(g, "group-symbol", "bracket")
            sub(g, "group-barline", "no")
        sp = sub(plist, "score-part", id=f"P{i + 1}")
        sub(sp, "part-name", p.name)
        sub(sp, "part-abbreviation", p.abbr)
        inst = sub(sp, "score-instrument", id=f"P{i + 1}-I1")
        sub(inst, "instrument-name", p.name)
        mi = sub(sp, "midi-instrument", id=f"P{i + 1}-I1")
        sub(mi, "midi-channel", i + 1)
        sub(mi, "midi-program", p.program)
        if choir and i == choir[-1] and len(choir) > 1:
            sub(plist, "part-group", type="stop", number=1)

    for pi, p in enumerate(src.parts):
        part = sub(root, "part", id=f"P{pi + 1}")
        for i in range(src.n):
            write_measure(src, pi, p, part, i)
    xml = ET.tostring(root, encoding="unicode")
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<!DOCTYPE score-partwise PUBLIC "-//Recordare//DTD MusicXML 4.0 Partwise//EN" '
            '"http://www.musicxml.org/dtds/partwise.dtd">\n' + xml + "\n").encode()


def write_measure(src: Source, pi: int, p: Part, part: ET.Element, i: int) -> None:
    num = i + src.first
    a = src.attrs(i)
    m = sub(part, "measure", number=num)
    if src.pickup and i == 0:
        m.set("implicit", "yes")
    if i > 0 and num in src.pages:
        sub(m, "print", new_page="yes")
    elif i > 0 and num in src.systems:
        sub(m, "print", new_system="yes")
    at = None
    if i == 0 or "key" in a or "time" in a:
        at = sub(m, "attributes")
        if i == 0:
            sub(at, "divisions", DIV)
        if i == 0 or "key" in a:
            key = sub(at, "key")
            if i > 0 and src.keys[i - 1] != src.keys[i] and src.keys[i - 1] != 0:
                sub(key, "cancel", src.keys[i - 1])
            sub(key, "fifths", src.keys[i])
            sub(key, "mode", "major")
        if i == 0 or "time" in a:
            tm = sub(at, "time")
            sub(tm, "beats", src.times[i][0])
            sub(tm, "beat-type", src.times[i][1])
        if i == 0:
            if len(p.staves) > 1:
                sub(at, "staves", len(p.staves))
            for si, s in enumerate(p.staves, 1):
                c = sub(at, "clef", number=si) if len(p.staves) > 1 else sub(at, "clef")
                sub(c, "sign", s.clef)
                sub(c, "line", 2 if s.clef == "G" else 4)
    if a.get("repeat_start") or "ending_start" in a:
        bl = sub(m, "barline", location="left")
        if a.get("repeat_start"):
            sub(bl, "bar-style", "heavy-light")
        if "ending_start" in a:
            sub(bl, "ending", number=a["ending_start"], type="start")
        if a.get("repeat_start"):
            sub(bl, "repeat", direction="forward")
    # The tempo mark goes on the top staff that plays in bar 1 (a hidden staff
    # would take it along).
    if i == 0 and pi == next(k for k, q in enumerate(src.parts)
                             if any(not e.whole_bar for s in q.staves for e in s.bars[0][0])):
        d = sub(m, "direction", placement="above")
        sub(sub(d, "direction-type"), "words", src.tempo[0], font_weight="bold")
        met = sub(sub(d, "direction-type"), "metronome", parentheses="no")
        sub(met, "beat-unit", "quarter")
        sub(met, "per-minute", src.tempo[1])
        sub(d, "staff", 1)
        sub(d, "sound", tempo=src.tempo[1])
    first_voice = True
    for si, s in enumerate(p.staves):
        for vi, bars in enumerate(s.bars):
            evs = bars[i]
            if evs is None:
                continue
            if not first_voice:
                sub(sub(m, "backup"), "duration", src.bar_len[i])
            first_voice = False
            voice = si * 4 + vi + 1
            for e in evs:
                if e.dyn:
                    d = sub(m, "direction", placement="above" if p.choir else "below")
                    sub(sub(sub(d, "direction-type"), "dynamics"), e.dyn)
                    if len(p.staves) > 1:
                        sub(d, "staff", si + 1)
                write_event(m, e, voice, si + 1 if len(p.staves) > 1 else None)
    last = i == src.n - 1
    if a.get("repeat_end") or "ending_stop" in a or a.get("double_bar") or last:
        bl = sub(m, "barline", location="right")
        sub(bl, "bar-style", "light-heavy" if a.get("repeat_end") or last
            else "light-light" if a.get("double_bar") else "regular")
        if "ending_stop" in a:
            num_, kind = a["ending_stop"]
            sub(bl, "ending", number=num_, type=kind)
        if a.get("repeat_end"):
            sub(bl, "repeat", direction="backward")


def write_event(m: ET.Element, e: Ev, voice: int, staff: int | None) -> None:
    pitches = e.pitches or [None]
    for n, p in enumerate(pitches):
        note = sub(m, "note")
        if n:
            sub(note, "chord")
        if p is None:
            if e.whole_bar:
                sub(note, "rest", measure="yes")
            else:
                sub(note, "rest")
        else:
            pe = sub(note, "pitch")
            sub(pe, "step", p.step)
            if p.alter:
                sub(pe, "alter", p.alter)
            sub(pe, "octave", p.octave)
        sub(note, "duration", e.dur)
        tie_stop = bool(p and e.tied_from[n])
        tie_start = bool(p and e.ties[n])
        if tie_stop:
            sub(note, "tie", type="stop")
        if tie_start:
            sub(note, "tie", type="start")
        sub(note, "voice", voice)
        if not e.whole_bar:
            sub(note, "type", TYPES[e.base])
            for _ in range(e.dots):
                sub(note, "dot")
        if p is not None and e.accidentals[n]:
            sub(note, "accidental", e.accidentals[n])
        if p is not None and e.stem:
            sub(note, "stem", e.stem)
        if staff:
            sub(note, "staff", staff)
        if n == 0:
            for level, value in sorted(e.beams.items()):
                sub(note, "beam", value, number=level)
        notations = []
        if tie_stop:
            notations.append(("tied", {"type": "stop"}))
        if tie_start:
            notations.append(("tied", {"type": "start"}))
        if n == 0 and ")" in e.slur:
            notations.append(("slur", {"type": "stop", "number": "1"}))
        if n == 0 and "(" in e.slur:
            notations.append(("slur", {"type": "start", "number": "1"}))
        if notations:
            nt = sub(note, "notations")
            for tag, attrs in notations:
                ET.SubElement(nt, tag, attrs)
        if n == 0:
            for verse, (syl, text, extend) in sorted(e.lyrics.items()):
                ly = sub(note, "lyric", number=verse)
                sub(ly, "syllabic", syl)
                sub(ly, "text", text)
                if extend:
                    sub(ly, "extend", type="start")


# ---------------------------------------------------------------- ground truth


def play_order(src: Source) -> list[int]:
    """Bar indices in the order a musician plays them: one repeat, with
    optional first/second endings."""
    endings: dict[int, int] = {}
    cur = None
    for i in range(src.n):
        a = src.attrs(i)
        if "ending_start" in a:
            cur = int(a["ending_start"])
        if cur is not None:
            endings[i] = cur
        if "ending_stop" in a:
            cur = None
    order, i, start, jumped = [], 0, 0, False
    passno = 1
    while i < src.n:
        a = src.attrs(i)
        if a.get("repeat_start"):
            start = i
        if i in endings and endings[i] != passno:
            i += 1
            continue
        order.append(i)
        if a.get("repeat_end") and not jumped:
            jumped, passno, i = True, 2, start
            continue
        i += 1
    return order


def ground_truth(src: Source) -> bytes:
    order = play_order(src)
    starts, t = [], 0
    for i in order:
        starts.append(t)
        t += src.bar_len[i]
    tpq = 480
    tick = tpq // DIV
    mf = mido.MidiFile(ticks_per_beat=tpq)
    meta = mido.MidiTrack()
    meta.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(src.tempo[1]), time=0))
    meta.append(mido.MetaMessage("end_of_track", time=0))
    mf.tracks.append(meta)
    for pi, p in enumerate(src.parts):
        for s in p.staves:
            events: list[tuple[int, int, mido.Message]] = []
            for bars in s.bars:
                # (bar, event) in score order, to follow ties
                flat = [(i, e) for i, evs in enumerate(bars) if evs for e in evs]
                length: dict[tuple[int, int], int] = {}
                for k, (i, e) in enumerate(flat):
                    for n, pch in enumerate(e.pitches):
                        if e.tied_from[n]:
                            continue
                        total, kk, nn = e.dur, k, n
                        while flat[kk][1].ties[nn]:
                            kk += 1
                            nxt = flat[kk][1]
                            nn = nxt.pitches.index(flat[kk - 1][1].pitches[nn])
                            total += nxt.dur
                        length[(k, n)] = total
                where = {id(e): k for k, (_, e) in enumerate(flat)}
                for start, i in zip(starts, order):
                    for e in bars[i] or []:
                        for n, pch in enumerate(e.pitches):
                            if e.tied_from[n]:
                                continue
                            on = (start + e.onset) * tick
                            off = on + length[(where[id(e)], n)] * tick
                            events.append((on, 1, mido.Message("note_on", channel=pi, note=pch.midi,
                                                               velocity=80)))
                            events.append((off, 0, mido.Message("note_off", channel=pi,
                                                                note=pch.midi, velocity=0)))
            track = mido.MidiTrack()
            track.append(mido.MetaMessage("track_name", name=p.name, time=0))
            track.append(mido.Message("program_change", channel=pi, program=p.program - 1, time=0))
            now = 0
            for at, _, msg in sorted(events, key=lambda x: (x[0], x[1], x[2].note)):
                track.append(msg.copy(time=at - now))
                now = at
            track.append(mido.MetaMessage("end_of_track", time=0))
            mf.tracks.append(track)
    buf = io.BytesIO()
    mf.save(file=buf)
    return buf.getvalue()


def midi_notes(data: bytes) -> list[tuple[float, int]]:
    """(onset in quarter notes, pitch), as tests/evaluate.py reads a MIDI file."""
    mf = mido.MidiFile(file=io.BytesIO(data))
    notes = []
    for tr in mf.tracks:
        t = 0
        for msg in tr:
            t += msg.time
            if msg.type == "note_on" and msg.velocity > 0 and getattr(msg, "channel", 0) != 9:
                notes.append((t / mf.ticks_per_beat, msg.note))
    return sorted(notes)


def compare(gt: bytes, other: bytes) -> str | None:
    q = lambda notes: Counter((round(t * 48), n) for t, n in notes)  # noqa: E731
    a, b = q(midi_notes(gt)), q(midi_notes(other))
    if a == b:
        return None
    missing, extra = a - b, b - a
    fmt = lambda c: ", ".join(f"{t / 48:g}:{n}" for (t, n) in sorted(c.elements())[:8])  # noqa: E731
    return (f"{sum(missing.values())} ground-truth notes missing (first: {fmt(missing)}); "
            f"{sum(extra.values())} extra (first: {fmt(extra)})")


# ---------------------------------------------------------------- engraving


def toolkit(options: dict, xml: bytes) -> verovio.toolkit:
    verovio.enableLog(verovio.LOG_OFF)
    tk = verovio.toolkit()
    tk.setResourcePath(str(Path(verovio.__file__).with_name("data")))
    tk.setOptions(options)
    if not tk.loadData(xml.decode()):
        raise RuntimeError("Verovio could not load the MusicXML")
    return tk


def verovio_options(font: str) -> dict:
    return {
        "font": font,
        "pageWidth": round(LETTER_MM[0] * 10),  # tenths of a millimetre
        "pageHeight": round(LETTER_MM[1] * 10),
        "pageMarginLeft": round(MARGIN_MM * 10),
        "pageMarginRight": round(MARGIN_MM * 10),
        "pageMarginTop": round(MARGIN_MM * 10),
        "pageMarginBottom": round(MARGIN_MM * 10),
        "unit": STAFF_MM * 10 / 8,  # half a staff space, in tenths of a millimetre
        "scaleToPageSize": True,
        "breaks": "encoded",
        "condense": "auto",  # hide the staves of voices that rest for a whole system
        "condenseFirstPage": True,
        "justifyVertically": True,
        "justificationMaxVertical": 0.5,
        "header": "auto",
        "footer": "none",
        "lyricSize": 4.0,
        "svgAdditionalAttribute": ["measure@n"],
    }


def rsvg_env(font: str, tmp: Path) -> dict[str, str]:
    """Verovio writes SMuFL text glyphs (the note of a metronome mark) as text
    in the music font, embedded as a web font that librsvg ignores; Pango then
    draws a page-high black box. Hand the font to Pango through fontconfig."""
    fonts = tmp / "fonts"
    fonts.mkdir(exist_ok=True)
    otf = fonts / f"{font}.otf"
    if not otf.exists():
        css = (Path(verovio.__file__).with_name("data") / f"{font}.css").read_text()
        ft = TTFont(io.BytesIO(base64.b64decode(re.search(r"base64,([^)]+)\)", css).group(1))))
        ft.flavor = None
        ft.save(otf)
    conf = tmp / "fonts.conf"
    system = "".join(f'<include ignore_missing="yes">{p}</include>' for p in
                     ("/etc/fonts/fonts.conf", "/opt/homebrew/etc/fonts/fonts.conf",
                      "/usr/local/etc/fonts/fonts.conf"))
    conf.write_text(f'<?xml version="1.0"?><fontconfig>{system}<dir>{fonts}</dir>'
                    f"<cachedir>{fonts / 'cache'}</cachedir></fontconfig>")
    return dict(os.environ, FONTCONFIG_FILE=str(conf), PANGOCAIRO_BACKEND="fc")


def engrave_verovio(xml: bytes, font: str, out: Path, tmp: Path) -> list[list[tuple[str, str, int]]]:
    """Returns, per page, (first bar, last bar, staves shown) per system."""
    tk = toolkit(verovio_options(font), xml)
    env = rsvg_env(font, tmp)
    writer = PdfWriter()
    layout = []
    for page in range(1, tk.getPageCount() + 1):
        svg = tk.renderToSVG(page)
        layout.append(svg_layout(svg))
        svg_path, pdf_path = tmp / f"{out.stem}-{page}.svg", tmp / f"{out.stem}-{page}.pdf"
        svg_path.write_text(svg)
        subprocess.run(["rsvg-convert", "-f", "pdf", "--page-width", "8.5in", "--page-height",
                        "11in", "-w", "8.5in", "-h", "11in", "-o", str(pdf_path), str(svg_path)],
                       check=True, env=env)
        writer.append(str(pdf_path))
    writer.add_metadata({"/Title": out.stem, "/Producer": f"Verovio ({font}), rsvg-convert, pypdf"})
    with open(out, "wb") as f:
        writer.write(f)
    return layout


def svg_layout(svg: str) -> list[tuple[str, str, int]]:
    ns = {"s": "http://www.w3.org/2000/svg"}
    root = ET.fromstring(svg)
    out = []
    for system in root.iter("{http://www.w3.org/2000/svg}g"):
        if "system" not in system.get("class", "").split():
            continue
        bars = [m for m in system.iter("{http://www.w3.org/2000/svg}g")
                if "measure" in m.get("class", "").split()]
        if not bars:
            continue
        staves = [g for g in bars[0].findall("s:g", ns) if "staff" in g.get("class", "").split()]
        out.append((bars[0].get("data-n", "?"), bars[-1].get("data-n", "?"), len(staves)))
    return out


def find_mscore() -> str | None:
    for c in (os.environ.get("MSCORE"), "/Applications/MuseScore 4.app/Contents/MacOS/mscore",
              shutil.which("mscore4"), shutil.which("mscore"), shutil.which("musescore")):
        if c and Path(c).is_file():
            return c
    return None


MSCORE_STYLE = f"""<?xml version="1.0" encoding="UTF-8"?>
<museScore version="4.40">
  <Style>
    <pageWidth>8.5</pageWidth>
    <pageHeight>11</pageHeight>
    <pagePrintableWidth>{8.5 - 2 * MARGIN_MM / 25.4:.3f}</pagePrintableWidth>
    <pageEvenLeftMargin>{MARGIN_MM / 25.4:.3f}</pageEvenLeftMargin>
    <pageOddLeftMargin>{MARGIN_MM / 25.4:.3f}</pageOddLeftMargin>
    <pageEvenTopMargin>{MARGIN_MM / 25.4:.3f}</pageEvenTopMargin>
    <pageEvenBottomMargin>{MARGIN_MM / 25.4:.3f}</pageEvenBottomMargin>
    <pageOddTopMargin>{MARGIN_MM / 25.4:.3f}</pageOddTopMargin>
    <pageOddBottomMargin>{MARGIN_MM / 25.4:.3f}</pageOddBottomMargin>
    <Spatium>{STAFF_MM / 4:.3f}</Spatium>
    <hideEmptyStaves>1</hideEmptyStaves>
    <dontHideStavesInFirstSystem>0</dontHideStavesInFirstSystem>
    <keySigNaturals>1</keySigNaturals>
  </Style>
</museScore>
"""


def run_mscore(mscore: str, args: list[str]) -> str | None:
    """None on success, else why it failed."""
    try:
        proc = subprocess.run([mscore, *args], capture_output=True, text=True,
                              timeout=MSCORE_TIMEOUT)
    except subprocess.TimeoutExpired:
        return f"timed out after {MSCORE_TIMEOUT} s"
    if proc.returncode != 0:
        tail = [ln for ln in proc.stderr.splitlines() if not ln.startswith("qt.")][-3:]
        return f"exit {proc.returncode}: {' | '.join(tail)}"
    return None


# ---------------------------------------------------------------- the pieces
# fmt: off

def satb_piano(sa, sa2, tb, tb2, rh, lh, words, children=None):
    parts = []
    if children:
        parts.append(Part("Children", "Ch.", [Staff("G", [children[0]], children[1])], 53))
    parts += [
        Part("Soprano Alto", "S A", [Staff("G", [sa, sa2], words)], 53),
        Part("Tenor Bass", "T B", [Staff("F", [tb, tb2])], 53),
        Part("Piano", "Pno.", [Staff("G", [rh]), Staff("F", [lh])], 1, choir=False),
    ]
    return parts


def silent(n: int, first: int = 1, **bars) -> str:
    """A voice that only sings in some bars, given as b<bar number>="..."."""
    return "|".join(bars.get(f"b{i}", "-") for i in range(first, first + n))


SOURCES = [
    Source(
        name="evening_lanterns", title="Evening Lanterns", key=1, time=(2, 4),
        tempo=("Flowing", 96), fonts=["Leipzig", "Bravura"],
        hazards=["a: 2/4 -> 4/4 in mid-line (bar 9, system 2) and 4/4 continues over the page "
                 "turn to the end with no reminder",
                 "e: ties over the line break (bars 5-6, chords in all staves) and over the page "
                 "turn (bars 10-11, S, A, T/B chord, piano chords)",
                 "f: lyrics with melisma extenders (bars 8, 14, 16)",
                 "two voices on the S/A staff (bars 10, 11, 14) and T/B staff (bar 14)"],
        bars={9: {"time": (4, 4)}},
        systems=[6, 14, 19], pages=[11, 17],
        parts=satb_piano(
            sa=r"R | R | \mf <b' d''>8 <b' d''> <g' b'>4 | <g' c''>8 <c'' e''> <g' c''>4 | <fis' a'>2~ |"
               r" <fis' a'>4 r8 <fis' d''>8 | <g' e''>8 <g' d''> <e' c''> <g' b'> | <fis' a'>8 <g' b'>( <fis' a'>4) |"
               r" <g' b'>4. <fis' a'>8 <d' g'>4 <g' b'>4 | c''4 e''8 d'' c''4 d''~ | d''2 b'4 g'4 |"
               r" <e' a'>4 <g' b'>8 <a' c''> <fis' d''>4 <fis' c''>8 <g' b'> | <e' c''>4 <e' a'>8 <g' b'> <g' c''>2 |"
               r" d''2.( c''8 b') | <g' b'>4 <g' b'>8 <g' b'> <g' e''>4 <g' d''>4 | <e' c''>4 <g' b'>4( <fis' a'>2) |"
               r" <d' g'>4. <fis' a'>8 <g' b'>4 <b' d''>4 | <g' e''>4 <g' d''>8 <e' c''> <g' b'>4 <fis' a'>4 |"
               r" <fis' a'>2 <d' g'>2~ | <d' g'>1",
            sa2=silent(20, b10="e'2 fis'4 a'~", b11="a'4 g' g' e'", b14="a'2 fis'2"),
            tb=r"R | R | \mf <g d'>8 <g d'> <g d'>4 | <c g>8 <c g> <c g>4 | <d a>2~ | <d a>4 r8 <d a>8 |"
               r" <e b>8 <g b> <c g> <e g> | <d a>8 <e g>( <d fis>4) | <g d'>4. <d d'>8 <g b>4 <g d'>4 |"
               r" <c g>2 <d fis>4 <d a>4~ | <d a>2 <g b>4 <e b>4 | <a, e>2 <d a>2 | <c g>4 <d a>4 <e g>2 |"
               r" a2( fis2) | <g d'>2 <c g>4 <b, g>4 | <a, e>4 <g, d>4 <d a>2 | <g d'>4. <d d'>8 <g d'>4 <g b>4 |"
               r" <c g>4 <c g>8 <c g> <d g>4 <d fis>4 | <d a>2 <g, g>2~ | <g, g>1",
            tb2=silent(20, b14="d1"),
            rh=r"\mp <b d' g'>8 d'' <b d' g'> d'' | <c' d' fis'>8 d'' <c' d' fis'> a' | <b d' g'>4 <b d' g'>4 |"
               r" <c' e' g'>4 <c' e' g'>4 | <a d' fis'>2~ | <a d' fis'>4 r8 <a d' fis'>8 | <g b e'>4 <g c' e'>4 |"
               r" <a d' fis'>8 <b d' g'>8 <a d' fis'>4 | <b d' g'>4. <a d' fis'>8 <g b d'>4 <b d' g'>4 |"
               r" <c' e' g'>2 <a d' fis'>2~ | <a d' fis'>2 <b d' g'>4 <b e' g'>4 | <c' e' a'>2 <c' fis' a'>2 |"
               r" <c' e' g'>4 <c' e' a'>4 <c' e' g'>2 | <d' fis' a'>2 <c' fis' a'>2 | <b d' g'>2 <c' e' g'>4 <b d' g'>4 |"
               r" <c' e' a'>4 <b d' g'>4 <a d' fis'>2 | <b d' g'>4. <a d' fis'>8 <b d' g'>4 <d' g' b'>4 |"
               r" <c' e' g'>2 <b d' g'>4 <a d' fis'>4 | <a d' fis'>2 <b d' g'>2~ | <b d' g'>1",
            lh="g,4 d4 | d4 a,4 | g,4 d4 | c4 g,4 | d2~ | d4 r8 d8 | e4 c4 | d4 d,4 | g,4 d4 g,4 d4 |"
               " c2 d2~ | d2 g,4 e4 | a,2 d2 | c2 e2 | d2 d,2 | g,2 c4 b,4 | a,4 g,4 d2 | g,4 d4 g,4 d4 |"
               " c2 d4 d4 | d2 g,2~ | g,1",
            words=["@3 Light the lan-- terns one by one, now the eve-- ning's work is done__ _"
                   " Home-- ward, home-- ward, through the qui-- et fields we go, where the riv-- er's"
                   " sil-- ver wa-- ters soft-- ly flow__ _ _ Light the lan-- terns, one by one__ _"
                   " home-- ward, home-- ward, now the eve-- ning's work is done."]),
    ),
    Source(
        name="harbor_song", title="Harbor Song", key=2, time=(4, 4),
        tempo=("Moderato", 84), fonts=["Leland", "Petaluma"],
        hazards=["b: one 2/4 bar in mid-line (4/4 -> 2/4 -> 4/4) twice: bar 6 (system 2) and "
                 "bar 13 (system 4, page 2)",
                 "e: ties over the line break in chords (bars 3-4: T/B chord, piano chord, bass)",
                 "two voices on the S/A staff (bars 2-4, 7-8, 10-11, 14-15) and T/B staff (bar 10)",
                 "f: lyrics with melismas (bars 4, 9, 11)"],
        bars={6: {"time": (2, 4)}, 7: {"time": (4, 4)}, 13: {"time": (2, 4)}, 14: {"time": (4, 4)}},
        systems=[4, 8, 15], pages=[11],
        parts=satb_piano(
            sa=r"R | \mf a'4 a'8 b' a'4 fis'4 | b'4 a'8 g' fis'4 e'4 | a'2( g'4) fis'4 |"
               r" <fis' a'>4 <fis' a'>8 <fis' a'> <g' b'>4 <fis' a'>4 | <cis' g'>4 <cis' e'>4 | fis'8 g' a'4 d''2 |"
               r" b'4 b'8 cis'' d''4 b'4 | <e' a'>4 <e' g'>8 <d' fis'>( <cis' e'>2) | fis'4 fis'8 g' a'4 b'4 |"
               r" a'4 g'8 fis'( e'4 d'4) | <g' d''>2 <g' b'>4 <fis' a'>4 | <fis' d''>4 <g' b'>4 | a'2 g'4 fis'4~ |"
               r" fis'2 r4 a'4 | <fis' d''>1 | R",
            sa2=silent(17, b2="fis'2 fis'4 d'4", b3="d'4 d'4 d'4 cis'4", b4="e'2 cis'4 d'4",
                       b7="d'4 e'4 fis'2", b8="fis'4 fis'4 fis'4 g'4", b10="d'4 d'4 fis'4 g'4",
                       b11="cis'4 cis'4 b4 a4", b14="fis'2 e'4 d'4~", b15="d'2 r4 cis'4"),
            tb=r"R | \mf <d d'>2 <d a>4 <d a>4 | <g, d>4 <b, g>4 <d a>4 <a, e>4~ | <a, e>2 <a, cis>4 <d a>4 |"
               r" <d d'>4 <d d'>8 <d d'> <g, d'>4 <d d'>4 | <a, e>4 <a, g>4 | <d a>4 <cis a>4 <d a>2 |"
               r" <b, d'>4 <b, d'>4 <b, b>4 <g, d'>4 | <a, e>4 <a, cis>4 <a, e>2 | a4 a8 b a4 b4 |"
               r" <a, e>2 <a, g>4 <d fis>4 | <g, b>2 <g d'>4 <d d'>4 | <b, d'>4 <g, d'>4 | <d a>2 <a, e>4 <d a>4~ |"
               r" <d a>2 r4 <a, g>4 | <d a>1 | R",
            tb2=silent(17, b10="d2 d4 g,4"),
            rh=r"\mp <a d' fis'>4 <a d' fis'>8 <a d' g'> <a d' fis'>2 | <a d' fis'>2 <a d' fis'>4 <a d' fis'>4 |"
               r" <g b d'>2 <a cis' e'>2~ | <a cis' e'>2 <a cis' e'>4 <a d' fis'>4 | <a d' fis'>2 <b d' g'>4 <a d' fis'>4 |"
               r" <a cis' g'>4 <a cis' e'>4 | <a d' fis'>4 <cis' e' g'>4 <d' fis' a'>2 | <b d' fis'>2 <b d' fis'>4 <b d' g'>4 |"
               r" <a cis' e'>2 <a cis' e'>2 | <a d' fis'>2 <a d' fis'>4 <b d' g'>4 | <a cis' e'>2 <a cis' g'>4 <a d' fis'>4 |"
               r" <b d' g'>2 <b d' g'>4 <a d' fis'>4 | <b d' fis'>4 <b d' g'>4 | <a d' fis'>2 <a cis' e'>4 <a d' fis'>4~ |"
               r" <a d' fis'>2 r4 <a cis' e'>4 | <a d' fis'>1 | <fis' a' d''>4 <e' a' cis''>4 <fis' a' d''>2",
            lh="d4 a,4 d2 | d8 a d' a d a d' a | g,8 d g d a,2~ | a,2 a,4 d4 | d8 a d' a g,4 d4 | a,4 a,4 |"
               " d4 a,4 d2 | b,8 fis b fis b,4 g,4 | a,8 e a e a,2 | d8 a d' a d4 g,4 | a,2 a,4 d4 |"
               " g,8 d g d g,4 d4 | b,4 g,4 | d2 a,4 d4~ | d2 r4 a,4 | d1 | d4 a,4 d,2",
            words=["Sails up-- on the har-- bor, bright a-- gainst the morn-- _ ing, gulls a-- bove the"
                   " wa-- ter call the fish-- ers home. Nets are mend-- ed, lan-- terns glow-- ing__ _"
                   " tide is turn-- ing, wind is blow-- ing__ _ _ sing us home, sing us home a-- gain,"
                   " a-- gain."]),
    ),
    Source(
        name="three_rivers", title="Where Three Rivers Meet", key=-2, time=(4, 4),
        tempo=("Steadily", 76), fonts=["Bravura", "Gootville"],
        hazards=["c: B-flat major -> C major in mid-line (bar 9, system 3): natural signs only",
                 "c: C major -> F major at a system start (bar 15, courtesy key at the end of "
                 "system 4)",
                 "c: F major -> D major in mid-line (bar 20, system 6): a natural then two sharps",
                 "notes whose pitch depends on the key: B and E in the C section, B, F#, C# in "
                 "the D section",
                 "two voices on the S/A staff (bars 12, 20) and T/B staff (bar 6)"],
        bars={8: {"double_bar": True}, 9: {"key": 0}, 14: {"double_bar": True}, 15: {"key": -1},
              19: {"double_bar": True}, 20: {"key": 2}},
        systems=[5, 8, 15, 19], pages=[12],
        parts=satb_piano(
            sa=r"R | R | \mf <d' bes'>4 <d' bes'>8 <es' c''> <d' bes'>4 <d' f'>4 |"
               r" <es' g'>4 <g' bes'>8 <g' bes'> <f' c''>4 <f' a'>4 | <f' bes'>2. r4 |"
               r" <bes' d''>4 <g' d''>8 <g' c''> <g' bes'>4 <es' g'>4 | <f' c''>4 <f' bes'>8 <f' a'> <f' bes'>2 |"
               r" r2 r4 <d' g'>4 | <e' c''>4 <e' c''>8 <f' d''> <g' e''>4 <e' g'>4 | <g' d''>4 <f' b'>4 <e' c''>2 |"
               r" r4 <c'' e''>4 <g' e''>8 <g' d''> <a' c''>4 | b'4 a'8 g' a'4 b'4 | <e' c''>2. r4 | R |"
               r" <f' a'>4 <f' a'>8 <g' bes'> <a' c''>4 <f' a'>4 | <g' bes'>4 <e' g'>8 <e' g'> <e' c''>2 |"
               r" <f' a'>4 <f' a'>8 <g' bes'> <a' c''>4 <a' d''>4 | <e' c''>4 <e' bes'>8 <e' g'> <c' f'>2 |"
               r" r2 r4 <e' a'>4 | fis'4 fis'8 g' a'4 b'4 | <e' cis''>2( <d' b'>4) <cis' a'>4 | <fis' d''>1",
            sa2=silent(22, b12="g'4 f'4 f'2", b20="d'2 fis'4 g'4"),
            tb=r"R | R | \mf <bes, f>4 <bes, f>8 <a, f> <bes, f>4 <bes, bes>4 | <es bes>4 <es bes>8 <es bes> <f a>4 <f c'>4 |"
               r" <bes, d'>2. r4 | d'4 d'8 c' bes4 bes4 | <f c'>4 <f d'>8 <f c'> <bes, d'>2 | r2 r4 <g, b>4 |"
               r" <c g>4 <c g>8 <b, g> <c g>4 <c c'>4 | <g, b>4 <g, b>4 <c c'>2 | r4 <a, e>4 <e b>8 <e b> <a, e>4 |"
               r" <e b>4 <f a>4 <f a>4 <g b>4 | <c g>2. r4 | R | <f c'>2 <f a>4 <d a>4 | <g, d>2 <c bes>2 |"
               r" <f c'>2 <f c'>4 <d f>4 | <c g>4 <c g>8 <c bes> <f a>2 | r2 r4 <a, cis'>4 |"
               r" <d a>4 <d a>8 <cis a> <d a>4 <g, b>4 | <a, e>2. <a, g>4 | <d a>1",
            tb2=silent(22, b6="g2 c2"),
            rh=r"\mp <d' f' bes'>2 <es' g' bes'>2 | <c' f' a'>2 <d' f' bes'>2 | <d' f' bes'>2 <d' f' bes'>2 |"
               r" <es' g' bes'>2 <f' a' c''>2 | <d' f' bes'>2 <d' f' bes'>4 <c' f' a'>4 | <d' g' bes'>2 <c' es' g'>2 |"
               r" <c' f' a'>2 <d' f' bes'>2 | <d' f' bes'>2 <d' f' b'>2 | <e' g' c''>2 <e' g' c''>2 |"
               r" <d' g' b'>2 <e' g' c''>2 | <c' e' a'>2 <e' g' b'>2 | <d' f' a'>2 <d' g' b'>2 | <e' g' c''>2. r4 |"
               r" <c' e' g'>2 <c' e' bes'>2 | <c' f' a'>2 <c' f' a'>2 | <d' g' bes'>2 <e' g' c''>2 |"
               r" <c' f' a'>2 <d' f' a'>2 | <e' g' c''>4 <e' g' bes'>4 <c' f' a'>2 | <c' f' a'>2 <cis' e' g'>2 |"
               r" <d' fis' a'>2 <d' g' b'>2 | <cis' e' a'>2 <cis' e' g'>2 | <d' fis' a' d''>1",
            lh="bes,2 es2 | f,2 bes,2 | bes,8 f bes f bes, f bes f | es2 f2 | bes,2 bes,4 f,4 | g,2 c2 |"
               " f,2 bes,2 | bes,2 g,2 | c8 g c' g c g c' g | g,2 c2 | a,2 e2 | f2 g2 | c2. r4 | c2 c,2 |"
               " f,8 c f c f, c f c | g,2 c2 | f,2 d2 | c2 f,2 | f,2 a,2 | d8 a d' a g,2 | a,2 a,2 | <d, d>1",
            words=["@3 Down by the wa-- ter where the three riv-- ers meet, stones worn smooth by the"
                   " pass-- ing of feet. Sing of the morn-- ing, sing of the light, sing of the riv--"
                   " er that runs through the night. @15 Blue is the eve-- ning, gold is the sky,"
                   " soft are the waves as the boats go by. Home, the riv-- ers bring us home__ _ a-- gain."]),
    ),
    Source(
        name="morning_bell", title="Morning Bell", key=-3, time=(3, 4),
        tempo=("Gently", 112), fonts=["Leipzig", "Leland"],
        hazards=["d: first system is piano only (children and choir tacet, their staves hidden) "
                 "and holds the start-repeat (bar 3)",
                 "d: first ending (bars 11-12) jumps back to that piano-only line; second ending "
                 "(bars 13-14) starts page 2",
                 "system 2 has children + piano only (choir staves hidden)",
                 "f: two verses under the children's staff, with melisma extenders (bar 11); "
                 "'Oo' extender over four bars in the choir",
                 "two voices on the S/A staff (bars 16, 18) and T/B staff (bar 11)"],
        bars={3: {"repeat_start": True}, 11: {"ending_start": "1"},
              12: {"ending_stop": ("1", "stop"), "repeat_end": True},
              13: {"ending_start": "2"}, 14: {"ending_stop": ("2", "discontinue")}},
        systems=[5, 9, 17], pages=[13],
        parts=satb_piano(
            children=(r"R | R | R | R | \mp bes'4 g'4 bes'4 | c''4 bes'4 g'4 | as'4 f'4 as'4 | bes'2. |"
                      r" g'4 es'4 g'4 | as'4 bes'4 c''4 | bes'4( as'4) f'4 | es'2. | \f bes'4 c''4 d''4 |"
                      r" es''2 r4 | c''4 bes'4 as'4 | g'4 as'4 bes'4 | c''4 d''4 es''4 | d''2 bes'4 | es''2.~ | es''2.",
                      ["@5 Hear the bell of the morn-- ing ring-- ing clear, call-- ing us out to greet"
                       " the__ _ new day. Ring, oh ring out, ring the morn-- ing bell, ring for the day"
                       " that is new!",
                       "@5 Sing to the hills and the o-- cean and plain, sing to the sun as it climbs__ _"
                       " a-- gain."]),
            sa=r"R | R | R | R | R | R | R | R | \p <g' bes'>2. | <as' c''>2. | <f' bes'>2( <d' as'>4) |"
               r" <es' g'>2. | \f <g' bes'>4 <as' c''>4 <bes' d''>4 | <bes' es''>2 r4 |"
               r" <as' c''>4 <g' bes'>4 <f' as'>4 | g'4 as'4 bes'4 | <as' c''>4 <bes' d''>4 <c'' es''>4 |"
               r" d''2 bes'4 | <g' es''>2.~ | <g' es''>2.",
            sa2=silent(20, b16="es'2 g'4", b18="bes'4 as'4 f'4"),
            tb=r"R | R | R | R | R | R | R | R | \p <es bes>2. | <as, es>2. | d2( f4) | <es g>2. |"
               r" \f <g, es>4 <as, es>4 <bes, f>4 | <es bes>2 r4 | <as, es>4 <g, es>4 <f, c>4 |"
               r" <es bes>4 <f as>4 <g bes>4 | <as, es>4 <bes, f>4 <c es>4 | <bes, f>2 <bes, d>4 |"
               r" <es bes>2.~ | <es bes>2.",
            tb2=silent(20, b11="bes,2."),
            rh=r"\p <es' g' bes'>2. | <d' f' as'>2. | r4 <es' g' bes'>4 <es' g' bes'>4 | r4 <d' f' as'>4 <d' f' as'>4 |"
               r" r4 <es' g' bes'>4 <es' g' bes'>4 | r4 <es' g' c''>4 <es' g' c''>4 | r4 <f' as' c''>4 <f' as' c''>4 |"
               r" r4 <d' f' bes'>4 <d' f' bes'>4 | r4 <es' g' bes'>4 <es' g' bes'>4 | r4 <es' as' c''>4 <es' as' c''>4 |"
               r" r4 <d' f' as'>4 <d' f' as'>4 | r4 <es' g' bes'>4 <es' g' bes'>4 | r4 <d' f' bes'>4 <d' f' bes'>4 |"
               r" r4 <es' g' bes'>4 <es' g' bes'>4 | r4 <es' as' c''>4 <es' as' c''>4 | r4 <es' g' bes'>4 <es' g' bes'>4 |"
               r" <es' as' c''>4 <d' f' bes'>4 <es' g' c''>4 | r4 <d' f' bes'>4 <d' f' bes'>4 |"
               r" <es' g' bes' es''>2.~ | <es' g' bes' es''>2.",
            lh="es4 bes,4 es4 | bes,4 f4 d4 | es4 r4 r4 | bes,4 r4 r4 | es4 r4 r4 | c4 r4 r4 | f,4 r4 r4 |"
               " bes,4 r4 r4 | es4 r4 r4 | as,4 r4 r4 | bes,4 r4 r4 | es4 r4 r4 | bes,4 r4 r4 | es4 r4 r4 |"
               " as,4 r4 r4 | es4 r4 r4 | as,4 bes,4 c4 | bes,4 r4 r4 | <es, es>2.~ | <es, es>2.",
            words=["@9 Oo__ _ _ _ _ Ring, oh ring out, ring the morn-- ing bell, ring for the day that is new!"]),
    ),
    Source(
        name="spring_procession", title="Spring Procession", key=3, time=(4, 4),
        tempo=("Maestoso", 100), fonts=["Petaluma", "Bravura"], pickup=True,
        hazards=["g: quarter-note pickup (dotted eighth + sixteenth) in every staff but T/B",
                 "g: dotted-eighth/sixteenth rhythms throughout, sixteenth runs and "
                 "broken-chord sixteenths in the piano, piano chords of 3-4 notes",
                 "e: tied chords over the page turn (bars 8-9) in S/A, T/B and both piano staves",
                 "two voices on the S/A staff (bars 2, 5-7, 12, 16) and T/B staff (bars 5, 12)",
                 "f: melisma extenders (bars 6, 14)"],
        systems=[3, 6, 12, 15], pages=[9],
        parts=satb_piano(
            sa=r"\f <cis' e'>8. <cis' e'>16 | <e' a'>4. <e' a'>8 <e' cis''>8. <e' b'>16 <e' a'>4 |"
               r" b'4. gis'8 e'4 e'8. e'16 | <cis' a'>4. <cis' a'>8 <fis' d''>8. <e' cis''>16 <e' b'>4 |"
               r" <e' cis''>4. <d' b'>8 <cis' a'>2 | d''8. cis''16 d''4 cis''8. b'16 cis''4 |"
               r" b'4 a'8. b'16 cis''4( b'4) | e''4 cis''8. a'16 e''4 cis''8. a'16 | <gis' b'>4. <e' gis'>8 <e' a'>2~ |"
               r" <e' a'>2 r4 <cis' e'>8. <cis' e'>16 | <cis' fis'>4. <cis' fis'>8 <e' a'>8. <e' gis'>16 <cis' fis'>4 |"
               r" <fis' a'>4 <e' gis'>4 <cis' e'>2 | e'8. e'16 a'8. a'16 cis''8. cis''16 e''4 |"
               r" <fis' d''>4. <e' cis''>8 <e' b'>4 <e' a'>4 | <gis' b'>2( <a' cis''>4) <gis' b'>4 |"
               r" <e' a'>4. <e' b'>8 <e' cis''>8. <fis' d''>16 <a' e''>4 | fis''4 e''8. d''16 cis''4 b'4 |"
               r" <e' a'>1~ | <e' a'>2 r2",
            sa2=silent(19, first=0, b2="e'2 b4 cis'8. cis'16", b5="fis'2 e'2", b6="d'4 d'4 e'2",
                       b7="a'4 e'4 a'4 e'4", b12="cis'2 e'4 a'4", b16="a'4 a'4 a'4 gis'4"),
            tb=r"r4 | \f <a, cis'>4. <a, cis'>8 <a, cis'>8. <a, d'>16 <a, cis'>4 | <e b>2 <e gis>4 <a, a>4 |"
               r" <a, e>4. <a, e>8 <d a>8. <d a>16 <e gis>4 | <a, e>4. <e gis>8 <a, e>2 | a2 e2 |"
               r" <b, fis>4 <b, fis>4 <e gis>2 | <a, e>2 <cis e>2 | <e b>4. <e b>8 <a, e>2~ | <a, e>2 r2 |"
               r" <fis, cis>2 <e cis>4 <d a>4 | <d a>4 <e b>4 <a, e>2 | cis'2 cis'2 |"
               r" <d a>4. <a, a>8 <e gis>4 <a, e>4 | <e b>2 <e a>4 <e b>4 | <a, e>2 <d fis>4 <cis e>4 |"
               r" <d a>2 <e b>4 <e gis>4 | <a, e>1~ | <a, e>2 r2",
            tb2=silent(19, first=0, b5="d8. d16 d4 a,8. a,16 a,4", b12="a,8. a,16 a,8. a,16 a,8. a,16 a,4"),
            rh=r"\f <gis' b' e''>8. <gis' b' e''>16 | <e' a' cis''>4. <e' a' cis''>8 <e' a' cis''>8. <e' gis' b'>16 <e' a' cis''>4 |"
               r" <e' gis' b'>4. <e' gis' b'>8 gis'16 a' b' gis' <cis' e'>8. <cis' e'>16 |"
               r" <e' a' cis''>4. <e' a' cis''>8 <fis' a' d''>8. <e' a' cis''>16 <e' gis' b'>4 |"
               r" <e' a' cis''>4. <d' e' gis' b'>8 <cis' e' a'>2 | d''16 cis'' b' a' <d' fis' a'>4 cis''16 b' a' gis' <cis' e' a'>4 |"
               r" <d' fis' b'>4 <d' fis' a'>8. <d' fis' b'>16 <e' gis' cis''>4 <e' gis' b'>4 |"
               r" <a' cis'' e''>4 <e' a' cis''>8. <cis' e' a'>16 <a' cis'' e''>4 <e' a' cis''>8. <cis' e' a'>16 |"
               r" <e' gis' b'>4. <e' gis' b'>8 <cis' e' a'>2~ | <cis' e' a'>2 r4 <gis b e'>8. <gis b e'>16 |"
               r" fis'16 a' cis'' a' fis' a' cis'' a' fis' a' cis'' a' fis' a' cis'' a' |"
               r" <d' fis' a'>4 <e' gis' b'>4 <cis' e' a'>2 | a'16 cis'' e'' cis'' a' cis'' e'' cis'' a' cis'' e'' cis'' a'8 cis''8 |"
               r" <fis' a' d''>4. <e' a' cis''>8 <e' gis' b'>4 <cis' e' a'>4 | <e' gis' b'>2 <e' a' cis''>4 <e' gis' b'>4 |"
               r" <cis' e' a'>4. <d' e' b'>8 <e' a' cis''>8. <fis' a' d''>16 <a' cis'' e''>4 |"
               r" fis''16 e'' d'' cis'' <a' cis'' e''>8. <fis' a' d''>16 <e' a' cis''>4 <d' e' gis' b'>4 |"
               r" <cis' e' a' cis''>1~ | <cis' e' a' cis''>2 r2",
            lh="e,4 | <a,, a,>4 <e a>4 <a,, a,>4 <e a>4 | <e, e>4 <b, e gis>4 <e, e>4 <a,, a,>4 |"
               " <a,, a,>4 <e a>4 <d, d>4 <e, e>4 | <a,, a,>4. <e, e>8 <a,, a,>2 |"
               " <d, d>4 <fis a d'>4 <a,, a,>4 <cis e a>4 | <b,, b,>4 <fis b d'>4 <e, e>4 <gis b e'>4 |"
               " <a,, a,>8. a,16 <e a>8. <e a>16 <a,, a,>8. a,16 <e a>8. <e a>16 | <e, e>4. <e, e>8 <a,, a,>2~ |"
               " <a,, a,>2 r4 e4 | <fis,, fis,>2 <e, e>4 <d, d>4 | <d, d>4 <e, e>4 <a,, a,>2 |"
               " <a,, a,>4 <e a>4 <a,, a,>4 <e a>4 | <d, d>4. <a,, a,>8 <e, e>4 <a,, a,>4 |"
               " <e, e>8. b,16 e8. b,16 <e, e>4 <e, e>4 | <a, a>4. <gis, gis>8 <fis, fis>8. <d, d>16 <cis, cis>4 |"
               " <d, d>4 <d fis a>4 <e, e>4 <e gis b d'>4 | <a,, a,>1~ | <a,, a,>2 r2",
            words=["Let the trum-- pets sound for the spring is here, let the ban-- ners fly in the"
                   " morn-- ing clear, march a-- long, march a-- long, sing with a cheer__ _ spring is"
                   " come, spring is come, spring is here! Let the flow-- ers bloom in the fields so"
                   " green, let the ri-- vers run to sea, shine, oh sun, so bright__ _ and warm on all"
                   " of us, spring is come, spring is here!"]),
    ),
]
# fmt: on


# ---------------------------------------------------------------- main


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--only", help="build only this source")
    ap.add_argument("--no-musescore", action="store_true")
    ap.add_argument("--layout", action="store_true", help="print each Verovio system's bars/staves")
    a = ap.parse_args()
    if not shutil.which("rsvg-convert"):
        sys.exit("rsvg-convert (librsvg) is needed: brew install librsvg")
    mscore = None if a.no_musescore else find_mscore()
    if not a.no_musescore and not mscore:
        print("MuseScore 4 not found (set MSCORE=...); skipping its engravings")
    failed = []
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        style = tmp / "letter.mss"
        style.write_text(MSCORE_STYLE)
        for src in SOURCES:
            if a.only and src.name != a.only:
                continue
            build(src)
            xml = to_musicxml(src)
            (HERE / f"{src.name}.musicxml").write_bytes(xml)
            gt = ground_truth(src)
            # The ground truth must agree with what the engravers themselves play.
            vrv_midi = base64.b64decode(toolkit({}, xml).renderToMIDI())
            if (diff := compare(gt, vrv_midi)):
                sys.exit(f"{src.name}: ground truth disagrees with Verovio's MIDI: {diff}")
            cases = []
            for font in src.fonts:
                case = f"{src.name}_vrv_{font.lower()}"
                layout = engrave_verovio(xml, font, HERE / f"{case}.pdf", tmp)
                cases.append(case)
                if a.layout:
                    print(f"{case}:")
                    for p, systems in enumerate(layout, 1):
                        print(f"  page {p}: " + ", ".join(f"bars {f}-{t} ({n} staves)"
                                                          for f, t, n in systems))
            if mscore:
                case = f"{src.name}_mscore"
                src_path = HERE / f"{src.name}.musicxml"
                mid = tmp / f"{case}.mid"
                why = (run_mscore(mscore, ["-S", str(style), "-o", str(HERE / f"{case}.pdf"),
                                           str(src_path)])
                       or run_mscore(mscore, ["-o", str(mid), str(src_path)]))
                if why:
                    failed.append(f"{case}: MuseScore {why}")
                else:
                    if (diff := compare(gt, mid.read_bytes())):
                        sys.exit(f"{src.name}: ground truth disagrees with MuseScore's MIDI: {diff}")
                    cases.append(case)
            for case in cases:
                (HERE / f"{case}.midi").write_bytes(gt)
            print(f"{src.name}: {len(midi_notes(gt))} notes, {src.n} bars -> {', '.join(cases)}")
    for f in failed:
        print("FAILED", f)


if __name__ == "__main__":
    main()
