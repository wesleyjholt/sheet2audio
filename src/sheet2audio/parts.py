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

# Whole words that name a voice (a label may also use S, A, T, B, S1, T2 ...).
_VOICE_WORDS = {
    "soprano": "Soprano", "sopranos": "Soprano", "sop": "Soprano", "sopr": "Soprano",
    "mezzo": "Mezzo", "alto": "Alto", "altos": "Alto", "alt": "Alto",
    "tenor": "Tenor", "tenors": "Tenor", "ten": "Tenor",
    "baritone": "Baritone", "baritones": "Baritone", "bari": "Baritone", "bar": "Baritone",
    "bass": "Bass", "basses": "Bass",
    "children": "Children", "child": "Children", "kids": "Children", "youth": "Children",
    "junior": "Children", "primary": "Children", "treble": "Treble", "trebles": "Treble",
    "descant": "Descant", "solo": "Solo", "soloist": "Solo", "melody": "Melody",
    "women": "Soprano/Alto", "ladies": "Soprano/Alto", "men": "Tenor/Bass",
    "sa": "Soprano/Alto", "tb": "Tenor/Bass", "satb": "Soprano/Alto/Tenor/Bass",
    "ssa": "Soprano 1/Soprano 2/Alto", "ttb": "Tenor 1/Tenor 2/Bass",
}
_LETTERS = {"s": "Soprano", "a": "Alto", "t": "Tenor", "b": "Bass"}
_INSTRUMENT = re.compile(
    r"\b(sax(ophone)?|bassoon|guitar|clarinet|flute|piccolo|recorder|violin|viola|cello|"
    r"violoncello|contrabass|double|trombone|trumpet|horn|tuba|oboe|drums?|timpani|"
    r"percussion|marimba|xylophone|vibraphone|glockenspiel|bells|ukulele|banjo|mandolin|"
    r"harmonica|accordion|strings|brass|winds)\b", re.I)
_GENERIC = re.compile(r"^(voice|voices|vox|vocals?|staff|part|instrument|choir|chorus)?"
                      r"\s*\d*$", re.I)
_KEYBOARD = re.compile(r"piano|pno|keyboard|keys|organ|org\.|accomp|harp|synth|"
                       r"celesta|harpsichord|cembalo", re.I)
_DIVISI = re.compile(r"(\b(1|I)\s*/\s*(2|II)\b|\bdiv(isi|\.)?\b|\b(1|I)\s*(&|and|\+)\s*(2|II)\b)",
                     re.I)


def is_generic_name(label: str) -> bool:
    return bool(_GENERIC.match(label or ""))


def _names_in(text: str, letters: bool = True) -> list[str]:
    """Voice names in a label or direction: 'Sop-Alto' -> [Soprano, Alto],
    'S1' -> [Soprano 1]. Single letters (S, A, T, B) count only in labels
    (`letters`), so 'a tempo' names nothing."""
    if _INSTRUMENT.search(text or ""):
        return []
    out = []
    for tok in re.split(r"[\s\-/&+,:;()]+|\band\b", (text or "").strip(), flags=re.I):
        tok = tok.strip(".").lower()
        if not tok:
            continue
        m = re.fullmatch(r"([a-z]+)\.?\s*([12])", tok)
        if m and (m.group(1) in _LETTERS or m.group(1) in _VOICE_WORDS):
            base = _LETTERS.get(m.group(1)) or _VOICE_WORDS[m.group(1)]
            out.append(f"{base} {m.group(2)}")
        elif tok in _VOICE_WORDS:
            out += _VOICE_WORDS[tok].split("/")
        elif letters and tok in _LETTERS:
            out.append(_LETTERS[tok])
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


def _voice_layers(staff: ET.Element) -> list[ET.Element]:
    """The layers (voices) of a staff that carry music - notes, or rests of a
    voice that is resting - in voice-number order (first = upper voice)."""
    kinds = (_t("note"), _t("rest"), _t("mRest"), _t("multiRest"))
    layers = [ly for ly in staff.findall(_t("layer"))
              if any(el.tag in kinds for el in ly.iter())]

    def n(ly):
        try:
            return int(ly.get("n") or 0)
        except ValueError:
            return 0
    return sorted(layers, key=n)


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
        layers = _voice_layers(staff)
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
            for name in _names_in(text, letters=False):
                stats.setdefault(n, _StaffStats()).words[name] += 1
    return stats


# ---------------------------------------------------------------- classify


@dataclass
class GroupPlan:
    """What one staff group becomes: its kind and, per staff, the names of
    the part(s) it carries (two names = split into upper and lower)."""

    group: _Group
    kind: str
    names: dict[str, list[str]]  # staff -> [name] or [upper, lower]; [] = name it later
    has_notes: bool
    given: str = ""  # the name the user gave (--parts), if any

    @property
    def entry(self) -> str:
        """The --parts entry that reproduces this plan."""
        if self.kind != "voice":
            return self.given or ("" if is_generic_name(self.group.label) else self.group.label)
        per = ["/".join(self.names.get(s, [])) for s in self.group.staves]
        return per[0] if len(per) == 1 else "|".join(per)


def plan_groups(root: ET.Element, names: list[str] | None = None,
                warnings: list[str] | None = None) -> list[GroupPlan]:
    score_def = root.find(f".//{_t('scoreDef')}")
    if score_def is None:
        return []
    groups = _groups(score_def)
    stats = _stats(root)
    names = list(names or [])
    warn = warnings if warnings is not None else []
    if names and len(names) != len(groups):
        warn.append(f"--parts gives {len(names)} names but the score has {len(groups)} staff "
                    "groups; names were matched in order.")
    plans: list[GroupPlan] = []
    for gi, g in enumerate(groups):
        st = [stats.get(s, _StaffStats()) for s in g.staves]
        has_notes = any(x.events for x in st)
        given = names[gi].strip() if gi < len(names) else ""
        given_names = [x.strip() for x in re.split(r"[/|]", given)] if given else []
        by_staff = ([[y.strip() for y in x.split("/")] for x in given.split("|")]
                    if "|" in given else None)
        if given and (any(not x for x in given_names) or len(given_names) > 2 * len(g.staves)
                      or (by_staff and len(by_staff) != len(g.staves))):
            warn.append(f"--parts entry '{given}' does not fit its staff group; ignored.")
            given, given_names, by_staff = "", [], None
        label = "" if is_generic_name(g.label) else g.label
        prog = g.program
        instrument = bool(_INSTRUMENT.search(label))
        keyboard = bool(_KEYBOARD.search(label)) or (prog is not None and 0 <= prog <= 23)
        voiceish = not instrument and (
            (prog is not None and 52 <= prog <= 54) or bool(_names_in(label))
            or any(x.lyrics > 0 for x in st))
        if voiceish and not (keyboard and not _names_in(label)):
            kind = "voice"
        elif keyboard or len(g.staves) >= 2:
            kind = "accompaniment"
        else:
            kind = "instrument"
        if given:  # a given name that says what the group is wins
            if "/" in given or "|" in given or any(_names_in(x) for x in given_names):
                kind = "voice"
            elif _KEYBOARD.search(given):
                kind = "accompaniment"
            elif _INSTRUMENT.search(given):
                kind = "instrument"
        per_staff: dict[str, list[str]] = {}
        if kind == "voice":
            # A given group word ('Women', 'SATB') stands for its voices; any
            # other given name is kept exactly as typed.
            expanded = []
            for x in given_names:
                exp = _names_in(x)
                expanded += exp if len(exp) > 1 else [x]
            source = expanded or _names_in(label)
            k = len(g.staves)
            for idx, s in enumerate(g.staves):
                if by_staff:
                    per_staff[s] = by_staff[idx][:2]
                    continue
                if source and len(source) == k:
                    wanted = [source[idx]]
                elif source and len(source) == 2 * k:
                    wanted = source[2 * idx: 2 * idx + 2]
                elif source and k == 1:
                    wanted = source[:2]
                else:
                    wanted = [n for n, _ in stats.get(s, _StaffStats()).words.most_common(1)]
                    wanted = [w for w in wanted if "/" not in w]
                x = stats.get(s, _StaffStats())
                divided = bool(x.events) and x.multi / x.events >= 0.10
                if len(wanted) == 1 and divided and label and _DIVISI.search(label):
                    wanted = [f"{wanted[0]} 1", f"{wanted[0]} 2"]  # 'Soprano 1/2', 'div.'
                elif len(wanted) == 1 and not given and divided and not _names_in(label):
                    wanted = []  # a direction word ('Sopranos only') does not decide a split
                if not wanted and divided:
                    wanted = ["?upper", "?lower"]
                per_staff[s] = wanted
        else:
            per_staff = {s: [] for s in g.staves}
        plans.append(GroupPlan(g, kind, per_staff, has_notes, given))
    _auto_names(plans, root)
    return plans


def _staff_is_low(root: ET.Element, g: _Group, staff: str) -> bool:
    shape, _line, dis = g.clefs[staff]
    if shape.upper() == "F" or dis == "8":
        return True
    pitches = [_pitch(n) for st in root.iter(_t("staff")) if st.get("n") == staff
               for n in st.iter(_t("note"))]
    return bool(pitches) and sorted(pitches)[len(pitches) // 2] < 60


def _auto_names(plans: list[GroupPlan], root: ET.Element) -> None:
    """Name the voice staves that have no name yet, from their clefs and
    ranges: two high + two low staves are S, A, T, B; otherwise high staves
    are Soprano (1, 2...) and Alto, low ones Tenor and Bass (or Baritone)."""
    taken = {n for p in plans for ns in p.names.values() for n in ns if not n.startswith("?")}
    high, low = [], []  # (plan, staff, divided)
    for p in plans:
        if p.kind != "voice":
            continue
        for s, ns in p.names.items():
            if not ns or ns[0].startswith("?"):
                (low if _staff_is_low(root, p.group, s) else high).append((p, s, bool(ns)))
    for p, s, divided in high + low:
        if divided:
            is_low = (p, s, True) in low
            pair = ["Tenor", "Bass"] if is_low else ["Soprano", "Alto"]
            p.names[s] = pair
    single_high = [(p, s) for p, s, d in high if not d]
    single_low = [(p, s) for p, s, d in low if not d]
    if len(single_high) == 2 and len(single_low) == 2 and not taken:
        wanted = ["Soprano", "Alto", "Tenor", "Bass"]
    else:
        hn = {1: ["Soprano"], 2: ["Soprano", "Alto"], 3: ["Soprano 1", "Soprano 2", "Alto"],
              4: ["Soprano 1", "Soprano 2", "Alto 1", "Alto 2"]}
        ln = {1: ["Baritone" if len(single_high) == 2 else "Tenor"], 2: ["Tenor", "Bass"],
              3: ["Tenor 1", "Tenor 2", "Bass"], 4: ["Tenor 1", "Tenor 2", "Bass 1", "Bass 2"]}
        wanted = (hn.get(len(single_high), [f"Voice {i + 1}" for i in range(len(single_high))])
                  + ln.get(len(single_low), [f"Low voice {i + 1}" for i in range(len(single_low))]))
    for (p, s), n in zip(single_high + single_low, wanted):
        p.names[s] = [n]


def describe_groups(mei: str, names: list[str] | None = None) -> list[tuple[str, str, bool]]:
    """(--parts entry, kind, has notes) for every staff group."""
    return [(p.entry, p.kind, p.has_notes) for p in plan_groups(ET.fromstring(mei), names)]


def find_tracks(mei: str, names: list[str] | None = None, hands: bool = True,
                warnings: list[str] | None = None) -> list[Track]:
    """The parts of the score. `names` optionally gives the names, one entry
    per staff group in score order ('Soprano/Alto' splits a staff in two, an
    entry naming voices makes the group sung; '' keeps the automatic name).
    `hands`: split a piano-only piece into right and left hand."""
    root = ET.fromstring(mei)
    plans = plan_groups(root, names, warnings)
    tracks: list[Track] = []
    for p in plans:
        if not p.has_notes:
            continue
        g = p.group
        prog = g.program if g.program is not None else 0
        if p.kind != "voice":
            label = "" if is_generic_name(g.label) else g.label
            name = p.entry or label or ("Accompaniment" if p.kind == "accompaniment"
                                        else "Instrument")
            tracks.append(Track(name, p.kind, prog, g.staves))
            continue
        for s in g.staves:
            ns = p.names.get(s) or ["Voice"]
            if len(ns) == 2:
                tracks.append(Track(ns[0], "voice", 52, [s], select="upper"))
                tracks.append(Track(ns[1], "voice", 52, [s], select="lower"))
            else:
                tracks.append(Track(ns[0], "voice", 52, [s]))
    if hands and len(tracks) == 1 and tracks[0].kind == "accompaniment" \
            and len(tracks[0].staves) >= 2:
        t = tracks[0]  # a piano piece: practise hands separately
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
        layers = _voice_layers(staff)
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
