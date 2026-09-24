"""Count the notes at every stage, so that none goes missing silently.

    recognised  noteheads Audiveris found (its book)
    exported    notes in Audiveris' MusicXML
    cleaned     notes in our MusicXML, after clean-up and repair
    drawn       notes in Verovio's score (what the page and video show)
    played      notes Verovio plays (the audio, MIDI and highlighting)

Each stage must keep every note of the stage before. Notes have gone
missing silently between each pair of stages before (Audiveris' rhythm
step dropping chords that did not fit the bar, Verovio dropping beamed
chord notes, bars under a misread ending bracket never played), so every
loss is counted here, named by bar for the user, and fails the corpus
tests.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from .omr import page_bar_order

MEI = "{http://www.music-encoding.org/ns/mei}"
XML_ID = "{http://www.w3.org/XML/1998/namespace}id"
_REND = re.compile(r"(-rend\d+)+$")


def _sheets(z: zipfile.ZipFile) -> list[str]:
    return sorted((n for n in z.namelist() if re.fullmatch(r"sheet#(\d+)/sheet#\1\.xml", n)),
                  key=lambda n: int(re.search(r"#(\d+)", n).group(1)))


def book_heads(book: Path) -> dict[tuple[int, int], int]:
    """Noteheads Audiveris recognised, per (page, bar): the page of music
    (0-based over the book, as omr.BarCheck counts them) and the bar's
    position on it (0-based, omr.page_bar_order). A head shared by two
    chords (one head, stems both ways) counts once per chord, as it is
    exported once per voice; a head in no chord counts in the bar it sits
    in."""
    out: dict[tuple[int, int], int] = {}
    try:
        z = zipfile.ZipFile(book)
    except (OSError, zipfile.BadZipFile):
        return out
    page_index = 0
    with z:
        for name in _sheets(z):
            try:
                root = ET.fromstring(z.read(name))
            except ET.ParseError:
                continue
            for page in root.iter("page"):
                order = page_bar_order(page)
                for system in page.iter("system"):
                    members: dict[str, int] = {}  # chord id -> heads in it
                    in_chord: set[str] = set()
                    heads = {h.get("id"): h for h in system.iter("head")}
                    rels = system.find("sig/relations")
                    for rel in (rels.findall("relation") if rels is not None else []):
                        if len(rel) and rel[0].tag == "containment" and rel.get("target") in heads:
                            members[rel.get("source")] = members.get(rel.get("source"), 0) + 1
                            in_chord.add(rel.get("target"))
                    stacks = [(float(st.get("left", 0)), float(st.get("right", 0)), st.get("id", ""))
                              for st in system.iter("stack") if st.get("special") != "CAUTIONARY"]
                    for part in system.findall("part"):
                        for m in part.findall("measure"):
                            if m.get("id") not in order:
                                continue
                            key = (page_index, order[m.get("id")])
                            for e in m.findall("head-chords"):
                                for chord in (e.text or "").split():
                                    out[key] = out.get(key, 0) + members.get(chord, 0)
                    for hid, h in heads.items():
                        b = h.find("bounds")
                        if hid in in_chord or b is None:
                            continue
                        x = float(b.get("x", 0))
                        bar = next((sid for left, right, sid in stacks if left <= x < right), None)
                        if bar in order:
                            key = (page_index, order[bar])
                            out[key] = out.get(key, 0) + 1
                page_index += 1
    return out


def musicxml_notes(root: ET.Element) -> list[int]:
    """Pitched notes (chord members and grace notes included) per measure,
    over all parts, in measure order."""
    counts: list[int] = []
    for part in root.findall("part"):
        for i, m in enumerate(part.findall("measure")):
            if i >= len(counts):
                counts.append(0)
            counts[i] += sum(1 for n in m.findall("note")
                             if n.find("rest") is None and n.find("unpitched") is None)
    return counts


def count(root: ET.Element) -> int:
    return sum(musicxml_notes(root))


@dataclass
class RenderCount:
    cleaned: int  # notes in the MusicXML given to Verovio
    drawn: int  # notes in Verovio's score
    played: int  # drawn notes that are heard (a tied note with the note it continues)
    lost: list[tuple[str, int]] = field(default_factory=list)  # (measure, notes not drawn)
    silent: list[tuple[str, int]] = field(default_factory=list)  # (measure, drawn, never played)


def render_count(xml: bytes, mei: str, timemap: list[dict]) -> RenderCount:
    """Compare the MusicXML Verovio was given with what it drew and played.
    A note tied from the note before does not sound again, so it is not
    expected in the playback."""
    root = ET.fromstring(xml)
    per_xml = musicxml_notes(root)
    labels = [m.get("number", str(i + 1)) for i, m in enumerate(root.find("part").findall("measure"))] \
        if root.find("part") is not None else []
    tree = ET.fromstring(mei)
    measures = list(tree.iter(MEI + "measure"))
    ties = {t.get("endid", "").lstrip("#") for t in tree.iter(MEI + "tie")}
    played_ids = {_REND.sub("", i) for e in timemap for i in e.get("on", [])}
    drawn = played = 0
    lost, silent = [], []
    for i, m in enumerate(measures):
        notes = list(m.iter(MEI + "note"))
        drawn += len(notes)
        sounding = [n for n in notes if n.get(XML_ID) not in ties and n.get("tie") not in ("m", "t")]
        heard = sum(1 for n in sounding if n.get(XML_ID) in played_ids)
        played += heard + (len(notes) - len(sounding))
        label = labels[i] if i < len(labels) else m.get("n", str(i + 1))
        if heard < len(sounding):
            silent.append((label, len(sounding) - heard))
        if len(measures) == len(per_xml) and len(notes) < per_xml[i]:
            lost.append((label, per_xml[i] - len(notes)))
    cleaned = sum(per_xml)
    if drawn < cleaned and not lost:
        lost.append(("?", cleaned - drawn))  # measures could not be matched up
    return RenderCount(cleaned, drawn, played, lost, silent)


def bars_text(items: list[tuple[str, int]], limit: int = 8) -> str:
    """'12 (3), 14 (1)' for the user."""
    shown = [f"{m} ({k})" for m, k in items[:limit]]
    more = len(items) - limit
    return ", ".join(shown) + (f" and {more} more" if more > 0 else "")


def book_loss_notes(roots: list[ET.Element], heads: dict[tuple[int, int], int],
                    skip: set[tuple[int, int]]):
    """Tell the user where Audiveris' MusicXML has fewer notes than the
    noteheads it recognised (per movement, as musicxml.Note). Bars in
    `skip` already have a note saying why (chords it could not time). Only
    when the whole score lost notes: a note exported one bar off (e.g. a
    grace note) is not a loss."""
    from . import musicxml

    notes: list[list] = [[] for _ in roots]
    per = [musicxml_notes(r) for r in roots]
    if sum(heads.values()) <= sum(map(sum, per)):
        return notes
    pages = musicxml._book_pages(roots)
    for (page, position), n in sorted(heads.items()):
        if page >= len(pages) or (page, position) in skip:
            continue
        r, first, end = pages[page]
        index = first + position
        if index >= end or per[r][index] >= n:
            continue
        missing = n - per[r][index]
        m = roots[r].find("part").findall("measure")[index]
        notes[r].append(musicxml.Note(
            f"Measure {{m}}: Audiveris recognised {missing} more note{'s' if missing != 1 else ''} "
            "here than it wrote out, so they are missing; check this bar.", musicxml._ref(m)))
    return notes
