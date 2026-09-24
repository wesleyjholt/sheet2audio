"""Side-by-side material for checking a result against the printed score.

    uv run python tests/audit_materials.py OUTDIR PDF DEST

OUTDIR is a sheet2audio output folder made from PDF (it needs omr/score.omr,
omr/score.mxl and the .musicxml). For every line of music it writes to DEST:
a crop of the printed line, our rendering of the same bars, and a JSON file
with what each stage has for those bars (noteheads Audiveris found, its
MusicXML, our final MusicXML, its rhythm log lines). The first stage where a
bar differs from print is where it went wrong. Needs pdftoppm and
rsvg-convert. Assumes a single-movement score whose first page is page 1.
"""
import json
import os
import re
import subprocess
import sys
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

import verovio
from PIL import Image

from sheet2audio import musicxml as mx
from sheet2audio.video import music_font_env

outdir, pdf, dest = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
dest.mkdir(parents=True, exist_ok=True)
book = outdir / "omr" / "score.omr"
final = next(p for p in outdir.glob("*.musicxml"))
raw = outdir / "omr" / "score.mxl"
log = (outdir / "omr" / "audiveris.log").read_text(errors="replace")

# 1. pages at 300 dpi (matches Audiveris' 2550x3300 picture)
pages_dir = dest / "pages"
if not pages_dir.exists():
    pages_dir.mkdir()
    subprocess.run(["pdftoppm", "-r", "300", "-png", str(pdf), str(pages_dir / "page")], check=True)
page_png = sorted(pages_dir.glob("page-*.png"), key=lambda p: int(re.search(r"(\d+)", p.stem).group(1)))

# 2. systems from the book
z = zipfile.ZipFile(book)
sheets = sorted((n for n in z.namelist() if re.fullmatch(r"sheet#(\d+)/sheet#\1\.xml", n)),
                key=lambda n: int(re.search(r"#(\d+)", n).group(1)))


def notes_text(root):
    """measure number -> part id -> compact note list."""
    out = {}
    names = {sp.get("id"): (sp.findtext("part-name") or sp.get("id")) for sp in root.iter("score-part")}
    for p in root.findall("part"):
        for m in p.findall("measure"):
            items = []
            for c in m:
                if c.tag == "attributes":
                    for t in c.findall("time"):
                        items.append(f"[time {t.findtext('beats')}/{t.findtext('beat-type')}]")
                    for k in c.findall("key"):
                        items.append(f"[key {k.findtext('fifths')}]")
                elif c.tag == "backup":
                    items.append("<<backup>>")
                elif c.tag == "forward":
                    items.append("<<forward>>")
                elif c.tag == "note":
                    typ = c.findtext("type") or "?"
                    dots = "." * len(c.findall("dot"))
                    tm = c.find("time-modification")
                    tup = f"(tuplet {tm.findtext('actual-notes')}:{tm.findtext('normal-notes')})" if tm is not None else ""
                    staff = c.findtext("staff") or "1"
                    voice = c.findtext("voice") or "1"
                    tie = "~" if any(t.get("type") == "start" for t in c.findall("tie")) else ""
                    lyr = c.findtext("lyric/text")
                    ly = f' "{lyr}"' if lyr else ""
                    if c.find("rest") is not None:
                        s = f"rest-{typ}{dots}"
                    else:
                        pt = c.find("pitch")
                        alt = {"1": "#", "-1": "b", "2": "##", "-2": "bb"}.get(pt.findtext("alter") or "", "")
                        s = f"{pt.findtext('step')}{alt}{pt.findtext('octave')}-{typ}{dots}{tup}{tie}"
                    if c.find("chord") is not None:
                        s = "+" + s
                    if c.find("grace") is not None:
                        s = "grace:" + s
                    items.append(f"s{staff}v{voice}:{s}{ly}")
            out.setdefault(m.get("number"), {})[f"{p.get('id')} ({names.get(p.get('id'))})"] = " ".join(items)
    return out


final_notes = notes_text(mx.read_musicxml(final))
raw_notes = notes_text(mx.read_musicxml(raw))

# page starts in printed measure numbers, from the final score's new-page marks (first part)
fin_root = mx.read_musicxml(final)
first_part = fin_root.find("part")
page_starts = []
for i, m in enumerate(first_part.findall("measure")):
    if i == 0 or any(pr.get("new-page") == "yes" for pr in m.findall("print")):
        page_starts.append(int(re.sub(r"\D", "", m.get("number")) or 0))

tk = verovio.toolkit()
tk.setResourcePath(str(Path(verovio.__file__).with_name("data")))
env = music_font_env()
xml_text = final.read_text(encoding="utf-8")

systems = []
for si_sheet, name in enumerate(sheets):
    sheet_no = int(re.search(r"#(\d+)", name).group(1))
    r = ET.fromstring(z.read(name))
    img = Image.open(page_png[sheet_no - 1])
    sheet_log = [l for l in log.splitlines() if f"#{sheet_no}]" in l and ("timeOffset" in l or "rhythm" in l or "too long" in l or "WARN" in l or "excess" in l)]
    for sys_i, s in enumerate(r.iter("system")):
        stacks = [st for st in s.iter("stack") if st.get("special") != "CAUTIONARY"]
        ids = [int(st.get("id")) for st in stacks]
        ys = [float(pt.get("y")) for st in s.iter("staff") for ln in st.iter("line") for pt in ln.iter("point")]
        if not ys or not ids:
            continue
        top, bottom = min(ys), max(ys)
        left = max(0, min(float(st.get("left")) for st in stacks) - 140)
        right = min(img.width, max(float(st.get("right")) for st in stacks) + 20)
        box = (int(left), int(max(0, top - 170)), int(right), int(min(img.height, bottom + 230)))
        start = page_starts[sheet_no - 1]
        bars = [start + i - 1 for i in sorted(set(ids))]
        tag = f"p{sheet_no}s{sys_i + 1}_m{bars[0]}-{bars[-1]}"
        crop = dest / f"{tag}_original.png"
        img.crop(box).save(crop)
        # heads per staff per bar in the book
        heads = {}
        for h in s.iter("head"):
            b = h.find("bounds")
            x = float(b.get("x"))
            st = next((int(k.get("id")) for k in stacks if float(k.get("left")) <= x < float(k.get("right"))), None)
            if st is not None:
                key = f"m{start + st - 1} staff{h.get('staff')}"
                heads[key] = heads.get(key, 0) + 1
        # our rendering of those bars
        tk.setOptions({"pageWidth": 2600, "pageHeight": 60000, "scale": 45, "adjustPageHeight": True,
                       "breaks": "none", "footer": "none", "header": "none", "svgViewBox": False})
        tk.loadData(xml_text)
        tk.select({"measureRange": f"{bars[0]}-{bars[-1]}"})
        tk.redoLayout()
        svg = tk.renderToSVG(1)
        svg_path = dest / f"{tag}_ours.svg"
        svg_path.write_text(svg, encoding="utf-8")
        png = dest / f"{tag}_ours.png"
        subprocess.run(["rsvg-convert", "-b", "white", "-o", str(png), str(svg_path)], check=True,
                       env={**os.environ, **env})
        svg_path.unlink()
        systems.append({
            "tag": tag, "sheet": sheet_no, "system": sys_i + 1, "bars": bars,
            "original_png": str(crop), "ours_png": str(png),
            "final_notes": {str(b): final_notes.get(str(b), {}) for b in bars},
            "audiveris_export_notes": {str(b): raw_notes.get(str(b), {}) for b in bars},
            "book_heads": heads,
            "audiveris_log": sheet_log,
            "book_stacks": [dict(st.attrib) for st in s.iter("stack")],
        })
        print(tag, bars)
(dest / "systems.json").write_text(json.dumps(systems, indent=1), encoding="utf-8")
