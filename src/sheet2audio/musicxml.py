"""Read MusicXML (.mxl / .musicxml / .xml) and repair common OMR rhythm errors.

The most frequent OMR rhythm error is a missed rest: the measure comes out
shorter than its time signature, the music "hiccups" by the missing amount,
and every later measure starts early. `repair_underfull_measures` pads such
measures with rests so the audio keeps the meter. Pickup measures, final
measures and measures split by a mid-measure repeat are left alone.
"""

from __future__ import annotations

import io
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path


def read_musicxml_bytes(path: Path) -> bytes:
    path = Path(path)
    if path.suffix.lower() == ".mxl" or zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            root_name = None
            try:
                container = ET.fromstring(z.read("META-INF/container.xml"))
                rf = container.find(".//{*}rootfile")
                if rf is not None:
                    root_name = rf.get("full-path")
            except KeyError:
                pass
            if root_name is None:
                root_name = next(
                    n
                    for n in z.namelist()
                    if n.lower().endswith((".xml", ".musicxml")) and not n.startswith("META-INF")
                )
            return z.read(root_name)
    return path.read_bytes()


def has_tempo_mark(xml: bytes) -> bool:
    """True if the score states a tempo (a <sound tempo> or a metronome mark)."""
    root = ET.fromstring(xml)
    return any(el.get("tempo") for el in root.iter("sound")) or root.find(".//metronome") is not None


@dataclass
class MeasureReport:
    part: str
    number: str
    index: int
    expected_beats: float  # in quarter notes
    actual_beats: float


@dataclass
class RepairReport:
    padded: list[MeasureReport] = field(default_factory=list)
    overfull: list[MeasureReport] = field(default_factory=list)
    underfull_kept: list[MeasureReport] = field(default_factory=list)
    measures: int = 0

    def summary_lines(self) -> list[str]:
        lines = []
        for m in self.padded:
            lines.append(
                f"measure {m.number}: read as {_fmt(m.actual_beats)} beats but the time signature "
                f"says {_fmt(m.expected_beats)}; padded with a rest (probably a rest OMR missed)"
            )
        for m in self.overfull:
            lines.append(
                f"measure {m.number}: read as {_fmt(m.actual_beats)} beats but the time signature "
                f"says {_fmt(m.expected_beats)}; left as is (check this measure)"
            )
        return lines


def _fmt(x: float) -> str:
    return f"{x:g}"


_NOTE_TYPES = [  # (quarter-length, type name)
    (Fraction(4), "whole"),
    (Fraction(2), "half"),
    (Fraction(1), "quarter"),
    (Fraction(1, 2), "eighth"),
    (Fraction(1, 4), "16th"),
    (Fraction(1, 8), "32nd"),
    (Fraction(1, 16), "64th"),
]


def _decompose(ql: Fraction) -> list[tuple[Fraction, str, bool]]:
    """Split a quarter-length into standard (optionally single-dotted) rest values."""
    out = []
    rem = ql
    while rem > 0:
        for base, name in _NOTE_TYPES:
            dotted = base * Fraction(3, 2)
            if dotted <= rem and base < 4:
                out.append((dotted, name, True))
                rem -= dotted
                break
            if base <= rem:
                out.append((base, name, False))
                rem -= base
                break
        else:  # smaller than a 64th: absorb as an unnamed tiny rest
            out.append((rem, "", False))
            rem = Fraction(0)
    return out


def _int(el: ET.Element | None, default: int = 0) -> int:
    if el is None or el.text is None:
        return default
    try:
        return int(float(el.text.strip()))
    except ValueError:
        return default


class _MeasureTiming:
    """Walk one <measure> and record, per staff and voice, where the music ends
    (in divisions) and which child element holds that voice's last note."""

    def __init__(self, measure: ET.Element):
        self.pos = 0
        self.max_pos = 0
        self.staff_end: dict[str, int] = {}
        # (staff, voice) -> (end in divisions, index of last child, cursor after it)
        self.voice_last: dict[tuple[str, str], tuple[int, int, int]] = {}
        last_start = 0
        for idx, child in enumerate(measure):
            tag = child.tag
            if tag == "note":
                if child.find("grace") is not None:
                    continue
                dur = _int(child.find("duration"))
                is_chord = child.find("chord") is not None
                start = last_start if is_chord else self.pos
                end = start + dur
                if not is_chord:
                    last_start = self.pos
                    self.pos += dur
                staff = (child.findtext("staff") or "1").strip()
                voice = (child.findtext("voice") or "1").strip()
                self.staff_end[staff] = max(self.staff_end.get(staff, 0), end)
                prev = self.voice_last.get((staff, voice))
                if prev is None or end >= prev[0]:
                    self.voice_last[(staff, voice)] = (end, idx, self.pos)
            elif tag == "backup":
                self.pos -= _int(child.find("duration"))
            elif tag == "forward":
                self.pos += _int(child.find("duration"))
                staff = child.findtext("staff")
                if staff:
                    s = staff.strip()
                    self.staff_end[s] = max(self.staff_end.get(s, 0), self.pos)
            self.max_pos = max(self.max_pos, self.pos)


def _time_signature_quarters(time_el: ET.Element) -> Fraction | None:
    if time_el.find("senza-misura") is not None:
        return None
    beats = time_el.findall("beats")
    types = time_el.findall("beat-type")
    if not beats or len(beats) != len(types):
        return None
    total = Fraction(0)
    try:
        for b, t in zip(beats, types):
            num = sum(int(x) for x in (b.text or "").split("+"))
            total += Fraction(num * 4, int(t.text))
    except (ValueError, ZeroDivisionError, TypeError):
        return None
    return total


def repair_underfull_measures(xml: bytes) -> tuple[bytes, RepairReport]:
    root = ET.fromstring(xml)
    report = RepairReport()
    if root.tag != "score-partwise":
        return xml, report  # score-timewise output is rare; leave it untouched
    part_names = {
        sp.get("id"): (sp.findtext("part-name") or sp.get("id") or "?")
        for sp in root.iter("score-part")
    }
    for part in root.findall("part"):
        pname = part_names.get(part.get("id"), part.get("id") or "?")
        measures = part.findall("measure")
        report.measures = max(report.measures, len(measures))
        divisions = 1
        bar_ql: Fraction | None = None
        info = []  # (measure, timing, divisions, bar_ql)
        for m in measures:
            for attr in m.findall("attributes"):
                d = attr.find("divisions")
                if d is not None and d.text:
                    divisions = max(1, _int(d, 1))
                t = attr.find("time")
                if t is not None:
                    bar_ql = _time_signature_quarters(t)
            info.append((m, _MeasureTiming(m), divisions, bar_ql))

        n = len(info)
        lengths = [Fraction(t.max_pos, d) for (_, t, d, _) in info]
        skip = set()
        # A measure split in two by a mid-measure repeat/double bar: two short
        # neighbours that add up to one full bar.
        for i in range(n - 1):
            b = info[i][3]
            if b is None or i in skip:
                continue
            if lengths[i] < b and lengths[i + 1] < b and lengths[i] + lengths[i + 1] == b:
                skip.update({i, i + 1})
        for i, (m, timing, div, bar) in enumerate(info):
            if bar is None:
                continue
            actual = lengths[i]
            rep = MeasureReport(pname, m.get("number", str(i + 1)), i, float(bar), float(actual))
            if actual > bar:
                report.overfull.append(rep)
                continue
            if actual == bar:
                continue
            if i == 0 or i == n - 1 or m.get("implicit") == "yes" or i in skip:
                report.underfull_kept.append(rep)
                continue
            _pad_measure(m, timing, int(bar * div), div)
            report.padded.append(rep)
    out = io.BytesIO()
    ET.ElementTree(root).write(out, encoding="UTF-8", xml_declaration=True)
    return out.getvalue(), report


def _rest(duration: int, type_name: str, dotted: bool, voice: str, staff: str) -> ET.Element:
    note = ET.Element("note")
    ET.SubElement(note, "rest")
    ET.SubElement(note, "duration").text = str(duration)
    ET.SubElement(note, "voice").text = voice
    if type_name:
        ET.SubElement(note, "type").text = type_name
    if dotted:
        ET.SubElement(note, "dot")
    ET.SubElement(note, "staff").text = staff
    return note


def _pad_measure(measure: ET.Element, timing: _MeasureTiming, bar_div: int, divisions: int) -> None:
    """Pad every staff that ends before `bar_div` divisions with rests.

    The rests go right after the last note of that staff's longest voice, the
    way notation programs write them (importers such as Verovio assign a rest
    that appears after a staff change to the wrong staff). The next <backup>
    is lengthened by the same amount so every later element keeps its time.
    `divisions` is the number of divisions per quarter note in this measure.
    """
    plans = []  # (insert-after index, staff, voice, start, deficit)
    for staff, end in timing.staff_end.items():
        if end >= bar_div:
            continue
        cands = [(v, info) for (s, v), info in timing.voice_last.items() if s == staff]
        if not cands:
            continue
        voice, (vend, idx, cursor) = max(cands, key=lambda c: (c[1][0], -int(c[0]) if c[0].isdigit() else 0))
        if cursor != vend:  # durations inconsistent; don't guess
            continue
        plans.append((idx, staff, voice, vend, bar_div - vend))
    if not plans and not timing.staff_end:
        # An empty measure: fill staff 1 with rests.
        plans.append((len(measure) - 1, "1", "1", 0, bar_div - timing.pos))
    for idx, staff, voice, start, deficit in sorted(plans, reverse=True):
        rests = [
            _rest(int(ql * divisions), name, dotted, voice, staff)
            for ql, name, dotted in _decompose(Fraction(deficit, divisions))
            if int(ql * divisions) > 0
        ]
        children = list(measure)
        # Find the next element that moves the cursor.
        nxt = next((c for c in children[idx + 1 :] if c.tag in ("note", "backup", "forward")), None)
        at = idx + 1
        for r in rests:
            measure.insert(at, r)
            at += 1
        if nxt is None:
            continue
        if nxt.tag == "backup":
            d = nxt.find("duration")
            d.text = str(_int(d) + deficit)
        else:
            back = ET.Element("backup")
            ET.SubElement(back, "duration").text = str(deficit)
            measure.insert(at, back)
