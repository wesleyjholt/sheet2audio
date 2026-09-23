"""MusicXML clean-up for OMR output, and repair of bars that play too short.

    sanitize()          importer workarounds and OMR clean-ups; always run
    split_movements()   separate pieces that OMR merged into one score
    set_time()          supply a time signature OMR did not read
    repair()            pad bars that play shorter than their time signature

Bar lengths are measured the way the music is played: Verovio times notes by
their written values (<type>, dots, tuplets), which OMR does not always keep
consistent with <duration>. `repair` therefore takes a callable that returns
the heard length of every bar, and re-measures after padding; a pad that does
not make the bar exactly right is undone.
"""

from __future__ import annotations

import copy
import math
import re
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

TOL = Fraction(1, 64)  # quarter notes


class MusicXMLError(ValueError):
    pass


# ---------------------------------------------------------------- reading


def _raw_bytes(path: Path) -> bytes:
    path = Path(path)
    if path.stat().st_size == 0:
        raise MusicXMLError(f"{path.name} is empty.")
    if path.suffix.lower() == ".mxl" or zipfile.is_zipfile(path):
        try:
            with zipfile.ZipFile(path) as z:
                name = None
                try:
                    rf = ET.fromstring(z.read("META-INF/container.xml")).find(".//{*}rootfile")
                    if rf is not None:
                        name = rf.get("full-path")
                except (KeyError, ET.ParseError):
                    pass
                if name is None:
                    name = next((n for n in z.namelist()
                                 if n.lower().endswith((".xml", ".musicxml"))
                                 and not n.startswith("META-INF")), None)
                if name is None:
                    raise MusicXMLError(f"{path.name} contains no MusicXML file.")
                return z.read(name)
        except zipfile.BadZipFile as e:
            raise MusicXMLError(f"{path.name} is not a valid compressed MusicXML file ({e}).")
    return path.read_bytes()


def parse(data: bytes, name: str = "input") -> ET.Element:
    try:
        root = ET.fromstring(data)  # honours the declared encoding (UTF-8, UTF-16, ...)
    except ET.ParseError as e:
        raise MusicXMLError(f"{name} is not valid XML ({e}).")
    if root.tag == "score-timewise":
        raise MusicXMLError(f"{name} is 'timewise' MusicXML; re-save it from MuseScore as MusicXML.")
    if root.tag != "score-partwise":
        raise MusicXMLError(f"{name} is not MusicXML (its root element is <{root.tag}>).")
    if not any(p.findall("measure") for p in root.findall("part")):
        raise MusicXMLError(f"{name} contains no measures.")
    return root


def read_musicxml(path: Path) -> ET.Element:
    path = Path(path)
    return parse(_raw_bytes(path), path.name)


def read_musicxml_bytes(path: Path) -> bytes:
    """The file's MusicXML, re-encoded as UTF-8."""
    return to_bytes(read_musicxml(path))


def to_bytes(root: ET.Element) -> bytes:
    return ET.tostring(root, encoding="UTF-8", xml_declaration=True)


def has_tempo_mark(root: ET.Element | bytes) -> bool:
    """True if the score states a tempo (a <sound tempo> or a metronome mark)."""
    if isinstance(root, bytes):
        root = ET.fromstring(root)
    return any(el.get("tempo") for el in root.iter("sound")) or root.find(".//metronome") is not None


def is_audiveris(root: ET.Element) -> bool:
    return any("audiveris" in (s.text or "").lower() for s in root.iter("software"))


def title_of(root: ET.Element) -> str | None:
    for path in ("movement-title", "work/work-title"):
        t = (root.findtext(path) or "").strip()
        if t:
            return t
    return None


def _parts(root: ET.Element) -> list[ET.Element]:
    return root.findall("part")


def _num(m: ET.Element, i: int) -> str:
    return m.get("number") or str(i + 1)


def _barlines(m: ET.Element, location: str) -> list[ET.Element]:
    # A barline with no location attribute is a right barline.
    return [b for b in m.findall("barline") if (b.get("location") or "right") == location]


def _repeat(m: ET.Element, location: str, direction: str) -> bool:
    return any((r := b.find("repeat")) is not None and r.get("direction") == direction
               for b in _barlines(m, location))


def _bar_style(m: ET.Element, location: str) -> str | None:
    for b in _barlines(m, location):
        s = b.findtext("bar-style")
        if s:
            return s.strip()
    return None


# ---------------------------------------------------------------- notes about measures

TAG = "s2a-ref"  # temporary attribute: a measure's index before clean-up and splitting


@dataclass
class Note:
    """Something to tell the user. `{m}` in `text` stands for the number of
    the measure tagged `ref`, looked up after the score has been split."""

    text: str
    ref: str | None = None


def tag_measures(root: ET.Element) -> None:
    for part in _parts(root):
        for i, m in enumerate(part.findall("measure")):
            m.set(TAG, str(i))


def untag(root: ET.Element) -> None:
    for m in root.iter("measure"):
        m.attrib.pop(TAG, None)


def resolve_notes(notes: list[Note], pieces: list[ET.Element],
                  titles: list[str]) -> list[str]:
    """Turn notes into text, with measure numbers as they are in each piece
    and, when there are several pieces, the piece's title in front."""
    where: dict[str, tuple[int, str]] = {}
    for k, piece in enumerate(pieces):
        parts = _parts(piece)
        if parts:
            for i, m in enumerate(parts[0].findall("measure")):
                if m.get(TAG) is not None:
                    where.setdefault(m.get(TAG), (k, _num(m, i)))
    out = []
    for n in notes:
        if n.ref is None or n.ref not in where:
            text = n.text.replace("{m}", str(int(n.ref) + 1) if n.ref and n.ref.isdigit() else "?")
            out.append(text)
            continue
        k, label = where[n.ref]
        text = n.text.replace("{m}", label)
        out.append(f"{titles[k]}: {text}" if len(pieces) > 1 else text)
    return list(dict.fromkeys(out))


# ---------------------------------------------------------------- sanitize


def sanitize(root: ET.Element, source_name: str | None = None) -> list[Note]:
    """Fix what Verovio would misplay or crash on. Call tag_measures() first
    so the notes can name measures."""
    notes: list[Note] = []
    audiveris = is_audiveris(root)
    if source_name:
        _fix_source(root, source_name)
    if audiveris:
        notes += _drop_octave_shifts(root)
    notes += _implausible_tempos(root)
    _strip_chord_beams(root)
    if audiveris:
        _renumber_lyrics(root)
    _order_ties(root)
    _normalize_repeat_barlines(root)
    notes += _drop_empty_measures(root)  # before the repeat fix: it moves barlines
    notes += _align_part_barlines(root)
    notes += _fix_part_mapping(root)
    notes += _implied_forward_repeats(root)
    notes += _tempo_from_words(root)
    notes += _jumps(root)
    notes += _check_keys(root)
    return notes


MERGED = "s2a-merged"  # marks measures joined by _align_part_barlines (removed by repair)


def _measure_length(m: ET.Element, div: int) -> Fraction:
    return Fraction(max(_Timing(m).staff_end.values(), default=0), div)


def _align_part_barlines(root: ET.Element) -> list[Note]:
    """OMR sometimes finds a barline on some staves but not on others, so the
    parts disagree about where bars begin. Verovio pairs bars by position, and
    the parts then drift apart. Join bars so that every part has the same bar
    lines (those all parts agree on); the music itself is unchanged."""
    parts = _parts(root)
    if len(parts) < 2:
        return []
    per_part = []
    for p in parts:
        ms = p.findall("measure")
        ctx = _part_context(p)
        starts, t = [], Fraction(0)
        for m, (div, _bar) in zip(ms, ctx):
            starts.append(t)
            length = _measure_length(m, div)
            if length == 0:
                return []  # an empty bar in one part only: too ambiguous to re-bar
            t += length
        per_part.append((ms, ctx, starts))
    # A missed bar line leaves a part with fewer bars but the same total length.
    # Equal bar counts or different totals mean short/long bars (repair's job).
    counts = {len(ms) for ms, _, _ in per_part}
    totals = {sum((_measure_length(m, c[0]) for m, c in zip(ms, ctx)), Fraction(0))
              for ms, ctx, _ in per_part}
    if len(counts) == 1 or len(totals) != 1:
        return []
    common = set.intersection(*(set(s) for _, _, s in per_part))
    if all(set(s) == common for _, _, s in per_part):
        return []
    plans = []  # per part: list of groups (lists of measure indices)
    for ms, ctx, starts in per_part:
        groups: list[list[int]] = []
        for i, s in enumerate(starts):
            if s in common or not groups:
                groups.append([i])
            else:
                groups[-1].append(i)
        plans.append(groups)
    if len({len(g) for g in plans}) != 1:
        return []
    # The same part(s) must have missed the bar line(s) in every group that is
    # joined; if different parts are "short" in different places, the parts
    # are not simply missing bar lines, and joining would move music.
    sides = {frozenset(k for k, groups in enumerate(plans) if len(groups[g]) > 1)
             for g in range(len(plans[0])) if any(len(groups[g]) > 1 for groups in plans)}
    if len(sides) != 1:
        return []
    # Every group that must be joined, in every part, has to be joinable.
    for (ms, ctx, _), groups in zip(per_part, plans):
        for g in groups:
            if len(g) > 1 and not _joinable([ms[i] for i in g], [ctx[i][0] for i in g]):
                return []
    for (ms, ctx, _), groups in zip(per_part, plans):
        for g in groups:
            if len(g) > 1:
                divs = [ctx[i][0] for i in g]
                unit = math.lcm(*divs)  # one division size for the whole joined bar
                lengths = [_measure_length(ms[i], ctx[i][0]) * unit for i in g]
                for i in g:
                    _scale_durations(ms[i], unit // ctx[i][0])
                    for a in ms[i].findall("attributes"):
                        for d in a.findall("divisions"):
                            a.remove(d)
                first = ms[g[0]]
                attrs = first.find("attributes")
                if attrs is None:
                    attrs = ET.Element("attributes")
                    _insert_after_header(first, attrs)
                d = ET.Element("divisions")
                d.text = str(unit)
                attrs.insert(sum(1 for c in attrs if c.tag in ("footnote", "level")), d)
                _join(first, [ms[i] for i in g[1:]], lengths, unit)
                first.set(MERGED, "1")
                nxt = g[-1] + 1
                if nxt < len(ms) and unit != divs[-1] and not any(
                        a.find("divisions") is not None for a in ms[nxt].findall("attributes")):
                    # the bars after it still count in the old division size
                    na = ms[nxt].find("attributes")
                    if na is None:
                        na = ET.Element("attributes")
                        _insert_after_header(ms[nxt], na)
                    dd = ET.Element("divisions")
                    dd.text = str(divs[-1])
                    na.insert(sum(1 for c in na if c.tag in ("footnote", "level")), dd)
    first_ms = per_part[0][0]
    spans = [g for groups in plans for g in groups if len(g) > 1]
    lo, hi = min(g[0] for g in spans), max(g[-1] for g in spans)
    last_label = _num(first_ms[hi], hi) if hi < len(first_ms) else str(hi + 1)
    for (ms, _, _), groups, p in zip(per_part, plans, parts):
        for i in sorted((i for g in groups for i in g[1:]), reverse=True):
            p.remove(ms[i])
    first_nums = [m.get("number") for m in parts[0].findall("measure")]
    for p in parts[1:]:  # every part gets the first part's bar numbers
        for m, n in zip(p.findall("measure"), first_nums):
            if n is not None:
                m.set("number", n)
    return [Note(f"The parts disagreed about bar lines between measures {{m}} and {last_label} "
                 "(OMR); those bars were joined so all parts stay together.", _ref(first_ms[lo]))]


def _joinable(measures: list[ET.Element], divisions: list[int]) -> bool:
    for k, m in enumerate(measures):
        for b in m.findall("barline"):
            loc = b.get("location") or "right"
            interior = (loc == "right" and k < len(measures) - 1) or (loc == "left" and k > 0)
            if interior and (b.find("repeat") is not None or b.find("ending") is not None):
                return False
    return True


def _scale_durations(m: ET.Element, factor: int) -> None:
    """Multiply every duration and offset in a measure (divisions changed)."""
    if factor == 1:
        return
    for el in m.iter():
        if el.tag in ("duration", "offset") and el.text and el.text.strip().lstrip("-").isdigit():
            el.text = str(int(el.text) * factor)


def _join(first: ET.Element, others: list[ET.Element], lengths_div: list[Fraction],
          div: int) -> None:
    """Append `others` to `first`, each starting where the previous one ended."""
    for b in [b for b in first.findall("barline") if (b.get("location") or "right") == "right"]:
        first.remove(b)
    offset = int(lengths_div[0])
    cursor = _Timing(first).pos
    for k, m in enumerate(others):
        # Back to the start of the bar, then forward to where the next bar
        # begins. (Going there directly is the same in MusicXML, but Verovio
        # then files the next notes on the staff it was last reading.)
        for tag, dur in (("backup", cursor), ("forward", offset)):
            if dur > 0:
                el = ET.SubElement(first, tag)
                ET.SubElement(el, "duration").text = str(dur)
        last = k == len(others) - 1
        for c in list(m):
            loc = c.get("location") or "right"
            if c.tag == "print" or (c.tag == "barline" and (loc == "left" or not last)):
                continue
            first.append(c)
        cursor = offset + _Timing(m).pos
        offset += int(lengths_div[k + 1])


def _clef_sign(c: ET.Element) -> str:
    oct_ = (c.findtext("clef-octave-change") or "0").strip()
    return (c.findtext("sign") or "G").strip().upper() + ("8" if oct_ == "-1" else "")


def _part_pitches(measures: list[ET.Element]) -> list[int]:
    steps = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
    out = []
    for m in measures:
        for n in m.findall("note"):
            p = n.find("pitch")
            if p is None:
                continue
            try:
                out.append(12 * (int(p.findtext("octave")) + 1) + steps.get(p.findtext("step"), 0)
                           + round(float(p.findtext("alter") or 0)))
            except (TypeError, ValueError):
                pass
    return out


def _median(xs: list[int]) -> float:
    s = sorted(xs)
    return s[len(s) // 2] if s else 0.0


def _fix_part_mapping(root: ET.Element) -> list[Note]:
    """Choral scores hide the staves of resting voices, and OMR then has to
    guess which part each staff of such a line belongs to. It sometimes puts
    a staff into the wrong part, which then shows a clef change (e.g. the
    children's line landing in the tenor/bass part with a treble clef).

    For each line of music: if a one-staff part changes clef at the start of
    the line, and another one-staff part that normally uses that clef rests
    throughout the line, and the notes fit that part's range better, the
    line's music is moved back to that part."""
    parts = [p for p in _parts(root)
             if all((n.findtext("staff") or "1") == "1" for n in p.iter("note"))]
    if len(parts) < 2:
        return []
    all_parts = _parts(root)
    n = min(len(p.findall("measure")) for p in all_parts)
    starts = [i for i in range(n)
              if any(_starts_system(p.findall("measure")[i]) for p in all_parts)] or [0]
    if starts[0] != 0:
        starts = [0] + starts
    lines = list(zip(starts, starts[1:] + [n]))
    usual = {}
    for p in parts:
        signs = Counter()
        sign = "G"
        for m in p.findall("measure")[:n]:
            for c in m.iter("clef"):
                sign = _clef_sign(c)
            signs[sign] += 1
        usual[id(p)] = signs.most_common(1)[0][0]
    notes = []
    for a, b in lines:
        for p in parts:
            ms = p.findall("measure")
            first_clef = next(iter(ms[a].iter("clef")), None)
            if first_clef is None or _clef_sign(first_clef) == usual[id(p)]:
                continue
            here = _part_pitches(ms[a:b])
            if not here:
                continue
            new_sign = _clef_sign(first_clef)
            for q in parts:
                if q is p or usual[id(q)] != new_sign:
                    continue
                qms = q.findall("measure")
                if _part_pitches(qms[a:b]):
                    continue  # q sings on this line: not a free staff
                elsewhere_p = _part_pitches(ms[:a] + ms[b:])
                elsewhere_q = _part_pitches(qms[:a] + qms[b:])
                if not elsewhere_q or not elsewhere_p:
                    continue
                # Move only when the line clearly lies outside p's range and
                # inside q's (a tenor line in treble clef stays put).
                outside_p = sum(1 for x in here if not min(elsewhere_p) <= x <= max(elsewhere_p))
                inside_q = sum(1 for x in here if min(elsewhere_q) <= x <= max(elsewhere_q))
                if outside_p < 0.8 * len(here) or inside_q < 0.8 * len(here):
                    continue
                for i in range(a, b):
                    pm, qm = ms[i], qms[i]
                    pc, qc = list(pm), list(qm)
                    for c in pc:
                        pm.remove(c)
                    for c in qc:
                        qm.remove(c)
                    qm.extend(pc)
                    pm.extend(qc)
                # The clef change came with the music; p keeps its own clef.
                for c in list(qms[a].iter("clef")):
                    for attr in qms[a].findall("attributes"):
                        if c in list(attr):
                            attr.remove(c)
                notes.append(Note("Measures {m}–" + _num(ms[b - 1], b - 1) + ": the music of one "
                                  "voice had been read into another voice's part (a line with "
                                  "fewer staves); it was moved back.", _ref(ms[a])))
                break
    return notes


def _fix_source(root: ET.Element, source_name: str) -> None:
    ident = root.find("identification")
    if ident is None:
        return
    for el in ident.iter():
        if el.text and "sheet2audio-" in el.text:
            el.text = source_name


def _ref(m: ET.Element) -> str | None:
    return m.get(TAG)


def _drop_octave_shifts(root: ET.Element) -> list[Note]:
    """Verovio 6.3 crashes on an 8va/8vb line that is never closed.

    Audiveris writes octave-shift starts without stops, and it writes the
    printed pitch rather than the sounding pitch, so its 8va lines would not
    change the sound correctly anyway. Many are also misreads (scanner specks,
    a dotted metronome mark). We drop them and say where they were.
    """
    notes = []
    for part in _parts(root):
        for m in part.findall("measure"):
            for d in list(m.findall("direction")):
                found = False
                for dt in list(d.findall("direction-type")):
                    os_ = dt.find("octave-shift")
                    if os_ is None:
                        continue
                    found |= os_.get("type") in ("up", "down")
                    d.remove(dt)
                if d.find("direction-type") is None:
                    m.remove(d)
                if found:
                    notes.append(Note("An 8va/8vb sign was read at measure {m}. It was ignored, "
                                      "so those notes play as printed: if the sign is real they "
                                      "sound an octave off.", _ref(m)))
    return notes


def close_octave_lines(root: ET.Element) -> list[Note]:
    """Close every 8va/8vb line left open at the end of this score (Verovio
    crashes on an open one). Run it on each piece after splitting."""
    notes = []
    for part in _parts(root):
        measures = part.findall("measure")
        open_: dict[tuple[str, str], tuple[str, ET.Element]] = {}
        for m in measures:
            for d in m.findall("direction"):
                staff = (d.findtext("staff") or "1").strip()
                for os_ in d.iter("octave-shift"):
                    key = (os_.get("number", "1"), staff)
                    if os_.get("type") in ("up", "down"):
                        open_[key] = (os_.get("size", "8"), m)
                    elif os_.get("type") == "stop":
                        open_.pop(key, None)
        for (number, staff), (size, start) in open_.items():
            d = ET.SubElement(measures[-1], "direction")
            ET.SubElement(ET.SubElement(d, "direction-type"), "octave-shift",
                          {"type": "stop", "number": number, "size": size})
            ET.SubElement(d, "staff").text = staff
            notes.append(Note("An 8va/8vb line starting in measure {m} had no end; it now runs "
                              "to the end of the piece.", _ref(start)))
    return notes


def _implausible_tempos(root: ET.Element) -> list[Note]:
    """A misread metronome mark ('1oo' for 100) gives tempos like 1 BPM, which
    overflow MIDI's tempo field; audio and highlighting then run at different
    speeds. Remove such marks and tempos."""
    notes = []
    for part in _parts(root):
        for m in part.findall("measure"):
            for d in list(m.findall("direction")):
                bad = []
                for mt in d.iter("metronome"):
                    pm = (mt.findtext("per-minute") or "").strip()
                    try:
                        ok = 20 <= float(pm) <= 400
                    except ValueError:
                        ok = False
                    if not ok:
                        bad.append(pm)
                        for dt in list(d.findall("direction-type")):
                            if dt.find("metronome") is mt:
                                d.remove(dt)
                snd = d.find("sound")
                if snd is not None and snd.get("tempo"):
                    try:
                        ok = 20 <= float(snd.get("tempo")) <= 400
                    except ValueError:
                        ok = False
                    if not ok or bad:
                        bad.append(snd.get("tempo"))
                        del snd.attrib["tempo"]
                        if not snd.attrib:
                            d.remove(snd)
                if bad:
                    if d.find("direction-type") is None and d.find("sound") is None:
                        m.remove(d)
                    notes.append(Note(f"Measure {{m}}: a tempo mark was misread ('{bad[0]}'); "
                                      "it was ignored.", _ref(m)))
        for snd in part.iter("sound"):  # <sound tempo> outside directions
            t = snd.get("tempo")
            if t is not None:
                try:
                    ok = 20 <= float(t) <= 400
                except ValueError:
                    ok = False
                if not ok:
                    del snd.attrib["tempo"]
    return notes


def _strip_chord_beams(root: ET.Element) -> None:
    """A chord's beam belongs to its first note. Audiveris repeats the <beam>
    marks on the other notes of the chord, and Verovio's reader then drops
    most of the beamed notes (28% of one real choral score went missing)."""
    for note in root.iter("note"):
        if note.find("chord") is not None:
            for b in note.findall("beam"):
                note.remove(b)


_LYRIC_LINE_GAP = 12  # tenths of a staff space between lyric lines


def _renumber_lyrics(root: ET.Element) -> None:
    """Number lyric lines by their height on the page, line by line of music:
    Audiveris sometimes gives each syllable of one line its own verse number,
    and each number is then drawn on a line of its own."""
    parts = _parts(root)
    if not parts:
        return
    n = min(len(p.findall("measure")) for p in parts)
    starts = [0] + [i for i in range(1, n) if any(_starts_system(p.findall("measure")[i])
                                                  for p in parts)]
    for part in parts:
        ms = part.findall("measure")
        for a, b in zip(starts, starts[1:] + [len(ms)]):
            lyrics = [ly for m in ms[a:b] for ly in m.iter("lyric")]
            try:
                ys = sorted({float(ly.get("default-y")) for ly in lyrics}, reverse=True)
            except (TypeError, ValueError):
                continue  # some lyric without a position: leave this line alone
            line, last, level = {}, None, 0
            for y in ys:  # top line first
                if last is not None and last - y > _LYRIC_LINE_GAP:
                    level += 1
                line[y] = level + 1
                last = y
            for ly in lyrics:
                ly.set("number", str(line[float(ly.get("default-y"))]))


def _order_ties(root: ET.Element) -> None:
    """In the middle note of a tie chain, Verovio needs the 'stop' tie before
    the 'start' one (MusicXML allows either order); otherwise the chain's last
    note is silent while the page still lights it."""
    for note in root.iter("note"):
        for parent, tag in ((note, "tie"), (note.find("notations"), "tied")):
            if parent is None:
                continue
            ties = parent.findall(tag)
            if len(ties) < 2:
                continue
            first = list(parent).index(ties[0])
            for t in ties:
                parent.remove(t)
            for k, t in enumerate(sorted(ties, key=lambda t: t.get("type") != "stop")):
                parent.insert(first + k, t)


def _normalize_repeat_barlines(root: ET.Element) -> None:
    """Verovio (and MuseScore) only honour a repeat drawn light-heavy (end)
    or heavy-light (start); Audiveris writes a printed ':||:' as light-light."""
    for bl in root.iter("barline"):
        rep = bl.find("repeat")
        if rep is None:
            continue
        want = "light-heavy" if rep.get("direction") == "backward" else "heavy-light"
        bs = bl.find("bar-style")
        if bs is None:
            bs = ET.Element("bar-style")
            bl.insert(0, bs)
        bs.text = want


def _insert_after_header(m: ET.Element, el: ET.Element) -> None:
    idx = 0
    for idx, child in enumerate(list(m)):
        if child.tag not in ("print", "attributes"):
            break
    else:
        idx = len(m)
    m.insert(idx, el)


def _implied_forward_repeats(root: ET.Element) -> list[Note]:
    """'A :| B :|' means A A B B. Without a start-repeat before B, Verovio
    jumps back to the beginning and plays A A B A B."""
    notes = []
    parts = _parts(root)
    if not parts:
        return notes
    measures0 = parts[0].findall("measure")
    last_back = None
    open_fwd = False
    endings_since = False
    targets = []
    for i, m in enumerate(measures0):
        if any(b.find("ending") is not None for b in m.findall("barline")):
            endings_since = True
        if _repeat(m, "left", "forward") or _repeat(m, "right", "forward"):
            open_fwd = True
        if _repeat(m, "right", "backward"):
            if not open_fwd and last_back is not None and not endings_since and last_back + 1 < i:
                targets.append(last_back + 1)
            last_back, open_fwd, endings_since = i, False, False
    for t in targets:
        for part in parts:
            ms = part.findall("measure")
            if t < len(ms):
                bl = ET.Element("barline", {"location": "left"})
                ET.SubElement(bl, "bar-style").text = "heavy-light"
                ET.SubElement(bl, "repeat", {"direction": "forward"})
                _insert_after_header(ms[t], bl)
        notes.append(Note("Measure {m}: assumed a start-repeat here, so the next end-repeat goes "
                          "back to this measure rather than to the beginning.", _ref(measures0[t])))
    return notes


def _has_music(m: ET.Element) -> bool:
    return m.find("note") is not None or m.find("forward") is not None


def _starts_system(m: ET.Element) -> bool:
    p = m.find("print")
    return p is not None and (p.get("new-system") == "yes" or p.get("new-page") == "yes")


def _drop_empty_measures(root: ET.Element) -> list[Note]:
    """Audiveris sometimes turns a courtesy key/time signature at the end of a
    line into an extra, empty measure. Played, it is a silent bar, and every
    later measure number is one too high. Remove it when the next measure starts
    a new line and restates a key or time signature; carry its signatures,
    directions and left barline (a start-repeat) over."""
    parts = _parts(root)
    if not parts:
        return []
    measures0 = parts[0].findall("measure")
    n = len(measures0)
    drop = []
    for i in range(1, n - 1):
        if any(i + 1 >= len(p.findall("measure")) or _has_music(p.findall("measure")[i])
               for p in parts):
            continue
        nxt = measures0[i + 1]
        restated = any(a.find("key") is not None or a.find("time") is not None
                       for a in nxt.findall("attributes"))
        if _starts_system(nxt) and restated and not _repeat(measures0[i], "right", "backward"):
            drop.append(i)
    if not drop:
        return []
    notes = [Note("Removed an empty measure after measure {m} (OMR made one out of a courtesy "
                  "signature at the end of a line).", _ref(measures0[i - 1])) for i in drop]
    for part in parts:
        ms = part.findall("measure")
        for j, m in enumerate(ms):
            shift = sum(1 for d in drop if d < j)
            if shift and j not in drop and m.get("number", "").isdigit():
                m.set("number", str(int(m.get("number")) - shift))
        for i in reversed(drop):
            empty, nxt = ms[i], ms[i + 1]
            carried = [c for c in empty if c.tag in ("attributes", "direction")
                       or (c.tag == "barline" and (c.get("location") or "right") == "left")]
            for k, c in enumerate(carried):
                nxt.insert(k, c)
            part.remove(empty)
    return notes


_TEMPO_WORDS = re.compile(r"^(?P<pre>[^\d]{0,40}?)(?P<n>\d{2,3})\s*\)?\s*$")
_CATALOGUE = re.compile(r"(?:^|[\s(])(?:K|KV|L|D|Hob|BWV|Op|No|N[º°o]|m|mm|bar|bars|Psalm|Ps|p|pp)"
                        r"\.?\s*$|#\s*$", re.IGNORECASE)


def _tempo_from_words(root: ET.Element) -> list[Note]:
    """Audiveris often reads a metronome mark as plain text ('= 132',
    'Allegro 144)', 'z 100'). Turn such text into a tempo, in quarter notes
    per minute: '♩. = 60' is 90."""
    notes = []
    for part in _parts(root)[:1]:
        for m in part.findall("measure"):
            for d in m.findall("direction"):
                snd = d.find("sound")
                if (snd is not None and snd.get("tempo")) or d.find(".//metronome") is not None:
                    continue
                text = " ".join((w.text or "") for w in d.iter("words")).strip()
                mt = _TEMPO_WORDS.match(text) if text else None
                if not mt:
                    continue
                n = int(mt.group("n"))
                pre = mt.group("pre")
                if "=" not in pre and ("(" not in text and ")" not in text
                                       and len(pre.strip()) > 2 or _CATALOGUE.search(pre)):
                    continue
                unit = 1.0
                if re.search(r"\.\s*=", pre):
                    unit = 1.5  # dotted beat
                elif re.search(r"[𝅗𝅥𝅗]\s*=", pre):
                    unit = 2.0  # half note
                elif re.search(r"[♪𝅘𝅥𝅮]\s*=", pre):
                    unit = 0.5  # eighth note
                bpm = round(n * unit)
                if not 30 <= bpm <= 300:
                    continue
                if snd is None:
                    snd = ET.SubElement(d, "sound")
                snd.set("tempo", str(bpm))
                notes.append(Note(f"Measure {{m}}: read the text '{text}' as a tempo of {bpm} "
                                  "quarter notes per minute.", _ref(m)))
    return notes


_DC = re.compile(r"\bD\.?\s*C\.(?:\s|$)|\bda\s*capo\b|^\s*al\s+fine\b", re.IGNORECASE)
_FINE = re.compile(r"^\s*fine\s*$", re.IGNORECASE)
_DS_CODA = re.compile(r"\bD\.\s*S\.|\bdal\s*segno\b|\bcoda\b|\bsegno\b", re.IGNORECASE)


def _jumps(root: ET.Element) -> list[Note]:
    """Make 'D.C. al Fine' playable; warn about D.S./Coda, which we cannot."""
    parts = _parts(root)
    if not parts or any(s.get("dacapo") or s.get("dalsegno") for s in root.iter("sound")):
        return []
    measures = parts[0].findall("measure")
    notes = []
    dc_at = fine_at = None
    al_fine = False
    for i, m in enumerate(measures):
        for d in m.findall("direction"):
            text = " ".join((w.text or "") for w in d.iter("words"))
            if _DS_CODA.search(text):
                notes.append(Note(f"Measure {{m}}: a D.S./Coda instruction ('{text.strip()}') was "
                                  "read but is not played; the audio plays straight through.",
                                  _ref(m)))
            elif _DC.search(text) and dc_at is None:
                dc_at = i
                al_fine = bool(re.search(r"\bfine\b", text, re.IGNORECASE))
            elif _FINE.match(text) and fine_at is None:
                fine_at = i
    if dc_at is None:
        return notes
    if fine_at is not None and fine_at >= dc_at:
        fine_at = None
    if fine_at is None and al_fine:
        # 'al Fine' but the word Fine itself was not read: the Fine is
        # usually at the last final barline (not an end-repeat) before the D.C.
        fine_at = next((i for i in range(dc_at - 1, 0, -1)
                        if _bar_style(measures[i], "right") in ("light-heavy", "heavy-heavy")
                        and not _repeat(measures[i], "right", "backward")), None)
    d = ET.SubElement(measures[dc_at], "direction")
    ET.SubElement(d, "sound", {"dacapo": "yes"})
    text = "Measure {m}: playing the 'D.C.' (back to the beginning)"
    if fine_at is not None:
        d = ET.SubElement(measures[fine_at], "direction")
        ET.SubElement(d, "sound", {"fine": "yes"})
        text += f", ending at the Fine at the end of measure {_num(measures[fine_at], fine_at)}"
    notes.append(Note(text + ". Check that this is right.", _ref(measures[dc_at])))
    return notes


_KEY_NAMES = {-7: "C♭", -6: "G♭", -5: "D♭", -4: "A♭", -3: "E♭", -2: "B♭", -1: "F", 0: "C",
              1: "G", 2: "D", 3: "A", 4: "E", 5: "B", 6: "F♯", 7: "C♯"}


def _check_keys(root: ET.Element) -> list[Note]:
    """Different key signatures on the staves of one piano part are almost
    always a misread."""
    notes = []
    for part in _parts(root):
        first = part.find("measure")
        if first is None:
            continue
        keys = {}
        for k in first.iter("key"):
            fifths = k.findtext("fifths")
            if fifths is not None and fifths.strip().lstrip("-").isdigit():
                keys[k.get("number", "all")] = int(fifths)
        staff_keys = {n: f for n, f in keys.items() if n != "all"}
        if len(set(staff_keys.values())) > 1:
            desc = " vs ".join(f"{_KEY_NAMES.get(f, f)} major (staff {n})"
                               for n, f in sorted(staff_keys.items()))
            notes.append(Note(f"The staves start in different keys ({desc}); one key signature "
                              "was probably misread."))
    return notes


# ---------------------------------------------------------------- movements

# Attributes that stay in force until changed. (measure-style, directive and
# footnote apply only where they are written, so they are not carried over.)
_ATTR_ORDER = ["divisions", "key", "time", "staves", "part-symbol", "instruments", "clef",
               "staff-details", "transpose", "for-part"]


def split_movements(root: ET.Element) -> list[ET.Element]:
    """Split where one piece ends and another begins on the same page.

    Audiveris starts a new movement only at an indented first line of a page,
    so a page holding several short pieces comes out as one score. A final
    barline followed by a measure that restates the time signature on a new
    line marks the start of the next piece, unless a D.C. after it points back
    to a Fine before it (then it is one piece in several sections).
    """
    parts = _parts(root)
    measures0 = parts[0].findall("measure")
    fine = [i for i, m in enumerate(measures0)
            if any(s.get("fine") == "yes" for s in m.iter("sound"))]
    dacapo = [i for i, m in enumerate(measures0)
              if any(s.get("dacapo") == "yes" or s.get("dalsegno") for s in m.iter("sound"))]
    cuts = []
    for i in range(2, len(measures0) - 1):
        prev, m = measures0[i - 1], measures0[i]
        restates_time = any(a.find("time") is not None for a in m.findall("attributes"))
        spans_jump = any(f < i for f in fine) and any(d >= i for d in dacapo)
        if (_bar_style(prev, "right") in ("light-heavy", "heavy-heavy") and restates_time
                and _starts_system(m) and not _repeat(prev, "right", "backward")
                and not spans_jump and (not cuts or i - cuts[-1] >= 2)):
            cuts.append(i)
    if not cuts:
        return [root]
    bounds = [0, *cuts, len(measures0)]
    pieces = []
    for k in range(len(bounds) - 1):
        lo, hi = bounds[k], bounds[k + 1]
        new = copy.deepcopy(root)
        for part, new_part in zip(parts, _parts(new)):
            ms = part.findall("measure")
            state = _attribute_state(ms[:lo])
            for m in new_part.findall("measure"):
                new_part.remove(m)
            chunk = [copy.deepcopy(m) for m in ms[lo:hi]]
            if lo and chunk:
                _prepend_state(chunk[0], state)
                if _looks_like_pickup(chunk[0]):
                    chunk[0].set("implicit", "yes")
                _renumber_from(chunk, 0 if chunk[0].get("implicit") == "yes" else 1)
            new_part.extend(chunk)
        pieces.append(new)
    return pieces


def _looks_like_pickup(m: ET.Element) -> bool:
    """True if the <duration>s of this measure (which carries its own
    divisions and time signature) add up to less than a bar."""
    a = m.find("attributes")
    if a is None or a.find("time") is None:
        return False
    bar = _time_quarters(a.find("time"))
    div = max(1, _int(a.find("divisions"), 1))
    t = _Timing(m)
    return bar is not None and 0 < max(t.staff_end.values(), default=0) < bar * div


def _attribute_state(measures: list[ET.Element]) -> dict[tuple[str, str], ET.Element]:
    state: dict[tuple[str, str], ET.Element] = {}
    for m in measures:
        for a in m.findall("attributes"):
            for c in a:
                if c.tag in _ATTR_ORDER:
                    state[(c.tag, c.get("number", ""))] = c
    return state


def _prepend_state(m: ET.Element, state: dict[tuple[str, str], ET.Element]) -> None:
    own = m.find("attributes")
    merged = dict(state)
    extra = []
    if own is not None:
        for c in own:
            if c.tag in _ATTR_ORDER:
                merged[(c.tag, c.get("number", ""))] = c
            else:
                extra.append(c)
        m.remove(own)
    attrs = ET.Element("attributes")
    for tag in _ATTR_ORDER:
        for (t, _n), el in sorted(merged.items(), key=lambda kv: kv[0][1]):
            if t == tag:
                attrs.append(copy.deepcopy(el))
    attrs.extend(extra)
    idx = 1 if len(m) and m[0].tag == "print" else 0
    m.insert(idx, attrs)


def _renumber_from(measures: list[ET.Element], start: int) -> None:
    n = start
    for m in measures:
        if m.get("number", "").isdigit():
            m.set("number", str(n))
            n += 1


# ---------------------------------------------------------------- keys

_SHARPS = "FCGDAEB"
_ACCIDENTAL_ALTER = {"sharp": 1, "flat": -1, "natural": 0, "double-sharp": 2,
                     "sharp-sharp": 2, "flat-flat": -2, "natural-sharp": 1,
                     "natural-flat": -1}
_KEY_NAMES_IN = {"C": 0, "G": 1, "D": 2, "A": 3, "E": 4, "B": 5, "F#": 6, "C#": 7,
                 "F": -1, "BB": -2, "EB": -3, "AB": -4, "DB": -5, "GB": -6, "CB": -7}


def parse_key(text: str) -> int:
    """'C', 'Bb', 'F#', 'Am' (minor), 'Ebm' or a number of fifths ('-3')."""
    t = text.strip().replace("♭", "b").replace("♯", "#")
    if re.fullmatch(r"[+-]?\d", t) and -7 <= int(t) <= 7:
        return int(t)
    m = re.fullmatch(r"([A-Ga-g])([b#]?)(m|min|minor)?", t)
    fifths = m and _KEY_NAMES_IN.get(m.group(1).upper() + m.group(2).upper())
    if fifths is None:
        raise ValueError(f"unknown key '{text}' (use e.g. C, Bb, F#, Am or -2)")
    return fifths - 3 if m.group(3) else fifths


def _key_alter(step: str, fifths: int) -> int:
    if fifths > 0 and step in _SHARPS[:fifths]:
        return 1
    if fifths < 0 and step in _SHARPS[::-1][:-fifths]:
        return -1
    return 0


def _key_in_effect(measures: list[ET.Element], index: int) -> int | None:
    fifths = None
    for m in measures[: index + 1]:
        for k in m.iter("key"):
            f = k.findtext("fifths")
            if f is not None and f.strip().lstrip("-").isdigit():
                fifths = int(f)
    return fifths


def _respell(measures: list[ET.Element], fifths: int) -> None:
    """Spell every note from the key signature and the accidentals printed
    before it in its bar (as notation defines them). A tied-over note takes
    the pitch of the note it is tied from; one tied from before `measures`
    keeps its pitch."""
    tied: dict[tuple[str, str, str], int] = {}  # pitch of the latest tie start
    for m in measures:
        carried: dict[tuple[str, str, str], int] = {}
        for n in m.findall("note"):
            p = n.find("pitch")
            if p is None:
                continue
            step, octave = (p.findtext("step") or "").strip(), (p.findtext("octave") or "").strip()
            where = ((n.findtext("staff") or "1").strip(), step, octave)
            acc = n.find("accidental")
            ties = {t.get("type") for t in n.findall("tie")}
            if acc is not None and (acc.text or "").strip() in _ACCIDENTAL_ALTER:
                alter = _ACCIDENTAL_ALTER[acc.text.strip()]
                carried[where] = alter
            elif "stop" in ties:
                if where not in tied:
                    continue
                alter = tied[where]
            else:
                alter = carried.get(where, _key_alter(step, fifths))
            if "start" in ties:
                tied[where] = alter
            elif "stop" in ties:
                tied.pop(where, None)
            el = p.find("alter")
            if alter == 0:
                if el is not None:
                    p.remove(el)
            else:
                if el is None:
                    el = ET.Element("alter")
                    p.insert(list(p).index(p.find("step")) + 1 if p.find("step") is not None else 0, el)
                el.text = str(alter)


def set_key(root: ET.Element, index: int, fifths: int) -> bool:
    """Make `fifths` the key from measure `index` (0-based) on, in every part,
    and re-spell the notes until the next key change. False if the parts
    are in different keys there (transposing instruments) or it is already
    that key."""
    parts = _parts(root)
    current = {_key_in_effect(p.findall("measure"), index) for p in parts}
    if len(current) > 1 or current == {fifths}:
        return False
    for part in parts:
        ms = part.findall("measure")
        if index >= len(ms):
            continue
        m = ms[index]
        attrs = m.find("attributes")
        if attrs is None:
            attrs = ET.Element("attributes")
            _insert_after_header(m, attrs)
        for k in attrs.findall("key"):
            attrs.remove(k)
        key = ET.Element("key")
        ET.SubElement(key, "fifths").text = str(fifths)
        attrs.insert(sum(1 for c in attrs if c.tag in ("footnote", "level", "divisions")), key)
        end = next((j for j in range(index + 1, len(ms))
                    if any(True for _ in ms[j].iter("key"))), len(ms))
        _respell(ms[index:end], fifths)
    return True


def apply_book_keys(roots: list[ET.Element], keys) -> list[list[Note]]:
    """Put back key changes that Audiveris recognised but left out of its
    MusicXML (`keys` from omr.book_keys). Audiveris starts a new page of
    its own at each movement, so pages here are the first bar of each
    movement and every bar marked new-page."""
    pages: list[tuple[int, int, int]] = []  # (movement, first bar, bar after the last)
    for r, root in enumerate(roots):
        parts = _parts(root)
        if not parts:
            continue
        ms = parts[0].findall("measure")
        starts = [i for i, m in enumerate(ms) if i == 0 or any(
            pr.get("new-page") == "yes" for pr in m.findall("print"))]
        pages += [(r, a, b) for a, b in zip(starts, starts[1:] + [len(ms)])]
    notes: list[list[Note]] = [[] for _ in roots]
    for k in sorted(keys, key=lambda k: (k.page, k.measure)):
        if k.page >= len(pages):
            continue
        r, first, end = pages[k.page]
        root, index = roots[r], first + k.measure
        parts = _parts(root)
        ms = parts[0].findall("measure")
        if index >= end or _key_in_effect(ms, index) is None:
            continue
        # Only where the export has no key at all: a key it did write wins.
        if any(any(True for _ in p.findall("measure")[index].iter("key"))
               for p in parts if index < len(p.findall("measure"))):
            continue
        if set_key(root, index, k.fifths):
            notes[r].append(Note(f"Measure {{m}}: the key changes to {key_label(k.fifths)} here; "
                              "Audiveris recognised it but left it out of its export, so it was "
                              "put back and the notes after it re-spelled.", _ref(ms[index])))
    return notes


def set_key_at(roots: list[ET.Element], number: str, fifths: int) -> str:
    """Set the key from the first bar numbered `number` (as printed); returns
    'set', 'same' (already that key), 'mixed' (parts in different keys) or
    'missing'."""
    for root in roots:
        parts = _parts(root)
        if not parts:
            continue
        for i, m in enumerate(parts[0].findall("measure")):
            if m.get("number", "").strip() == number:
                if set_key(root, i, fifths):
                    return "set"
                keys = {_key_in_effect(p.findall("measure"), i) for p in parts}
                return "same" if keys == {fifths} else "mixed"
    return "missing"


def key_label(fifths: int) -> str:
    """-2 -> 'B♭ major / G minor'."""
    majors = "C♭ G♭ D♭ A♭ E♭ B♭ F C G D A E B F♯ C♯".split()
    minors = "A♭ E♭ B♭ F C G D A E B F♯ C♯ G♯ D♯ A♯".split()
    return f"{majors[fifths + 7]} major / {minors[fifths + 7]} minor"


def set_time(root: ET.Element, beats: int, beat_type: int) -> bool:
    """Give the score a time signature if OMR found none. True if one was added."""
    if root.find(".//time") is not None:
        return False
    added = False
    for part in _parts(root):
        first = part.find("measure")
        if first is None:
            continue
        attrs = first.find("attributes")
        if attrs is None:
            attrs = ET.Element("attributes")
            _insert_after_header(first, attrs)
        t = ET.Element("time")
        ET.SubElement(t, "beats").text = str(beats)
        ET.SubElement(t, "beat-type").text = str(beat_type)
        pos = sum(1 for c in attrs if c.tag in ("footnote", "level", "divisions", "key"))
        attrs.insert(pos, t)
        added = True
    return added


# ---------------------------------------------------------------- repair


@dataclass
class RepairReport:
    notes: list[str] = field(default_factory=list)
    padded: list[str] = field(default_factory=list)
    unfixed: list[str] = field(default_factory=list)
    overfull: list[str] = field(default_factory=list)
    measures: int = 0


_NOTE_TYPES = [
    (Fraction(4), "whole"), (Fraction(2), "half"), (Fraction(1), "quarter"),
    (Fraction(1, 2), "eighth"), (Fraction(1, 4), "16th"), (Fraction(1, 8), "32nd"),
    (Fraction(1, 16), "64th"),
]


def _decompose(ql: Fraction) -> list[tuple[Fraction, str, bool]]:
    """Split a length in quarter notes into standard rest values (at most one dot)."""
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
        else:
            return []  # not expressible with standard values
    return out


def _int(el: ET.Element | None, default: int = 0) -> int:
    if el is None or el.text is None:
        return default
    try:
        return int(float(el.text.strip()))
    except ValueError:
        return default


class _Timing:
    """Where each staff and voice of one <measure> ends, by <duration>."""

    def __init__(self, measure: ET.Element):
        self.pos = 0
        self.staff_end: dict[str, int] = {}
        # (staff, voice) -> (end, index of last child, cursor after it)
        self.voice_last: dict[tuple[str, str], tuple[int, int, int]] = {}
        last_start = 0
        for idx, child in enumerate(measure):
            if child.tag == "note":
                if child.find("grace") is not None or child.find("cue") is not None:
                    continue
                dur = _int(child.find("duration"))
                chord = child.find("chord") is not None
                start = last_start if chord else self.pos
                end = start + dur
                if not chord:
                    last_start = self.pos
                    self.pos += dur
                staff = (child.findtext("staff") or "1").strip()
                voice = (child.findtext("voice") or "1").strip()
                self.staff_end[staff] = max(self.staff_end.get(staff, 0), end)
                prev = self.voice_last.get((staff, voice))
                if prev is None or end >= prev[0]:
                    self.voice_last[(staff, voice)] = (end, idx, self.pos)
            elif child.tag == "backup":
                self.pos -= _int(child.find("duration"))
            elif child.tag == "forward":
                self.pos += _int(child.find("duration"))


def _time_quarters(time_el: ET.Element) -> Fraction | None:
    if time_el.find("senza-misura") is not None:
        return None
    beats, types = time_el.findall("beats"), time_el.findall("beat-type")
    if not beats or len(beats) != len(types):
        return None
    total = Fraction(0)
    try:
        for b, t in zip(beats, types):
            total += Fraction(sum(int(x) for x in (b.text or "").split("+")) * 4, int(t.text))
    except (ValueError, ZeroDivisionError, TypeError):
        return None
    return total or None


def _part_context(part: ET.Element) -> list[tuple[int, Fraction | None]]:
    """(divisions, bar length in quarters) in effect in each measure."""
    out = []
    div, bar = 1, None
    for m in part.findall("measure"):
        for a in m.findall("attributes"):
            d = a.find("divisions")
            if d is not None and d.text:
                div = max(1, _int(d, 1))
            t = a.find("time")
            if t is not None:
                bar = _time_quarters(t)
        out.append((div, bar))
    return out


def _rest(duration: int, type_name: str, dotted: bool, voice: str, staff: str) -> ET.Element:
    note = ET.Element("note")
    ET.SubElement(note, "rest")
    ET.SubElement(note, "duration").text = str(duration)
    ET.SubElement(note, "voice").text = voice
    ET.SubElement(note, "type").text = type_name
    if dotted:
        ET.SubElement(note, "dot")
    ET.SubElement(note, "staff").text = staff
    return note


def _append_rests(measure: ET.Element, after_idx: int, amount_q: Fraction, divisions: int,
                  voice: str, staff: str) -> bool:
    """Insert rests totalling `amount_q` after child `after_idx`, and lengthen
    the next <backup> (or add one) so later elements keep their times."""
    pieces = _decompose(amount_q)
    durs = [ql * divisions for ql, _, _ in pieces]
    if not pieces or any(d.denominator != 1 or d <= 0 for d in durs):
        return False
    total = int(sum(durs))
    children = list(measure)
    nxt = next((c for c in children[after_idx + 1:] if c.tag in ("note", "backup", "forward")), None)
    at = after_idx + 1
    for (ql, name, dotted), d in zip(pieces, durs):
        measure.insert(at, _rest(int(d), name, dotted, voice, staff))
        at += 1
    if nxt is not None:
        if nxt.tag == "backup":
            dur = nxt.find("duration")
            dur.text = str(_int(dur) + total)
        else:
            back = ET.Element("backup")
            ET.SubElement(back, "duration").text = str(total)
            measure.insert(at, back)
    return True


def _pad_by_duration(measure: ET.Element, divisions: int, bar_q: Fraction) -> bool:
    """Pad every staff whose <duration>s end before the bar line."""
    t = _Timing(measure)
    bar_div = bar_q * divisions
    if bar_div.denominator != 1:
        return False
    plans = []
    for staff, end in t.staff_end.items():
        if end >= bar_div:
            continue
        cands = [(v, info) for (s, v), info in t.voice_last.items() if s == staff]
        if not cands:
            continue
        voice, (vend, idx, cursor) = max(
            cands, key=lambda c: (c[1][0], -int(c[0]) if c[0].isdigit() else 0))
        if cursor != vend:
            continue
        plans.append((idx, staff, voice, Fraction(int(bar_div) - vend, divisions)))
    if not t.staff_end:  # nothing at all in the bar: one full-bar rest on staff 1
        plans.append((len(measure) - 1, "1", "1", bar_q))
    done = False
    for idx, staff, voice, amount in sorted(plans, reverse=True):
        done |= _append_rests(measure, idx, amount, divisions, voice, staff)
    return done


def _pad_by_heard(measure: ET.Element, divisions: int, amount_q: Fraction) -> bool:
    """Pad each staff's longest voice by `amount_q` (the heard shortfall)."""
    t = _Timing(measure)
    done = False
    plans = []
    for staff in t.staff_end:
        cands = [(v, info) for (s, v), info in t.voice_last.items() if s == staff]
        if cands:
            voice, (_, idx, _) = max(cands, key=lambda c: c[1][0])
            plans.append((idx, staff, voice))
    for idx, staff, voice in sorted(plans, reverse=True):
        done |= _append_rests(measure, idx, amount_q, divisions, voice, staff)
    return done


def _fmt(q: Fraction) -> str:
    return f"{float(q):g}"


def _beat_type(part: ET.Element, index: int) -> int:
    bt = 4
    for m in part.findall("measure")[: index + 1]:
        for t in m.iter("time"):
            try:
                bt = int((t.findtext("beat-type") or "4").split("+")[0])
            except ValueError:
                pass
    return bt


def _meter_runs(lengths: list[Fraction], bars: list[Fraction | None],
                skip: set[int]) -> list[tuple[int, int]]:
    """Runs of 3+ consecutive bars that all play the same length, all
    different from their (shared) time signature."""
    runs, i, n = [], 0, len(lengths)
    while i < n:
        j = i
        if bars[i] is not None and abs(lengths[i] - bars[i]) > TOL and lengths[i] > 0 \
                and i not in skip:
            while (j + 1 < n and j + 1 not in skip and bars[j + 1] == bars[i]
                   and lengths[j + 1] == lengths[i]):
                j += 1
            if j - i >= 2:
                runs.append((i, j))
        i = j + 1
    return runs


def _set_time_at(parts: list[ET.Element], index: int, beats: int, beat_type: int) -> None:
    for part in parts:
        ms = part.findall("measure")
        if index >= len(ms):
            continue
        m = ms[index]
        attrs = m.find("attributes")
        if attrs is None:
            attrs = ET.Element("attributes")
            _insert_after_header(m, attrs)
        t = attrs.find("time")
        if t is None:
            t = ET.Element("time")
            pos = sum(1 for c in attrs if c.tag in ("footnote", "level", "divisions", "key"))
            attrs.insert(pos, t)
        for c in list(t):
            t.remove(c)
        ET.SubElement(t, "beats").text = str(beats)
        ET.SubElement(t, "beat-type").text = str(beat_type)


def repair(root: ET.Element, heard: Callable[[ET.Element], list[Fraction]],
           apply: bool = True) -> RepairReport:
    """Check every bar's played length against its time signature; pad the
    ones that are short because OMR missed a rest.

    Left alone: pickups (and the matching short last bar), the halves of a
    bar split by a repeat, short bars at repeats that complete a pickup,
    section starts/ends at a final barline, and runs of equally short bars
    (more likely a misread time signature than missed rests).
    """
    rep = RepairReport()
    parts = _parts(root)
    measures0 = parts[0].findall("measure")
    n = len(measures0)
    rep.measures = n
    ctx0 = _part_context(parts[0])
    bars = [b for _, b in ctx0]
    if all(b is None for b in bars):
        rep.notes.append("No time signature was recognised, so bar lengths were not checked "
                         "(set one with --time, e.g. --time 3/4).")
        return rep
    lengths = heard(root)
    if len(lengths) != n:
        return rep  # measures could not be matched up; do nothing rather than guess

    def num(i):
        return _num(measures0[i], i)

    merged = {i for i, m in enumerate(measures0) if m.get(MERGED)}
    for m in root.iter("measure"):
        m.attrib.pop(MERGED, None)

    # Runs of equally long bars that disagree with the time signature: a
    # missed time signature (e.g. the return to 4/4 after one 2/4 bar).
    for i, j in _meter_runs(lengths, bars, merged):
        beat_type = _beat_type(parts[0], i)
        beats = lengths[i] * beat_type / 4
        label = f"{num(i)}–{num(j)}"
        gap = bars[i] - lengths[i]
        # Other bars short by the same amount: these are missed rests, not a
        # missed time signature; let the padding below handle them.
        elsewhere = [k for k in range(1, n - 1) if not i <= k <= j
                     and bars[k] is not None and bars[k] - lengths[k] == gap]
        if gap > 0 and elsewhere:
            continue
        if apply and beats.denominator == 1 and 1 <= beats <= 32:
            _set_time_at(parts, i, int(beats), beat_type)
            back = bars[i] * beat_type / 4
            own_time = j + 1 < n and any(a.find("time") is not None
                                         for a in measures0[j + 1].findall("attributes"))
            if j + 1 < n and back.denominator == 1 and not own_time:
                _set_time_at(parts, j + 1, int(back), beat_type)  # and back again
            rep.notes.append(f"Measures {label} all play {_fmt(lengths[i])} beats but the time "
                             f"signature said {_fmt(bars[i])}: a time signature was probably "
                             f"missed; using {int(beats)}/{beat_type} from measure {num(i)}.")
        else:
            rep.notes.append(f"Measures {label} all play {_fmt(lengths[i])} beats but the time "
                             f"signature says {_fmt(bars[i])}: the time signature may have been "
                             "misread.")
    bars = [b for _, b in _part_context(parts[0])]

    def short(i):
        return bars[i] is not None and lengths[i] < bars[i] - TOL


    exempt: set[int] = set()
    kept: list[str] = []
    pickup = short(0) and (measures0[0].get("implicit") == "yes" or measures0[0].get("number") == "0"
                           or (short(n - 1) and abs(lengths[0] + lengths[n - 1] - bars[0]) <= TOL))
    if pickup:
        exempt.update({0, n - 1})
    exempt.add(n - 1)  # a short final bar is harmless
    for i in range(n):
        m = measures0[i]
        if m.get("implicit") == "yes":
            exempt.add(i)
        if i > 0 and _bar_style(measures0[i - 1], "right") in ("light-heavy", "heavy-heavy") \
                and not _repeat(measures0[i - 1], "right", "backward"):
            exempt.add(i)  # first bar of a new section (may be a pickup)
        if i < n - 1 and _bar_style(m, "right") in ("light-heavy", "heavy-heavy") \
                and not _repeat(m, "right", "backward"):
            exempt.add(i)  # last bar of a section
    # A bar split in two (mid-bar repeat or double bar): two short neighbours
    # with the same meter adding up to one bar, with a barline between them.
    for i in range(n - 1):
        if short(i) and short(i + 1) and bars[i] == bars[i + 1] \
                and abs(lengths[i] + lengths[i + 1] - bars[i]) <= TOL \
                and (_barlines(measures0[i], "right") or _barlines(measures0[i + 1], "left")
                     or measures0[i + 1].get("implicit") == "yes"):
            exempt.update({i, i + 1})
    # Short bars at repeats that complete the pickup of the repeated section.
    fwd = 0
    for i in range(n):
        m = measures0[i]
        if _repeat(m, "left", "forward"):
            fwd = i
        at_repeat = (_repeat(m, "right", "backward") or _repeat(m, "left", "forward")
                     or any(b.find("ending") is not None for b in m.findall("barline")))
        if at_repeat and short(i) and i not in exempt:
            if short(fwd) and abs(lengths[i] + lengths[fwd] - bars[i]) <= TOL:
                exempt.add(i)
            elif pickup:
                exempt.add(i)
                kept.append(num(i))
        if _repeat(m, "right", "forward"):
            fwd = i + 1
    for i in range(n):
        if bars[i] is not None and lengths[i] > bars[i] + TOL and i not in merged:
            rep.overfull.append(num(i))
            rep.notes.append(f"Measure {num(i)} plays {_fmt(lengths[i])} beats in a "
                             f"{_fmt(bars[i])}-beat bar; check it.")
    for k in kept:
        rep.notes.append(f"Measure {k} (at a repeat) is shorter than a full bar; left as is "
                         "because the piece starts with a pickup. Check it.")

    todo = [i for i in range(n) if short(i) and i not in exempt]
    if not todo:
        return rep
    if not apply:
        for i in todo:
            rep.unfixed.append(num(i))
            rep.notes.append(f"Measure {num(i)} plays {_fmt(lengths[i])} of {_fmt(bars[i])} beats "
                             "(not repaired: --no-repair).")
        return rep

    contexts = [_part_context(p) for p in parts]
    originals = {i: [copy.deepcopy(p.findall("measure")[i]) if i < len(p.findall("measure")) else None
                     for p in parts] for i in todo}

    def restore(i):
        for p, orig in zip(parts, originals[i]):
            ms = p.findall("measure")
            if orig is not None and i < len(ms):
                cur = ms[i]
                cur.clear()
                cur.attrib.update(orig.attrib)
                cur.extend(list(copy.deepcopy(orig)))

    for i in todo:
        for p, ctx in zip(parts, contexts):
            ms = p.findall("measure")
            if i < len(ms) and ctx[i][1] is not None:
                _pad_by_duration(ms[i], ctx[i][0], ctx[i][1])
    after = heard(root)
    retry = [i for i in todo if len(after) != n or abs(after[i] - bars[i]) > TOL]
    for i in retry:
        restore(i)
        shortfall = bars[i] - lengths[i]
        for p, ctx in zip(parts, contexts):
            ms = p.findall("measure")
            if i < len(ms):
                _pad_by_heard(ms[i], ctx[i][0], shortfall)
    if retry:
        after = heard(root)
    for i in todo:
        ok = len(after) == n and abs(after[i] - bars[i]) <= TOL
        if ok:
            rep.padded.append(num(i))
            rep.notes.append(f"Measure {num(i)}: played {_fmt(lengths[i])} of {_fmt(bars[i])} beats; "
                             "added a rest at the end (probably a rest the OMR missed).")
        else:
            restore(i)
            rep.unfixed.append(num(i))
            rep.notes.append(f"Measure {num(i)} plays {_fmt(lengths[i])} of {_fmt(bars[i])} beats "
                             "and could not be repaired automatically; check it.")
    return rep
