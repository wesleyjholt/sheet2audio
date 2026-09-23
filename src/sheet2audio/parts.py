"""Split a score into the parts a singer or player can switch on and off.

Works on Verovio's MEI (the same document the page is drawn from, so note ids
match the SVG):

* every staff group is classed as voice, accompaniment or other instrument,
  from its label, its MIDI instrument, whether it has lyrics, and words such
  as "Children only" written on it;
* a voice staff that carries two parts (Soprano and Alto on one staff, mostly
  as two-note chords) is split into an upper and a lower part: in chords the
  top note is the upper part's and the bottom note the lower part's, a single
  note is sung by both, and where two voices (layers) are written, the first
  is the upper part;
* for each part, a copy of the MEI in which every other note is replaced by
  an invisible space of the same length gives that part's own MIDI, on
  exactly the same time line as the whole score.
"""

from __future__ import annotations

import copy
import re
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass, field

MEI = "http://www.music-encoding.org/ns/mei"
XML_ID = "{http://www.w3.org/XML/1998/namespace}id"
ET.register_namespace("", MEI)


def _t(tag: str) -> str:
    return f"{{{MEI}}}{tag}"


@dataclass
class Track:
    name: str
    kind: str  # "voice", "accompaniment" or "instrument"
    program: int  # General MIDI program (0-based) written in the score
    staves: list[str]
    ids: set[str] = field(default_factory=set)  # drawn notes (MEI ids) this part plays
    select: str = "all"  # "all", "upper" or "lower" (of a two-part staff)


# ---------------------------------------------------------------- names

_VOICE_WORDS = [
    (r"sop(rano)?s?|^s\.?$", "Soprano"), (r"mezzo", "Mezzo"), (r"alto?s?|^a\.?$", "Alto"),
    (r"ten(or)?s?|^t\.?$", "Tenor"), (r"bari(tone)?s?", "Baritone"), (r"bass(es)?|^b\.?$", "Bass"),
    (r"child(ren)?|kids|youth|junior|primary", "Children"), (r"treble", "Treble"),
    (r"descant", "Descant"), (r"solo(ist)?", "Solo"), (r"melody", "Melody"),
    (r"women|ladies|^sa$", "Soprano/Alto"), (r"^men$|^tb$", "Tenor/Bass"),
]
_GENERIC = re.compile(r"^(voice|voices|vox|vocals?|staff|part|instrument|choir)?\s*\d*$", re.I)
_KEYBOARD = re.compile(r"piano|pno|keyboard|keys|organ|org\.|accomp|harp|guitar|synth|"
                       r"celesta|harpsichord|cembalo", re.I)


def is_generic_name(label: str) -> bool:
    return bool(_GENERIC.match(label or ""))


def _names_in(text: str) -> list[str]:
    """Voice names mentioned in a label or a direction ('Sop-Alto' -> both)."""
    out = []
    for tok in re.split(r"[\s\-/&+,.:;()]+|\band\b", text.strip(), flags=re.I):
        if not tok:
            continue
        for pat, name in _VOICE_WORDS:
            if re.fullmatch(pat, tok, re.I) or (len(tok) > 2 and re.match(pat, tok, re.I)):
                out += name.split("/")
                break
    return list(dict.fromkeys(out))


# ---------------------------------------------------------------- MEI helpers

_STEP = {"c": 0, "d": 2, "e": 4, "f": 5, "g": 7, "a": 9, "b": 11}
_ACC = {"s": 1, "f": -1, "ss": 2, "x": 2, "ff": -2, "n": 0, "xs": 3, "ts": 3, "tf": -3,
        "ns": 1, "nf": -1, "su": 1, "fd": -1}


def _pitch(note: ET.Element) -> int:
    base = _STEP.get((note.get("pname") or "c").lower(), 0) + 12 * (int(note.get("oct") or 4) + 1)
    acc = note.get("accid.ges") or note.get("accid")
    if acc is None:
        a = note.find(_t("accid"))
        if a is not None:
            acc = a.get("accid.ges") or a.get("accid")
    return base + _ACC.get(acc or "n", 0)


def _sounding_events(layer: ET.Element):
    """Notes and chords of a layer in order (inside beams, tuplets...)."""
    members = {n for c in layer.iter(_t("chord")) for n in c.iter(_t("note"))}
    for el in layer.iter():
        if el.tag == _t("chord") or (el.tag == _t("note") and el not in members):
            yield el


@dataclass
class _Group:
    label: str
    staves: list[str]
    clefs: dict[str, tuple[str, str, str]]  # staff -> (shape, line, dis)
    program: int | None


def _groups(score_def: ET.Element) -> list[_Group]:
    """Parts in score order: a staffGrp with its own label is one part (e.g.
    the piano's two staves); other staffGrps only bracket parts together."""
    out: list[_Group] = []

    def clef_of(sd):
        c = sd.find(_t("clef"))
        if c is not None:
            return (c.get("shape") or "G", c.get("line") or "2", c.get("dis") or "")
        return (sd.get("clef.shape") or "G", sd.get("clef.line") or "2", sd.get("clef.dis") or "")

    def program_of(el):
        d = el.find(_t("instrDef"))
        try:
            return int(d.get("midi.instrnum")) if d is not None and d.get("midi.instrnum") else None
        except ValueError:
            return None

    def label_of(el):
        lab = el.find(_t("label"))
        return "".join(lab.itertext()).strip() if lab is not None else ""

    def walk(grp):
        for c in grp:
            if c.tag == _t("staffDef"):
                out.append(_Group(label_of(c), [c.get("n")], {c.get("n"): clef_of(c)},
                                  program_of(c)))
            elif c.tag == _t("staffGrp"):
                sds = c.findall(_t("staffDef"))
                own = label_of(c) or program_of(c) is not None
                if own and sds and len(sds) == len(list(c.iter(_t("staffDef")))) \
                        and not any(label_of(s) for s in sds):
                    out.append(_Group(label_of(c), [s.get("n") for s in sds],
                                      {s.get("n"): clef_of(s) for s in sds}, program_of(c)))
                else:
                    walk(c)

    top = score_def.find(_t("staffGrp"))
    if top is not None:
        walk(top)
    return out


@dataclass
class _StaffStats:
    events: int = 0  # notes or chords (onsets)
    multi: int = 0  # chords of 2+ notes, or events in a second layer
    lyrics: int = 0
    words: Counter = field(default_factory=Counter)


def _stats(root: ET.Element) -> dict[str, _StaffStats]:
    stats: dict[str, _StaffStats] = {}
    for staff in root.iter(_t("staff")):
        st = stats.setdefault(staff.get("n"), _StaffStats())
        layers = [ly for ly in staff.findall(_t("layer"))
                  if ly.find(f".//{_t('note')}") is not None]
        for k, ly in enumerate(layers):
            for ev in _sounding_events(ly):
                st.events += 1
                if k > 0 or (ev.tag == _t("chord") and len(ev.findall(f".//{_t('note')}")) > 1):
                    st.multi += 1
        st.lyrics += len(list(staff.iter(_t("syl"))))
    for d in root.iter(_t("dir")):
        staff = (d.get("staff") or "").split()
        text = "".join(d.itertext())
        for n in staff:
            for name in _names_in(text):
                stats.setdefault(n, _StaffStats()).words[name] += 1
    return stats


# ---------------------------------------------------------------- classify


def find_tracks(mei: str, names: list[str] | None = None) -> list[Track]:
    """The parts of the score. `names` optionally overrides the names, one
    entry per staff group in score order ('Soprano/Alto' splits a staff in
    two; '' keeps the automatic name)."""
    root = ET.fromstring(mei)
    score_def = root.find(f".//{_t('scoreDef')}")
    if score_def is None:
        return []
    groups = _groups(score_def)
    stats = _stats(root)
    names = list(names or [])

    classified = []  # (group, kind)
    for g in groups:
        label = g.label if not _GENERIC.match(g.label or "") else ""
        prog = g.program
        voiceish = (prog is not None and 52 <= prog <= 54) or bool(_names_in(label)) or \
            any(stats.get(s, _StaffStats()).lyrics > 0 for s in g.staves)
        keyboard = bool(_KEYBOARD.search(label)) or (prog is not None and 0 <= prog <= 23)
        if voiceish and not (keyboard and not _names_in(label)):
            kind = "voice"
        elif keyboard or len(g.staves) >= 2:
            kind = "accompaniment"
        else:
            kind = "instrument"
        if not any(stats.get(s, _StaffStats()).events for s in g.staves):
            continue  # a staff with no notes at all
        classified.append((g, kind, label))

    tracks: list[Track] = []
    pending = []  # voice staves still needing automatic names
    for k, (g, kind, label) in enumerate(classified):
        given = names[k].strip() if k < len(names) else ""
        if kind != "voice":
            name = given or g.label or ("Accompaniment" if kind == "accompaniment" else "Instrument")
            tracks.append(Track(name, kind, g.program if g.program is not None else 0, g.staves))
            continue
        for s in g.staves:
            st = stats.get(s, _StaffStats())
            divided = st.events and st.multi / st.events >= 0.10
            wanted = [x.strip() for x in given.split("/")] if given else (_names_in(label) or [
                n for n, _ in st.words.most_common(2)])
            low = g.clefs[s][0].upper() == "F" or g.clefs[s][2] == "8"
            if divided or len(wanted) == 2 and given:
                pair = wanted if len(wanted) == 2 else (["Tenor", "Bass"] if low else
                                                         ["Soprano", "Alto"])
                tracks.append(Track(pair[0], "voice", 52, [s], select="upper"))
                tracks.append(Track(pair[1], "voice", 52, [s], select="lower"))
            else:
                t = Track(wanted[0] if wanted else "", "voice", 52, [s])
                tracks.append(t)
                if not wanted:
                    pending.append((t, low))

    # Automatic names for unlabelled single-part voice staves.
    taken = {t.name for t in tracks}
    if len(pending) == 4 and len([t for t in tracks if t.kind == "voice"]) == 4:
        for (t, _), n in zip(pending, ["Soprano", "Alto", "Tenor", "Bass"]):
            t.name = n
    else:
        for t, low in pending:
            for n in (["Tenor", "Bass", "Baritone"] if low else ["Soprano", "Alto", "Treble"]):
                if n not in taken:
                    t.name = n
                    break
            else:
                t.name = "Voice"
            taken.add(t.name)

    voices = [t for t in tracks if t.kind == "voice"]
    if not voices:
        keys = [t for t in tracks if t.kind == "accompaniment" and len(t.staves) >= 2]
        if len(tracks) == 1 and keys:  # a piano piece: practise hands separately
            t = keys[0]
            tracks = [Track("Right hand", "accompaniment", t.program, t.staves[:1]),
                      Track("Left hand", "accompaniment", t.program, t.staves[1:])]
    counts = Counter(t.name for t in tracks)
    seen: Counter = Counter()
    for t in tracks:
        if counts[t.name] > 1:
            seen[t.name] += 1
            t.name = f"{t.name} {seen[t.name]}"
    _assign_ids(root, tracks)
    return [t for t in tracks if t.ids]


# A single note on a two-part staff is sung by both parts unless it is far
# outside one part's range (then only the other part sings it).
_RANGE_SLACK = 3
_PAIR_RANGES = {False: (60, 74), True: (48, 64)}  # low clef? -> (upper min, lower max)


def _assign_ids(root: ET.Element, tracks: list[Track]) -> None:
    low_clef: dict[str, bool] = {}
    for sd in root.iter(_t("staffDef")):
        c = sd.find(_t("clef"))
        shape = (c.get("shape") if c is not None else sd.get("clef.shape")) or "G"
        dis = (c.get("dis") if c is not None else sd.get("clef.dis")) or ""
        low_clef[sd.get("n")] = shape.upper() == "F" or dis == "8"
    by_staff: dict[str, list[Track]] = {}
    for t in tracks:
        for s in t.staves:
            by_staff.setdefault(s, []).append(t)
    for staff in root.iter(_t("staff")):
        ts = by_staff.get(staff.get("n"), [])
        if not ts:
            continue
        layers = [ly for ly in staff.findall(_t("layer"))
                  if ly.find(f".//{_t('note')}") is not None]
        for k, ly in enumerate(layers):
            for ev in _sounding_events(ly):
                notes = [ev] if ev.tag == _t("note") else ev.findall(f".//{_t('note')}")
                if not notes:
                    continue
                order = sorted(notes, key=_pitch)
                for t in ts:
                    if t.select == "all":
                        keep = notes
                    elif len(layers) > 1:
                        keep = notes if (k == 0) == (t.select == "upper") else []
                    elif len(notes) == 1:
                        upper_min, lower_max = _PAIR_RANGES[low_clef.get(staff.get("n"), False)]
                        p = _pitch(notes[0])
                        fits_upper = p >= upper_min - _RANGE_SLACK
                        fits_lower = p <= lower_max + _RANGE_SLACK
                        if fits_upper == fits_lower or (fits_upper if t.select == "upper"
                                                        else fits_lower):
                            keep = notes  # both parts sing it (or only this one can)
                        else:
                            keep = []
                    elif t.select == "upper":
                        keep = order[-1:]
                    else:
                        keep = order[:-1] if len(order) > 2 else order[:1]
                    t.ids.update(n.get(XML_ID) for n in keep if n.get(XML_ID))


# ---------------------------------------------------------------- one part alone


def isolate(mei: str, track: Track) -> str:
    """The MEI with every note that `track` does not play replaced by an
    invisible space of the same length (grace notes are simply removed), so
    its MIDI keeps the whole score's time line."""
    root = ET.fromstring(mei)
    keep = track.ids
    removed: set[str] = set()

    def space_for(el: ET.Element) -> ET.Element:
        sp = ET.Element(_t("space"))
        for a in ("dur", "dots", "dur.ppq"):
            if el.get(a) is not None:
                sp.set(a, el.get(a))
        if el.get(XML_ID):
            sp.set(XML_ID, el.get(XML_ID) + "-s2a")
        return sp

    def replace(parent: ET.Element, old: ET.Element, new: ET.Element | None) -> None:
        idx = list(parent).index(old)
        parent.remove(old)
        if new is not None:
            new.tail = old.tail
            parent.insert(idx, new)

    parents = {c: p for p in root.iter() for c in p}
    for layer in root.iter(_t("layer")):
        for el in list(layer.iter()):
            if el.tag == _t("chord"):
                notes = el.findall(_t("note"))
                kept = [n for n in notes if n.get(XML_ID) in keep]
                for n in notes:
                    if n not in kept:
                        removed.add(n.get(XML_ID))
                if len(kept) == len(notes):
                    continue
                parent = parents.get(el)
                if parent is None:
                    continue
                if not kept:
                    replace(parent, el, None if el.get("grace") else space_for(el))
                elif len(kept) == 1:
                    n = copy.deepcopy(kept[0])
                    for a in ("dur", "dots", "dur.ppq", "grace", "stem.dir"):
                        if el.get(a) is not None and n.get(a) is None:
                            n.set(a, el.get(a))
                    replace(parent, el, n)
                else:
                    for n in notes:
                        if n not in kept:
                            el.remove(n)
            elif el.tag == _t("note") and el.get(XML_ID) not in keep:
                parent = parents.get(el)
                if parent is None or parent.tag == _t("chord"):
                    continue  # chord members are handled with their chord
                removed.add(el.get(XML_ID))
                replace(parent, el, None if el.get("grace") else space_for(el))
    # Control events that point at removed notes (ties, slurs, arpeggios...).
    refs = {f"#{i}" for i in removed if i}
    for measure in root.iter(_t("measure")):
        for ev in list(measure):
            if ev.tag in (_t("staff"),):
                continue
            ends = [ev.get(a) for a in ("startid", "endid") if ev.get(a)]
            if any(e in refs for e in ends):
                measure.remove(ev)
                continue
            if ev.get("plist"):
                pl = [p for p in ev.get("plist").split() if p not in refs]
                if pl:
                    ev.set("plist", " ".join(pl))
                else:
                    measure.remove(ev)
    return ET.tostring(root, encoding="unicode")
