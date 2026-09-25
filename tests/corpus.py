"""Run the whole fixture corpus through the CLI and score every result.

    uv run python tests/corpus.py [--jobs 4] [--filter REGEX] [--out out/corpus]

Each fixture is a PDF or image with a ground-truth MIDI written by LilyPond
(for several pieces in one file LilyPond writes name.midi, name-1.midi, ...).
Scores: pitch F1 (order-aware, ignores rhythm) and, when the file holds a
single piece, onset F1 (same pitch starting within 1/48 quarter note).
Results go to <out>/results.json and a table on stdout.

Every case also checks the note ledger (src/sheet2audio/ledger.py): our own
stages (Audiveris' MusicXML -> our clean-up -> Verovio's score -> playback)
must not lose a note. A loss there marks the case LEDGER and makes the run
exit with status 1. Notes Audiveris recognised but did not write out are
counted too (column 'omr lost'); they are Audiveris' failures, not ours,
but each must be named in the report's notes.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures"
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from evaluate import f1, lcs_len, midi_notes, onset_matches  # noqa: E402

from sheet2audio.musicxml import read_musicxml_bytes  # noqa: E402
from sheet2audio.render import render_musicxml  # noqa: E402


def ground_truth(stem_path: Path) -> list[Path]:
    """name.midi, name-1.midi, name-2.midi ... in order."""
    first = stem_path.with_suffix(".midi")
    if not first.is_file():
        return []
    out = [first]
    k = 1
    while (p := stem_path.with_name(f"{stem_path.name}-{k}.midi")).is_file():
        out.append(p)
        k += 1
    return out


def cases() -> list[tuple[str, Path, list[Path], list[str]]]:
    found = []
    for pdf in sorted(FIX.glob("*/*.pdf")) + sorted(FIX.glob("*.pdf")):
        gt = ground_truth(pdf.with_suffix(""))
        if gt:
            found.append((f"{pdf.parent.name}/{pdf.stem}", pdf, gt, []))
    for img in sorted((FIX / "scans" / "degraded").iterdir()):
        base = img.name.split("__")[0]
        gt = ground_truth(FIX / "scans" / base)
        if gt:
            found.append((f"scans/{img.stem}", img, gt, []))
    return found


VEROVIO = threading.Lock()  # Verovio is not thread-safe


def concat(lists: list[list[tuple[float, int]]]) -> list[tuple[float, int]]:
    out, off = [], 0.0
    for notes in lists:
        out += [(t + off, n) for t, n in notes]
        off += max((t for t, _ in notes), default=0) + 8
    return out


def run_case(name: str, src: Path, gt: list[Path], extra: list[str], out: Path,
             rescore: bool = False) -> dict:
    dest = out / re.sub(r"[^\w.-]+", "_", name)
    t0 = time.monotonic()
    done = dest / "report.json"
    if rescore and done.is_file() and json.loads(done.read_text()).get("status") == "done":
        res = {"name": name, "exit": 0, "secs": 0.0, "rescored": True}
    else:
        proc = subprocess.run(
            [sys.executable, "-m", "sheet2audio.cli", str(src), "-o", str(dest), "-q",
             "--no-video", "--no-viewer", "--formats", "mp3", *extra],
            capture_output=True, text=True, cwd=ROOT,
        )
        res = {"name": name, "exit": proc.returncode, "secs": round(time.monotonic() - t0, 1)}
        if proc.returncode != 0:
            res["error"] = (proc.stderr.strip().splitlines() or ["?"])[0][:200]
            return res
    report = json.loads((dest / "report.json").read_text())
    xmls = [Path(p) for p in report["musicxml"]]
    with VEROVIO:
        preds = [midi_notes(render_musicxml(read_musicxml_bytes(x), "e").midi) for x in xmls]
    gts = [midi_notes(p.read_bytes()) for p in gt]
    g, p = concat(gts), concat(preds)
    res.update(gt_notes=len(g), pred_notes=len(p), movements=len(xmls), gt_pieces=len(gts),
               pitch=f1(lcs_len([n for _, n in g], [n for _, n in p]), len(p), len(g))["f1"],
               notes=report.get("notes", []))
    if len(gts) == 1 and len(preds) == 1:
        res["onset"] = f1(onset_matches(g, p), len(p), len(g))["f1"]
    res.update(ledger_check(report))
    return res


def ledger_check(report: dict) -> dict:
    """Our stages must keep every note; Audiveris' losses must be named."""
    c = report.get("ledger") or {}
    problems = []
    for a, b in (("exported", "cleaned"), ("cleaned", "drawn"), ("drawn", "played")):
        # notes the repair removed on purpose (named in the notes) are not lost
        # (and notes it added by splitting one into tied values do not hide a loss)
        allowed = ((c.get("removed") or 0) - (c.get("added") or 0)) if a == "exported" else 0
        if c.get(a) is not None and c.get(b) is not None and c[b] + allowed < c[a]:
            problems.append(f"{c[a] - allowed - c[b]} lost from {a} to {b}")
    omr_lost = max(0, (c.get("recognised") or 0) - (c.get("exported") or 0))
    named = any("more note" in n or "could not fit" in n for n in report.get("notes", []))
    if omr_lost and not named:
        problems.append(f"{omr_lost} lost by Audiveris without a note to the user")
    return {"ledger": c, "omr_lost": omr_lost, "ledger_problems": problems}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--filter", default="")
    ap.add_argument("--out", type=Path, default=ROOT / "out" / "corpus")
    ap.add_argument("--rescore", action="store_true",
                    help="score existing outputs instead of re-running the pipeline")
    a = ap.parse_args()
    todo = [c for c in cases() if re.search(a.filter, c[0])]
    a.out.mkdir(parents=True, exist_ok=True)
    def safe(c):
        try:
            return run_case(*c, a.out, a.rescore)
        except Exception as e:  # noqa: BLE001 - one bad case must not stop the run
            return {"name": c[0], "exit": -1, "secs": 0, "error": f"scoring failed: {e!r}"[:200]}

    with ThreadPoolExecutor(a.jobs) as pool:
        results = list(pool.map(safe, todo))
    (a.out / "results.json").write_text(json.dumps(results, indent=1))
    for r in results:
        score = (f"pitch {r['pitch']:.3f}" + (f" onset {r['onset']:.3f}" if "onset" in r else "")
                 if "pitch" in r else f"FAIL: {r.get('error')}")
        if r.get("omr_lost"):
            score += f"  omr lost {r['omr_lost']}"
        if r.get("ledger_problems"):
            score += "  LEDGER: " + "; ".join(r["ledger_problems"])
        print(f"{r['name']:48s} {r['secs']:6.1f}s  {score}")
    bad = [r["name"] for r in results if r.get("ledger_problems")]
    if bad:
        print(f"\nNote ledger broken in {len(bad)} case(s): {', '.join(bad)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
