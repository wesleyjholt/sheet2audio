"""Score a transcription against ground truth.

    uv run python tests/evaluate.py GROUND_TRUTH.mid PIPELINE_OUTPUT.musicxml [--json]

Both sides are reduced to (onset in quarter notes, MIDI pitch) pairs. The
pipeline side is rendered with the same Verovio call the pipeline uses, so
what is scored is what you hear.

Metrics
  pitch_f1   order-aware pitch agreement: longest common subsequence of the
             two note lists sorted by (onset, pitch). Ignores rhythm drift.
  onset_f1   note matches with the same pitch whose onsets agree within 1/48
             quarter note. A rhythm error shifts every later onset, so this
             is strict; `measure_onset_f1` is the per-measure-aligned version.
  measure_onset_f1  onsets compared relative to the start of their measure,
             measures matched by index (a local rhythm error stays local).
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from collections import Counter
from pathlib import Path

import mido

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sheet2audio.musicxml import read_musicxml_bytes  # noqa: E402
from sheet2audio.render import render_musicxml  # noqa: E402

TOL = 1 / 48


def midi_notes(data: bytes) -> list[tuple[float, int]]:
    mf = mido.MidiFile(file=io.BytesIO(data))
    tpb = mf.ticks_per_beat
    notes = []
    for tr in mf.tracks:
        t = 0
        for msg in tr:
            t += msg.time
            if msg.type == "note_on" and msg.velocity > 0 and getattr(msg, "channel", 0) != 9:
                notes.append((t / tpb, msg.note))
    return sorted(notes)


def lcs_len(a: list, b: list) -> int:
    if not a or not b:
        return 0
    prev = [0] * (len(b) + 1)
    for x in a:
        cur = [0] * (len(b) + 1)
        for j, y in enumerate(b, 1):
            cur[j] = prev[j - 1] + 1 if x == y else max(prev[j], cur[j - 1])
        prev = cur
    return prev[-1]


def f1(tp: int, n_pred: int, n_gt: int) -> dict:
    p = tp / n_pred if n_pred else 0.0
    r = tp / n_gt if n_gt else 0.0
    return {"precision": round(p, 4), "recall": round(r, 4),
            "f1": round(2 * p * r / (p + r), 4) if p + r else 0.0}


def onset_matches(gt: list[tuple[float, int]], pred: list[tuple[float, int]]) -> int:
    q = lambda t: round(t / TOL)  # noqa: E731
    return sum((Counter((q(t), n) for t, n in gt) & Counter((q(t), n) for t, n in pred)).values())


def per_measure(notes: list[tuple[float, int]], bar_starts: list[float]) -> list[tuple[int, float, int]]:
    import bisect
    out = []
    for t, n in notes:
        i = max(0, bisect.bisect_right(bar_starts, t + 1e-9) - 1)
        out.append((i, t - bar_starts[i], n))
    return out


def bar_starts_from_gt(total: float, bar_ql: float, pickup: float) -> list[float]:
    starts = [0.0]
    t = pickup if pickup else bar_ql
    while t < total + bar_ql:
        starts.append(t)
        t += bar_ql
    return starts


def evaluate(gt_midi: Path, musicxml: Path, bar_ql: float | None = None, pickup: float = 0.0) -> dict:
    gt = midi_notes(Path(gt_midi).read_bytes())
    r = render_musicxml(read_musicxml_bytes(Path(musicxml)), "eval")
    pred = midi_notes(r.midi)
    res = {
        "gt_notes": len(gt),
        "pred_notes": len(pred),
        "pitch": f1(lcs_len([n for _, n in gt], [n for _, n in pred]), len(pred), len(gt)),
        "onset": f1(onset_matches(gt, pred), len(pred), len(gt)),
        "gt_length_ql": round(max((t for t, _ in gt), default=0), 3),
        "pred_length_ql": round(max((t for t, _ in pred), default=0), 3),
    }
    # Measure-aligned onsets, using each side's own measure starts from the timemap
    pred_bars = sorted({e["qstamp"] for e in r.timemap if "measureOn" in e})
    if bar_ql:
        gt_bars = bar_starts_from_gt(res["gt_length_ql"], bar_ql, pickup)
        g = per_measure(gt, gt_bars)
        p = per_measure(pred, pred_bars or [0.0])
        q = lambda x: (x[0], round(x[1] / TOL), x[2])  # noqa: E731
        tp = sum((Counter(map(q, g)) & Counter(map(q, p))).values())
        res["measure_onset"] = f1(tp, len(pred), len(gt))
    return res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("ground_truth_midi", type=Path)
    ap.add_argument("musicxml", type=Path)
    ap.add_argument("--bar", type=float, help="bar length in quarter notes (enables measure_onset)")
    ap.add_argument("--pickup", type=float, default=0.0, help="pickup length in quarter notes")
    a = ap.parse_args()
    print(json.dumps(evaluate(a.ground_truth_midi, a.musicxml, a.bar, a.pickup), indent=2))


if __name__ == "__main__":
    main()
