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
import re
import xml.etree.ElementTree as ET
import zipfile
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


# ---------------------------------------------------------------- sanitize


def sanitize(root: ET.Element, source_name: str | None = None) -> list[str]:
    """Fix what Verovio would misplay or crash on. Returns notes for the user."""
    notes: list[str] = []
    audiveris = is_audiveris(root)
    if source_name:
        _fix_source(root, source_name)
    notes += _octave_shifts(root, audiveris)
    _normalize_repeat_barlines(root)
    notes += _implied_forward_repeats(root)
    notes += _drop_empty_measures(root)
    notes += _tempo_from_words(root)
    notes += _jumps(root)
    notes += _check_keys(root)
    return notes


def _fix_source(root: ET.Element, source_name: str) -> None:
    ident = root.find("identification")
    if ident is None:
        return
    for el in ident.iter():
        if el.text and "sheet2audio-" in el.text:
            el.text = source_name


def _octave_shifts(root: ET.Element, audiveris: bool) -> list[str]:
    """Verovio 6.3 crashes on an 8va/8vb line that is never closed.

    Audiveris writes octave-shift starts without stops, and it writes the
    printed pitch rather than the sounding pitch, so its 8va lines would not
    change the sound correctly anyway. Many are also misreads (scanner specks,
    a dotted metronome mark). For Audiveris output we drop them and say where
    they were; for other files we close any line left open.
    """
    notes = []
    for part in _parts(root):
        measures = part.findall("measure")
        dropped = []
        open_: dict[tuple[str, str], tuple[str, str]] = {}  # (number, staff) -> (size, measure)
        for i, m in enumerate(measures):
            for d in list(m.findall("direction")):
                staff = (d.findtext("staff") or "1").strip()
                for dt in list(d.findall("direction-type")):
                    os_ = dt.find("octave-shift")
                    if os_ is None:
                        continue
                    kind = os_.get("type", "")
                    key = (os_.get("number", "1"), staff)
                    if audiveris:
                        if kind in ("up", "down"):
                            dropped.append(_num(m, i))
                        d.remove(dt)
                    elif kind in ("up", "down"):
                        open_[key] = (os_.get("size", "8"), _num(m, i))
                    elif kind == "stop":
                        open_.pop(key, None)
                if audiveris and d.find("direction-type") is None:
                    m.remove(d)
        for (number, staff), (size, where) in open_.items():
            d = ET.SubElement(measures[-1], "direction")
            dt = ET.SubElement(d, "direction-type")
            ET.SubElement(dt, "octave-shift", {"type": "stop", "number": number, "size": size})
            ET.SubElement(d, "staff").text = staff
            notes.append(f"An 8va/8vb line starting in measure {where} had no end; it now runs to the end.")
        if dropped:
            where = ", ".join(sorted(set(dropped), key=lambda s: (len(s), s)))
            notes.append(
                f"An 8va/8vb sign was read at measure {where}. It was ignored, so those notes play "
                "as printed: if the sign is real they sound an octave off."
            )
    return notes


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


def _implied_forward_repeats(root: ET.Element) -> list[str]:
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
        notes.append(
            f"Measure {_num(measures0[t], t)}: assumed a start-repeat here, so the next end-repeat "
            "goes back to this measure rather than to the beginning."
        )
    return notes


def _has_music(m: ET.Element) -> bool:
    return m.find("note") is not None or m.find("forward") is not None


def _starts_system(m: ET.Element) -> bool:
    p = m.find("print")
    return p is not None and (p.get("new-system") == "yes" or p.get("new-page") == "yes")


def _drop_empty_measures(root: ET.Element) -> list[str]:
    """Audiveris sometimes turns a courtesy key/time signature at the end of a
    line into an extra, empty measure. Played, it is a silent bar, and every
    later measure number is one too high. Remove it when the next measure starts
    a new line and restates a key or time signature; carry its signatures over."""
    parts = _parts(root)
    if not parts:
        return []
    measures0 = parts[0].findall("measure")
    n = len(measures0)
    drop = []
    for i in range(1, n - 1):
        if any(i >= len(p.findall("measure")) or _has_music(p.findall("measure")[i]) for p in parts):
            continue
        nxt = measures0[i + 1]
        restated = any(a.find("key") is not None or a.find("time") is not None
                       for a in nxt.findall("attributes"))
        if _starts_system(nxt) and restated and not _repeat(measures0[i], "right", "backward"):
            drop.append(i)
    if not drop:
        return []
    labels = [_num(measures0[i - 1], i - 1) for i in drop]
    for part in parts:
        ms = part.findall("measure")
        for j, m in enumerate(ms):
            shift = sum(1 for d in drop if d < j)
            if shift and j not in drop and m.get("number", "").isdigit():
                m.set("number", str(int(m.get("number")) - shift))
        for i in reversed(drop):
            empty, nxt = ms[i], ms[i + 1]
            carried = [c for c in empty if c.tag in ("attributes", "direction")]
            for k, c in enumerate(carried):
                nxt.insert(k, c)
            part.remove(empty)
    return [
        f"Removed an empty measure after measure {label} "
        "(OMR made one out of a courtesy signature at the end of a line)."
        for label in labels
    ]


_TEMPO_WORDS = re.compile(r"^(?P<pre>[^\d]{0,40}?)(?P<n>\d{2,3})\s*\)?\s*$")


def _tempo_from_words(root: ET.Element) -> list[str]:
    """Audiveris often reads a metronome mark as plain text ('= 132',
    'Allegro 144)', 'z 100'). Turn such text into a tempo."""
    notes = []
    for part in _parts(root)[:1]:
        for i, m in enumerate(part.findall("measure")):
            for d in m.findall("direction"):
                if d.find("sound") is not None and d.find("sound").get("tempo"):
                    continue
                if d.find(".//metronome") is not None:
                    continue
                text = " ".join((w.text or "") for w in d.iter("words")).strip()
                mt = _TEMPO_WORDS.match(text) if text else None
                if not mt:
                    continue
                bpm = int(mt.group("n"))
                pre = mt.group("pre")
                if not 30 <= bpm <= 300:
                    continue
                if not ("=" in text or "(" in text or ")" in text or len(pre.strip()) <= 2):
                    continue
                snd = d.find("sound")
                if snd is None:
                    snd = ET.SubElement(d, "sound")
                snd.set("tempo", str(bpm))
                notes.append(f"Measure {_num(m, i)}: read the text '{text}' as a tempo of {bpm} BPM.")
    return notes


_DC = re.compile(r"\bD\.\s*C\.|\bda\s*capo\b|\bal\s+fine\b", re.IGNORECASE)
_FINE = re.compile(r"^\s*fine\s*$", re.IGNORECASE)
_DS_CODA = re.compile(r"\bD\.\s*S\.|\bdal\s*segno\b|\bcoda\b|\bsegno\b", re.IGNORECASE)


def _jumps(root: ET.Element) -> list[str]:
    """Make 'D.C. al Fine' playable; warn about D.S./Coda, which we cannot."""
    parts = _parts(root)
    if not parts or any(s.get("dacapo") or s.get("dalsegno") for s in root.iter("sound")):
        return []
    measures = parts[0].findall("measure")
    notes = []
    dc_at = fine_at = None
    for i, m in enumerate(measures):
        for d in m.findall("direction"):
            text = " ".join((w.text or "") for w in d.iter("words"))
            if _DS_CODA.search(text):
                notes.append(f"Measure {_num(m, i)}: a D.S./Coda instruction ('{text.strip()}') was "
                             "read but is not played; the audio plays straight through.")
            elif _DC.search(text) and dc_at is None:
                dc_at = i
            elif _FINE.match(text) and fine_at is None:
                fine_at = i
    if dc_at is None:
        return notes
    if fine_at is None or fine_at >= dc_at:
        fine_at = next((i for i in range(dc_at - 1, 0, -1)
                        if _bar_style(measures[i], "right") in ("light-heavy", "heavy-heavy")), None)
    d = ET.SubElement(measures[dc_at], "direction")
    ET.SubElement(d, "direction-type").append(_words("D.C."))
    ET.SubElement(d, "sound", {"dacapo": "yes"})
    msg = f"Measure {_num(measures[dc_at], dc_at)}: playing the 'D.C.' (back to the beginning)"
    if fine_at is not None:
        d = ET.SubElement(measures[fine_at], "direction")
        ET.SubElement(d, "direction-type").append(_words(""))
        ET.SubElement(d, "sound", {"fine": "yes"})
        msg += f", ending at the Fine in measure {_num(measures[fine_at], fine_at)}"
    notes.append(msg + ". Check that this is right.")
    return notes


def _words(text: str) -> ET.Element:
    w = ET.Element("words")
    w.text = text
    return w


_KEY_NAMES = {-7: "C♭", -6: "G♭", -5: "D♭", -4: "A♭", -3: "E♭", -2: "B♭", -1: "F", 0: "C",
              1: "G", 2: "D", 3: "A", 4: "E", 5: "B", 6: "F♯", 7: "C♯"}


def _check_keys(root: ET.Element) -> list[str]:
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
            notes.append(f"The staves start in different keys ({desc}); one key signature "
                         "was probably misread.")
    return notes


# ---------------------------------------------------------------- movements

_ATTR_ORDER = ["footnote", "level", "divisions", "key", "time", "staves", "part-symbol",
               "instruments", "clef", "staff-details", "transpose", "for-part", "directive",
               "measure-style"]


def split_movements(root: ET.Element) -> list[ET.Element]:
    """Split where one piece ends and another begins on the same page.

    Audiveris starts a new movement only at an indented first line of a page,
    so a page holding several short pieces comes out as one score. A final
    barline followed by a measure that restates the time signature on a new
    line marks the start of the next piece.
    """
    parts = _parts(root)
    measures0 = parts[0].findall("measure")
    cuts = []
    for i in range(2, len(measures0) - 1):
        prev, m = measures0[i - 1], measures0[i]
        restates_time = any(a.find("time") is not None for a in m.findall("attributes"))
        if (_bar_style(prev, "right") in ("light-heavy", "heavy-heavy") and restates_time
                and _starts_system(m) and not _repeat(prev, "right", "backward")
                and (not cuts or i - cuts[-1] >= 2)):
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
                state[(c.tag, c.get("number", ""))] = c
    return state


def _prepend_state(m: ET.Element, state: dict[tuple[str, str], ET.Element]) -> None:
    own = m.find("attributes")
    merged = dict(state)
    if own is not None:
        for c in own:
            merged[(c.tag, c.get("number", ""))] = c
        m.remove(own)
    attrs = ET.Element("attributes")
    for tag in _ATTR_ORDER:
        for (t, _n), el in sorted(merged.items(), key=lambda kv: kv[0][1]):
            if t == tag:
                attrs.append(copy.deepcopy(el))
    idx = 1 if len(m) and m[0].tag == "print" else 0
    m.insert(idx, attrs)


def _renumber_from(measures: list[ET.Element], start: int) -> None:
    n = start
    for m in measures:
        if m.get("number", "").isdigit():
            m.set("number", str(n))
            n += 1


def set_time(root: ET.Element, beats: int, beat_type: int) -> bool:
    """Give the score a time signature if OMR found none. True if one was added."""
    if root.find(".//time") is not None:
        return False
    for part in _parts(root):
        first = part.find("measure")
        attrs = first.find("attributes")
        if attrs is None:
            attrs = ET.Element("attributes")
            _insert_after_header(first, attrs)
        t = ET.Element("time")
        ET.SubElement(t, "beats").text = str(beats)
        ET.SubElement(t, "beat-type").text = str(beat_type)
        children = list(attrs)
        pos = sum(1 for c in children if c.tag in ("footnote", "level", "divisions", "key"))
        attrs.insert(pos, t)
    return True


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

    def short(i):
        return bars[i] is not None and lengths[i] < bars[i] - TOL

    def num(i):
        return _num(measures0[i], i)

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
    # Runs of three or more equally short bars: probably a misread meter.
    i = 0
    while i < n:
        j = i
        while j + 1 < n and short(i) and short(j + 1) and lengths[j + 1] == lengths[i] \
                and bars[j + 1] == bars[i]:
            j += 1
        if short(i) and j - i >= 2:
            exempt.update(range(i, j + 1))
            rep.notes.append(
                f"Measures {num(i)}–{num(j)} all play {_fmt(lengths[i])} beats but the time "
                f"signature says {_fmt(bars[i])}: the time signature may have been misread.")
        i = j + 1

    for i in range(n):
        if bars[i] is not None and lengths[i] > bars[i] + TOL:
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
