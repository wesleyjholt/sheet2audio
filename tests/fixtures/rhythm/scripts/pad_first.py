"""Simulate the suggested fix (pad measure 1 when it is not marked implicit) on an already-repaired file."""
import sys, io, xml.etree.ElementTree as ET
from fractions import Fraction
from pathlib import Path
sys.path.insert(0, "/Users/holtw/.local/share/project-worktrees/sheet-music-to-audio/src")
from sheet2audio.musicxml import read_musicxml_bytes, _MeasureTiming, _pad_measure
root = ET.fromstring(read_musicxml_bytes(Path(sys.argv[1])))
m = root.find("part").find("measure")
div = int(m.find("attributes/divisions").text)
t = m.find("attributes/time"); bar = Fraction(int(t.findtext("beats")) * 4, int(t.findtext("beat-type")))
tm = _MeasureTiming(m)
print("m1 implicit:", m.get("implicit"), "len", Fraction(tm.max_pos, div), "bar", bar)
_pad_measure(m, tm, int(bar * div), div)
ET.ElementTree(root).write(sys.argv[2], encoding="UTF-8", xml_declaration=True)
