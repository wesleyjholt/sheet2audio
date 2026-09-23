"""Quantitative audio / MIDI / timemap / viewer sync check for one sheet2audio output folder.

    uv run python tests/fixtures/sync/sync_check.py OUTDIR [--layout encoded|auto] [--json]
    uv run python tests/fixtures/sync/sync_check.py --calibrate OUTDIR

Reads OUTDIR/report.json, <stem>.html (the timemap the viewer uses), <stem>.mid (the
combined MIDI that FluidSynth played), <stem>.wav and <stem>.mp3/.flac, and the
repaired MusicXML (only to map timemap note ids to pitches via Verovio).
Writes OUTDIR/sync_expected.json (per-note intervals) for viewer_replay.js.

Stdlib + mido + verovio only. Uses sheet2audio.render / synth read-only.
"""
from __future__ import annotations

import argparse
import array
import json
import math
import operator
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

import mido

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))
from sheet2audio.render import DEFAULT_TEMPO, _base_options, _toolkit  # noqa: E402

HOP = 32  # samples per envelope frame (0.73 ms at 44.1 kHz)
WIN = 8  # frames in the rise window (5.8 ms)


# ----------------------------------------------------------------------------- inputs
def viewer_data(html_path: Path) -> dict:
    text = html_path.read_text(encoding="utf-8")
    m = re.search(r'<script type="application/json" id="data">(.*?)</script>', text, re.S)
    return json.loads(m.group(1))


def id_pitches(xml_path: Path, layout: str, report_bpm: float) -> tuple[dict, dict]:
    """id -> MIDI pitch and id -> Verovio's own onset (ms), same options as the pipeline."""
    text = xml_path.read_text(encoding="utf-8")
    opts = _base_options(layout)
    tk = _toolkit(opts, text)
    probe = tk.renderToTimemap({})
    base = next((float(e["tempo"]) for e in probe if "tempo" in e), DEFAULT_TEMPO)
    scale = report_bpm / base
    if abs(scale - 1) > 1e-9:
        opts["midiTempoAdjustment"] = scale
        tk = _toolkit(opts, text)
    tm = tk.renderToTimemap({})
    ids = {i for e in tm for i in e.get("on", [])}
    pitch, vtime = {}, {}
    for i in ids:
        v = tk.getMIDIValuesForElement(i)
        pitch[i] = v.get("pitch")
        vtime[i] = v.get("time")
    return pitch, vtime


def midi_notes(path: Path) -> list[dict]:
    t = 0.0
    open_: dict[tuple[int, int], list[float]] = {}
    out = []
    for msg in mido.MidiFile(path):
        t += msg.time
        if msg.type == "note_on" and msg.velocity > 0:
            open_.setdefault((msg.channel, msg.note), []).append(t)
        elif msg.type in ("note_off", "note_on"):
            q = open_.get((msg.channel, msg.note))
            if q:
                out.append({"pitch": msg.note, "on": q.pop(0) * 1000, "off": t * 1000})
    for (ch, n), q in open_.items():
        for on in q:
            out.append({"pitch": n, "on": on * 1000, "off": None})
    out.sort(key=lambda d: (d["on"], d["pitch"]))
    return out


def timemap_notes(data: dict) -> tuple[list[dict], set]:
    notes, svg_ids = [], set()
    for mi, mv in enumerate(data["movements"]):
        for svg in mv["svgs"]:
            svg_ids |= {f"{mi}:{i}" for i in re.findall(r'data-id="([^"]+)"', svg)}
        open_: dict[str, list[dict]] = {}
        for e in sorted(mv["timemap"], key=lambda e: e["tstamp"]):
            t = e["tstamp"] + mv["offset_ms"]
            for i in e.get("off", []):
                q = open_.get(i)
                if q:
                    q.pop(0)["off"] = t
            for i in e.get("on", []):
                d = {"mv": mi, "id": i, "key": f"{mi}:{i}", "on": t, "off": None}
                open_.setdefault(i, []).append(d)
                notes.append(d)
    return notes, svg_ids


def match(tm: list[dict], md: list[dict], tol_ms: float = 150.0) -> list[tuple[dict, dict]]:
    by_pitch: dict[int, list[dict]] = {}
    for n in md:
        by_pitch.setdefault(n["pitch"], []).append(n)
    used = set()
    pairs = []
    for n in sorted(tm, key=lambda d: d["on"]):
        best, bd = None, tol_ms
        for c in by_pitch.get(n.get("pitch"), []):
            if id(c) in used:
                continue
            dd = abs(c["on"] - n["on"])
            if dd <= bd:
                best, bd = c, dd
        if best is not None:
            used.add(id(best))
            pairs.append((n, best))
    return pairs


# ----------------------------------------------------------------------------- audio
def read_mono(path: Path) -> tuple[array.array, int]:
    with wave.open(str(path), "rb") as w:
        ch, sw, sr, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        raw = w.readframes(n)
    if sw != 2:
        raise SystemExit(f"{path}: expected 16-bit PCM")
    a = array.array("h")
    a.frombytes(raw)
    if sys.byteorder == "big":
        a.byteswap()
    if ch == 2:
        return array.array("i", map(operator.add, a[0::2], a[1::2])), sr
    return array.array("i", a), sr


def hf_energy(x: array.array) -> list[float]:
    """Energy of the first difference (a crude high-pass that favours attacks) per HOP."""
    d = list(map(operator.sub, x[1:], x[:-1]))
    sq = list(map(operator.mul, d, d))
    return [float(sum(sq[k:k + HOP])) for k in range(0, len(sq) - HOP + 1, HOP)]


def rise(e: list[float]) -> list[float]:
    """dB rise of a WIN-frame energy window over the WIN frames before it (onset function)."""
    c = [0.0]
    for v in e:
        c.append(c[-1] + v)
    out = [0.0] * len(e)
    for k in range(2 * WIN, len(e)):
        a = c[k + 1] - c[k + 1 - WIN]
        b = c[k + 1 - WIN] - c[k + 1 - 2 * WIN]
        out[k] = 10 * math.log10((a + 1e3) / (b + 1e3))
    return out


def onset_groups(md: list[dict], merge_ms: float = 5.0) -> list[float]:
    ts = sorted(n["on"] for n in md)
    g = []
    for t in ts:
        if not g or t - g[-1] > merge_ms:
            g.append(t)
    return g


def audio_onsets(wav: Path, md: list[dict], bias_ms: float = 0.0) -> dict:
    x, sr = read_mono(wav)
    fr_ms = 1000 * HOP / sr
    r = rise(hf_energy(x))
    groups = onset_groups(md)
    # 1) systematic lag: sum of the onset function at every MIDI onset, lags -50..+150 ms
    best = (-1e9, 0)
    for lag_f in range(int(-50 / fr_ms), int(150 / fr_ms) + 1):
        s = 0.0
        for t in groups:
            k = int(round(t / fr_ms)) + lag_f
            if 0 <= k < len(r):
                s += r[k]
        best = max(best, (s, lag_f))
    lag_ms = best[1] * fr_ms
    # 2) per-onset: argmax of the onset function within +-25 ms of MIDI onset + lag
    errs = []
    isolated = [t for i, t in enumerate(groups) if i == 0 or t - groups[i - 1] >= 60]
    for t in isolated:
        k0 = int(round((t + lag_ms - 25) / fr_ms))
        k1 = int(round((t + lag_ms + 25) / fr_ms))
        seg = r[max(k0, 0):max(k1, 0)]
        if not seg:
            continue
        k = max(range(len(seg)), key=seg.__getitem__) + max(k0, 0)
        errs.append((k * fr_ms - bias_ms) - t)
    # 3) blind peak picking (no MIDI knowledge), matched to MIDI onsets afterwards
    peaks = []
    half = int(round(30 / fr_ms))
    for k in range(1, len(r) - 1):
        if r[k] >= 6.0 and r[k] == max(r[max(0, k - half):k + half + 1]):
            if not peaks or k - peaks[-1] > half:
                peaks.append(k)
    pk_ms = [k * fr_ms - bias_ms for k in peaks]
    hit, used = 0, set()
    blind_err = []
    for t in groups:
        cands = [(abs(p - (t + lag_ms - bias_ms * 0)), j) for j, p in enumerate(pk_ms)
                 if j not in used and abs(p - (t + lag_ms)) <= 30]
        if cands:
            _, j = min(cands)
            used.add(j)
            hit += 1
            blind_err.append(pk_ms[j] - t)
    med = statistics.median(errs) if errs else float("nan")
    return {
        "sample_rate": sr,
        "wav_duration_s": round(len(x) / sr, 4),
        "onset_groups": len(groups),
        "xcorr_lag_ms": round(lag_ms - bias_ms, 2),
        "isolated_onsets_measured": len(errs),
        "latency_median_ms": round(med, 2),
        "latency_min_ms": round(min(errs), 2) if errs else None,
        "latency_max_ms": round(max(errs), 2) if errs else None,
        "max_abs_dev_from_median_ms": round(max(abs(e - med) for e in errs), 2) if errs else None,
        "max_abs_error_ms": round(max(abs(e) for e in errs), 2) if errs else None,
        "blind_peaks": len(peaks),
        "blind_recall": round(hit / len(groups), 4) if groups else None,
        "blind_precision": round(hit / len(peaks), 4) if peaks else None,
        "blind_max_abs_error_ms": round(max(abs(e) for e in blind_err), 2) if blind_err else None,
        "blind_median_error_ms": round(statistics.median(blind_err), 2) if blind_err else None,
        "tail_last_50ms_dbfs": _dbfs(x[-int(0.05 * sr):]),
    }


def _dbfs(seg) -> float | None:
    if not len(seg):
        return None
    rms = math.sqrt(sum(v * v for v in seg) / len(seg)) / 2  # mono = L+R
    return round(20 * math.log10(rms / 32768), 1) if rms > 0 else -999.0


def first_sound(x: array.array, sr: int, thresh: int = 200) -> float:
    for i, v in enumerate(x):
        if abs(v) > thresh:
            return 1000 * i / sr
    return float("nan")


def align(ref: array.array, other: array.array, sr: int, max_lag_ms: float = 120) -> dict:
    """Lag (ms) of `other` relative to `ref`: coarse on envelopes, fine on samples."""
    er, eo = hf_energy(ref[: sr * 8]), hf_energy(other[: sr * 8])
    m = min(len(er), len(eo))
    L = int(max_lag_ms / (1000 * HOP / sr))
    best = max((sum(a * b for a, b in zip(er[: m - L], eo[lag: m - L + lag])), lag)
               for lag in range(0, L))
    best_neg = max((sum(a * b for a, b in zip(er[lag: m - L + lag], eo[: m - L])), -lag)
                   for lag in range(0, L))
    coarse = max(best, best_neg)[1] * HOP
    # fine: raw-sample correlation over 1 s around the loudest part of the first 8 s
    start = max(range(0, len(er) - 1400, 100), key=lambda k: sum(er[k:k + 1378])) * HOP
    seg = ref[start:start + sr]
    fine = max(
        (sum(map(operator.mul, seg, other[start + lag:start + lag + len(seg)])), lag)
        for lag in range(coarse - 2 * HOP, coarse + 2 * HOP + 1)
        if start + lag >= 0
    )[1]
    num = sum(map(operator.mul, seg, other[start + fine:start + fine + len(seg)]))
    den = math.sqrt(sum(map(operator.mul, seg, seg)) *
                    sum(v * v for v in other[start + fine:start + fine + len(seg)])) or 1
    return {"lag_samples": fine, "lag_ms": round(1000 * fine / sr, 3), "corr": round(num / den, 4),
            "first_sound_ms": round(first_sound(other, sr), 3),
            "duration_s": round(len(other) / sr, 4)}


def mp3_checks(outdir: Path, stem: str, wav: Path) -> dict:
    ref, sr = read_mono(wav)
    res = {"wav_first_sound_ms": round(first_sound(ref, sr), 3), "wav_duration_s": round(len(ref) / sr, 4)}
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        for fmt in ("mp3", "flac", "ogg"):
            src = outdir / f"{stem}.{fmt}"
            if not src.exists():
                continue
            d = tmp / f"{fmt}.wav"
            subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
                            "-c:a", "pcm_s16le", str(d)], check=True)
            res[f"{fmt}_ffmpeg"] = align(ref, read_mono(d)[0], sr)
            if fmt == "mp3":
                # the same file with the LAME/Xing gapless info ignored (raw decoder output)
                d2 = tmp / "mp3raw.wav"
                subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                                "-flags2", "+skip_manual", "-i", str(src), "-c:a", "pcm_s16le",
                                str(d2)], check=True)
                res["mp3_ffmpeg_no_gapless_trim"] = align(ref, read_mono(d2)[0], sr)
                if shutil.which("afconvert"):  # macOS CoreAudio (what Safari uses)
                    d3 = tmp / "mp3_coreaudio.wav"
                    subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16", str(src), str(d3)],
                                   check=True)
                    res["mp3_coreaudio_afconvert"] = align(ref, read_mono(d3)[0], sr)
                p = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                                    "format=duration:stream=start_time,duration,nb_frames",
                                    "-of", "json", str(src)], capture_output=True, text=True)
                res["mp3_ffprobe"] = json.loads(p.stdout)
    return res


# ----------------------------------------------------------------------------- main
def check(outdir: Path, layout: str, bias_ms: float) -> dict:
    rep = json.loads((outdir / "report.json").read_text())
    stem = Path(rep["midi"]).stem
    data = viewer_data(outdir / f"{stem}.html")
    tm, svg_ids = timemap_notes(data)
    vt_err = []
    for mi, (xml, mv) in enumerate(zip(rep["musicxml"], rep["movements"])):
        pitch, vtime = id_pitches(Path(xml), layout, mv["bpm"])
        for n in tm:
            if n["mv"] == mi:
                n["pitch"] = pitch.get(n["id"])
                if vtime.get(n["id"]) is not None:
                    vt_err.append(abs(vtime[n["id"]] + mv["offset_s"] * 1000 - n["on"]))
    md = midi_notes(outdir / f"{stem}.mid")
    pairs = match(tm, md)
    d_on = [m["on"] - t["on"] for t, m in pairs]
    d_off = [m["off"] - t["off"] for t, m in pairs if m["off"] is not None and t["off"] is not None]
    worst = max(pairs, key=lambda p: abs(p[1]["on"] - p[0]["on"])) if pairs else None
    mf = mido.MidiFile(outdir / f"{stem}.mid")
    res = {
        "outdir": str(outdir),
        "movements": rep["movements"],
        "timemap_notes": len(tm),
        "midi_notes": len(md),
        "matched": len(pairs),
        "timemap_unmatched": len(tm) - len(pairs),
        "midi_unmatched": len(md) - len(pairs),
        "timemap_ids_missing_in_svg": sorted({n["key"] for n in tm} - svg_ids)[:10],
        "timemap_ids_without_pitch": sum(1 for n in tm if n.get("pitch") is None),
        "onset_max_abs_err_ms": round(max(map(abs, d_on)), 3) if d_on else None,
        "onset_mean_err_ms": round(statistics.mean(d_on), 3) if d_on else None,
        "onset_worst": ({"key": worst[0]["key"], "pitch": worst[0]["pitch"], "timemap_ms": worst[0]["on"],
                         "midi_ms": round(worst[1]["on"], 3)} if worst else None),
        "offset_max_abs_err_ms": round(max(map(abs, d_off)), 3) if d_off else None,
        "offset_mean_err_ms": round(statistics.mean(d_off), 3) if d_off else None,
        "verovio_element_time_max_abs_err_ms": round(max(vt_err), 3) if vt_err else None,
        "midi_length_s": round(mf.length, 4),
        "last_note_off_s": round(max(n["off"] or n["on"] for n in md) / 1000, 4),
        "timemap_last_event_s": round(max(e["tstamp"] + mv["offset_ms"] for mv in data["movements"]
                                          for e in mv["timemap"]) / 1000, 4),
    }
    wav = outdir / f"{stem}.wav"
    if wav.exists():
        res["audio"] = audio_onsets(wav, md, bias_ms)
        res["audio"]["tail_after_last_note_off_s"] = round(
            res["audio"]["wav_duration_s"] - res["last_note_off_s"], 4)
        res["encoded"] = mp3_checks(outdir, stem, wav)
    unmatched_tm = [n for n in tm if all(n is not t for t, _ in pairs)]
    res["timemap_unmatched_examples"] = [{k: n.get(k) for k in ("key", "pitch", "on", "off")}
                                         for n in unmatched_tm[:5]]
    expected = {
        "notes": [{"key": t["key"], "pitch": t["pitch"], "tm_on": t["on"], "tm_off": t["off"],
                   "midi_on": round(m["on"], 3), "midi_off": None if m["off"] is None else round(m["off"], 3)}
                  for t, m in pairs]
                 + [{"key": n["key"], "pitch": n.get("pitch"), "tm_on": n["on"], "tm_off": n["off"],
                     "midi_on": None, "midi_off": None} for n in unmatched_tm],
        "end_ms": res["midi_length_s"] * 1000,
    }
    (outdir / "sync_expected.json").write_text(json.dumps(expected))
    return res


def calibrate(outdir: Path) -> dict:
    """Isolated notes -> FluidSynth: true attack start vs MIDI time, and this script's detector bias."""
    from sheet2audio import synth, tools
    outdir.mkdir(parents=True, exist_ok=True)
    mf = mido.MidiFile(type=0, ticks_per_beat=960)
    tr = mido.MidiTrack()
    mf.tracks.append(tr)
    tr.append(mido.MetaMessage("set_tempo", tempo=500000, time=0))
    tr.append(mido.Message("program_change", program=0, time=0))
    pitches = [36, 48, 55, 60, 64, 67, 72, 79, 84, 96, 60, 60]
    times = [0.0] + [0.2 + 1.5 * i + 0.0137 * i for i in range(1, len(pitches))]  # t=0, then off-grid
    now = 0
    events = []
    for p, t in zip(pitches, times):
        events += [(t, mido.Message("note_on", note=p, velocity=90)),
                   (t + 0.3, mido.Message("note_on", note=p, velocity=0))]
    for t, msg in sorted(events, key=lambda e: e[0]):
        tick = int(round(t * 1920))
        tr.append(msg.copy(time=tick - now))
        now = tick
    tr.append(mido.MetaMessage("end_of_track", time=3840))
    mid = outdir / "calib.mid"
    mf.save(mid)
    wav = outdir / "calib.wav"
    synth.render_wav(tools.find_fluidsynth(), tools.find_soundfont(None), mid, wav)
    x, sr = read_mono(wav)
    fr_ms = 1000 * HOP / sr
    r = rise(hf_energy(x))
    rows = []
    for p, t in zip(pitches, times):
        i0 = max(int((t - 0.02) * sr), 0)
        seg = x[i0:int((t + 0.2) * sr)]
        peak = max(abs(v) for v in seg)
        bg = max((abs(v) for v in x[max(int((t - 0.06) * sr), 0):i0]), default=0)
        th = max(peak * 0.01, 4 * bg, 20)  # -40 dB re. note peak, and 4x the ringing before it
        first = next(i for i, v in enumerate(seg) if abs(v) > th) + i0
        k0, k1 = int((t * 1000 - 20) / fr_ms), int((t * 1000 + 80) / fr_ms)
        k = max(range(max(k0, 2 * WIN), k1), key=r.__getitem__)
        rows.append({"pitch": p, "midi_ms": round(t * 1000, 3),
                     "attack_start_ms": round(1000 * first / sr - t * 1000, 3),
                     "detector_ms": round(k * fr_ms - t * 1000, 3) if t > 0.1 else None})
    att = [r_["attack_start_ms"] for r_ in rows]
    det = [r_["detector_ms"] - r_["attack_start_ms"] for r_ in rows if r_["detector_ms"] is not None]
    return {"notes": rows, "attack_latency_ms": {"min": min(att), "median": statistics.median(att),
                                                 "max": max(att)},
            "detector_bias_ms": {"min": round(min(det), 3), "median": round(statistics.median(det), 3),
                                 "max": round(max(det), 3)},
            "wav_duration_s": round(len(x) / sr, 4), "midi_length_s": round(mido.MidiFile(mid).length, 4)}


def diff_onsets(outdir: Path, n_notes: int, extra_ms: list[float] | None = None, jobs: int = 4) -> dict:
    """Sample-exact onset of individual notes inside the real mix.

    FluidSynth is deterministic, so rendering the combined MIDI with one note removed and
    subtracting it from the full render leaves exactly that note's contribution. Its first
    non-zero sample is where the note starts sounding in the audio the viewer plays.
    """
    from concurrent.futures import ThreadPoolExecutor

    from sheet2audio import synth, tools
    rep = json.loads((outdir / "report.json").read_text())
    mid_path = Path(rep["midi"])
    fs, sf = tools.find_fluidsynth(), tools.find_soundfont(None)
    mf = mido.MidiFile(mid_path)
    tr = mf.tracks[0]
    abs_t, t = [], 0
    for msg in tr:
        t += msg.time
        abs_t.append(t)
    tps = mf.ticks_per_beat * 1_000_000 / 500000  # combine_midi writes a fixed 120 BPM
    ons = [i for i, m in enumerate(tr) if m.type == "note_on" and m.velocity > 0]
    # evenly spread sample of notes + the first note after each requested time (e.g. tempo changes)
    pick = sorted({ons[round(j * (len(ons) - 1) / max(n_notes - 1, 1))] for j in range(n_notes)})
    for ms in extra_ms or []:
        cand = [i for i in ons if abs_t[i] / tps * 1000 >= ms - 1]
        if cand:
            pick.append(cand[0])
    pick = sorted(set(pick))
    tmp = Path(tempfile.mkdtemp(prefix="syncdiff-"))
    full = tmp / "full.wav"

    def without(i: int) -> Path:
        m = tr[i]
        off = next(j for j in range(i + 1, len(tr)) if tr[j].type in ("note_on", "note_off")
                   and tr[j].note == m.note and tr[j].channel == m.channel
                   and (tr[j].type == "note_off" or tr[j].velocity == 0))
        new = mido.MidiTrack()
        prev = 0
        for j, msg in enumerate(tr):
            if j in (i, off):
                continue
            new.append(msg.copy(time=abs_t[j] - prev))
            prev = abs_t[j]
        out = mido.MidiFile(type=0, ticks_per_beat=mf.ticks_per_beat)
        out.tracks.append(new)
        p = tmp / f"wo{i}.mid"
        out.save(p)
        w = tmp / f"wo{i}.wav"
        synth.render_wav(fs, sf, p, w)
        return w

    with ThreadPoolExecutor(jobs) as ex:
        fut_full = ex.submit(synth.render_wav, fs, sf, mid_path, full)
        futs = {i: ex.submit(without, i) for i in pick}
        fut_full.result()
        a, sr = read_mono(full)
        rows = []
        for i, f in futs.items():
            b, _ = read_mono(f.result())
            midi_ms = abs_t[i] / tps * 1000
            start = max(int((midi_ms - 50) * sr / 1000), 0)
            d = [abs(u - v) for u, v in zip(a[start:start + sr], b[start:start + sr])]
            pk = max(d) if d else 0
            first = next((k for k, v in enumerate(d) if v > 2), None)
            first40 = next((k for k, v in enumerate(d) if v > pk * 0.01), None)
            rows.append({
                "pitch": tr[i].note, "midi_ms": round(midi_ms, 3),
                "first_nonzero_ms": None if first is None else round((start + first) * 1000 / sr - midi_ms, 3),
                "first_above_-40dB_ms": None if first40 is None else round((start + first40) * 1000 / sr - midi_ms, 3),
            })
    lat = [r["first_nonzero_ms"] for r in rows if r["first_nonzero_ms"] is not None]
    shutil.rmtree(tmp, ignore_errors=True)
    return {"notes_tested": len(rows), "not_found": sum(1 for r in rows if r["first_nonzero_ms"] is None),
            "latency_ms": {"min": min(lat), "median": statistics.median(lat), "max": max(lat)} if lat else None,
            "rows": rows}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("outdir", type=Path)
    ap.add_argument("--layout", default="encoded")
    ap.add_argument("--bias-ms", type=float, default=0.0,
                    help="detector bias from --calibrate, subtracted from audio onset times")
    ap.add_argument("--calibrate", action="store_true")
    ap.add_argument("--diff-notes", type=int, default=0,
                    help="measure N notes sample-exactly by leave-one-out FluidSynth renders")
    ap.add_argument("--diff-at-ms", default="", help="also test the first note at/after these times")
    a = ap.parse_args()
    if a.calibrate:
        out = calibrate(a.outdir)
    elif a.diff_notes:
        extra = [float(v) for v in a.diff_at_ms.split(",") if v.strip()]
        out = diff_onsets(a.outdir, a.diff_notes, extra)
    else:
        out = check(a.outdir, a.layout, a.bias_ms)
    print(json.dumps(out, indent=1))
