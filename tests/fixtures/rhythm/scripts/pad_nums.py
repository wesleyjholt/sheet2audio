"""Simulate a fix that pads the given measure numbers (bypassing the split-measure skip)."""
import sys, xml.etree.ElementTree as ET
from fractions import Fraction
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "src"))
from sheet2audio.musicxml import read_musicxml_bytes, _MeasureTiming, _pad_measure
root = ET.fromstring(read_musicxml_bytes(Path(sys.argv[1])))
div, bar = 1, None
for m in root.find("part").findall("measure"):
    for a in m.findall("attributes"):
        if a.find("divisions") is not None: div = int(a.findtext("divisions"))
        t = a.find("time")
        if t is not None: bar = Fraction(int(t.findtext("beats")) * 4, int(t.findtext("beat-type")))
    if m.get("number") in sys.argv[3:]:
        tm = _MeasureTiming(m)
        print("m", m.get("number"), "len", Fraction(tm.max_pos, div), "barlines:", [b.findtext("bar-style") for b in m.findall("barline")])
        _pad_measure(m, tm, int(bar * div), div)
ET.ElementTree(root).write(sys.argv[2], encoding="UTF-8", xml_declaration=True)
