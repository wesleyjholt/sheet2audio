"""Print Verovio measure start qstamps and per-measure lengths for a MusicXML file."""
import sys
from pathlib import Path
sys.path.insert(0, "/Users/holtw/.local/share/project-worktrees/sheet-music-to-audio/src")
from sheet2audio.musicxml import read_musicxml_bytes
from sheet2audio.render import render_musicxml
r = render_musicxml(read_musicxml_bytes(Path(sys.argv[1])), "x")
starts = [e["qstamp"] for e in r.timemap if "measureOn" in e]
ends = max(e["qstamp"] for e in r.timemap)
L = [round(b - a, 4) for a, b in zip(starts, starts[1:] + [ends])]
print("starts", starts)
print("lengths", L)
