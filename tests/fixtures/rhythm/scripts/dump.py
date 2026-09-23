"""Dump per-measure, per-staff content of a MusicXML file: onset(ql) dur(ql) pitch/rest, plus timing."""
import sys
from fractions import Fraction
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "src"))
import xml.etree.ElementTree as ET
from sheet2audio.musicxml import read_musicxml_bytes, _MeasureTiming
root = ET.fromstring(read_musicxml_bytes(Path(sys.argv[1])))
div = 1
for part in root.findall("part"):
    for m in part.findall("measure"):
        for a in m.findall("attributes"):
            if a.find("divisions") is not None: div = int(a.findtext("divisions"))
            t = a.find("time")
            if t is not None: ts = f"{t.findtext('beats')}/{t.findtext('beat-type')}"
        tm = _MeasureTiming(m)
        pos = 0; last = 0; ev = {}
        for c in m:
            if c.tag == "note":
                if c.find("grace") is not None: continue
                d = int(c.findtext("duration") or 0)
                ch = c.find("chord") is not None
                st = last if ch else pos
                if not ch: last = pos; pos += d
                p = c.find("pitch")
                name = "r" if c.find("rest") is not None else (p.findtext("step") + (p.findtext("alter") or "") + p.findtext("octave"))
                if c.find("rest") is not None and c.find("rest").get("measure") == "yes": name = "R"
                tup = "t" if c.find("time-modification") is not None else ""
                tie = "~" if any(t.get("type") == "start" for t in c.findall("tie")) else ""
                ev.setdefault(c.findtext("staff") or "1", []).append(f"{float(Fraction(st, div)):g}:{name}{tie}/{float(Fraction(d, div)):g}{tup}")
            elif c.tag == "backup": pos -= int(c.findtext("duration"))
            elif c.tag == "forward": pos += int(c.findtext("duration"))
            elif c.tag == "barline":
                ev.setdefault("bar", []).append((c.get("location") or "right") + ":" + "".join(x.tag + "=" + (x.get("direction") or x.get("type") or x.get("number") or x.text or "") for x in c))
        print(f"m{m.get('number')} impl={m.get('implicit')} div={div} len={float(Fraction(tm.max_pos, div)):g} staff_end={ {k: float(Fraction(v, div)) for k, v in tm.staff_end.items()} }")
        for k in sorted(ev):
            print(f"   {k}: {' '.join(ev[k])}")
