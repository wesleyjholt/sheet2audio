"""Simulate OMR missing rests: delete rest notes from an Audiveris MusicXML.

usage: mutate.py IN OUT MEASURE:STAFF:INDEX ...   (INDEX = n-th rest of that staff in that measure, 0-based)
The next <backup> is shortened by the removed duration, like Audiveris writes it when it
never saw the rest (later voices keep their time); a leading rest just vanishes (notes move earlier).
"""
import sys, xml.etree.ElementTree as ET
from pathlib import Path
sys.path.insert(0, "/Users/holtw/.local/share/project-worktrees/sheet-music-to-audio/src")
from sheet2audio.musicxml import read_musicxml_bytes
src, dst, specs = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3:]
root = ET.fromstring(read_musicxml_bytes(src))
ms = {m.get("number"): m for m in root.find("part").findall("measure")}
for spec in specs:
    num, staff, k = spec.split(":")
    m = ms[num]
    rests = [c for c in m if c.tag == "note" and c.find("rest") is not None and (c.findtext("staff") or "1") == staff]
    r = rests[int(k)]
    dur = int(r.findtext("duration"))
    kids = list(m)
    i = kids.index(r)
    nxt = next((c for c in kids[i + 1:] if c.tag in ("note", "backup", "forward")), None)
    if nxt is not None and nxt.tag == "backup":
        d = nxt.find("duration"); d.text = str(int(d.text) - dur)
    m.remove(r)
    print(f"removed rest m{num} staff{staff} #{k} dur={dur}")
ET.ElementTree(root).write(dst, encoding="UTF-8", xml_declaration=True)
