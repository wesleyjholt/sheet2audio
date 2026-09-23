"""Hand-correct the Audiveris MusicXML of pickup_volta_34: add the volta 1/2 brackets it missed."""
import sys, xml.etree.ElementTree as ET
from pathlib import Path
sys.path.insert(0, "/Users/holtw/.local/share/project-worktrees/sheet-music-to-audio/src")
from sheet2audio.musicxml import read_musicxml_bytes
src, dst = Path(sys.argv[1]), Path(sys.argv[2])
root = ET.fromstring(read_musicxml_bytes(src))
ms = {m.get("number"): m for m in root.find("part").findall("measure")}
def barline(m, loc):
    for b in m.findall("barline"):
        if b.get("location", "right") == loc:
            return b
    b = ET.Element("barline", location=loc)
    if loc == "left":
        idx = next(i for i, c in enumerate(m) if c.tag not in ("print", "attributes"))
        m.insert(idx, b)
    else:
        m.append(b)
    return b
def add_ending(b, num, typ):
    if any(x.get("number") == num for x in b.findall("ending")):
        return
    e = ET.Element("ending", number=num, type=typ)
    kids = list(b)
    rep = b.find("repeat")
    b.insert(kids.index(rep) if rep is not None else len(kids), e)
add_ending(barline(ms["8"], "left"), "1", "start")
add_ending(barline(ms["8"], "right"), "1", "stop")
add_ending(barline(ms["9"], "left"), "2", "start")
add_ending(barline(ms["9"], "right"), "2", "discontinue")
ET.ElementTree(root).write(dst, encoding="UTF-8", xml_declaration=True)
