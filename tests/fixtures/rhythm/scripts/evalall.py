import sys, json
from pathlib import Path
ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "tests"))
from evaluate import evaluate
cases = {  # name: (bar_ql, pickup)
    "pickup_ties_34": (3, 1), "pickup_68": (3, 0.5), "compound_98": (4.5, 0),
    "tuplets_44": (4, 0), "sixteenths_24": (2, 0), "rests_44": (4, 0), "pickup_volta_34": (3, 1), "pickup_repeat_44": (4, 1), "trailing_rests_44": (4, 0),
}
only = sys.argv[1:] or list(cases)
for name in only:
    bar, pu = cases[name.split(":")[0]]
    gt = ROOT / f"tests/fixtures/rhythm/{name.split(':')[0]}.midi"
    for variant in ("", "_norepair"):
        d = name.split(":")[1] if ":" in name else name.split(":")[0] + variant
        xml = ROOT / "out/rhythm" / d / f"{name.split(':')[0]}.musicxml"
        if not xml.exists():
            continue
        r = evaluate(gt, xml, bar, pu)
        print(f"{d:28s} gt={r['gt_notes']:3d} pred={r['pred_notes']:3d} pitchF1={r['pitch']['f1']:.4f} onsetF1={r['onset']['f1']:.4f} measF1={r['measure_onset']['f1']:.4f} len gt/pred={r['gt_length_ql']}/{r['pred_length_ql']}")
        if ":" in name: break
