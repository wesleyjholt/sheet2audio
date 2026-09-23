"""MIDI -> audio with FluidSynth (LGPL-2.1), then encode with FFmpeg (LGPL/GPL)."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

SAMPLE_RATE = 44100
PEAK_TARGET_DB = -1.0
FADE_S = 0.5

FORMATS = ("mp3", "wav", "flac", "m4a", "ogg")

# Encoder preferences per format; the first one this FFmpeg has is used.
_CODECS: dict[str, list[tuple[str, list[str]]]] = {
    "wav": [("pcm_s16le", ["-c:a", "pcm_s16le"])],
    "mp3": [("libmp3lame", ["-c:a", "libmp3lame", "-q:a", "2"])],
    "flac": [("flac", ["-c:a", "flac"])],
    "m4a": [("aac", ["-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart"])],
    "ogg": [("libvorbis", ["-c:a", "libvorbis", "-q:a", "6"]),
            ("libopus", ["-c:a", "libopus", "-b:a", "128k"]),
            ("vorbis", ["-c:a", "vorbis", "-strict", "-2", "-q:a", "6"])],
}


class SynthError(RuntimeError):
    pass


def encoders(ffmpeg: Path) -> set[str]:
    proc = subprocess.run([str(ffmpeg), "-hide_banner", "-encoders"], capture_output=True, text=True)
    return set(re.findall(r"^\s*[VAS][\w.]{5}\s+(\S+)", proc.stdout, re.MULTILINE))


def plan_codecs(ffmpeg: Path, formats: list[str]) -> dict[str, list[str]]:
    """FFmpeg arguments for each format. Fails before any slow work if one
    format has no encoder in this FFmpeg."""
    have = encoders(ffmpeg)
    plan = {}
    for fmt in formats:
        choice = next((args for name, args in _CODECS[fmt] if name in have), None)
        if choice is None:
            names = ", ".join(n for n, _ in _CODECS[fmt])
            raise SynthError(f"This FFmpeg cannot write .{fmt} (it has none of: {names}).")
        plan[fmt] = choice
    return plan


def render_wav(fluidsynth: Path, soundfont: Path, midi: Path, wav: Path) -> None:
    cmd = [
        str(fluidsynth),
        "-n",  # no MIDI input
        "-i",  # no interactive shell
        "-q",
        "-o", "synth.dynamic-sample-loading=1",  # load only the samples used: 4x faster, same audio
        "-g", "0.5",
        "-r", str(SAMPLE_RATE),
        "-T", "wav",
        "-O", "s16",
        "-F", str(wav),
        str(soundfont),
        str(midi),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or not wav.is_file() or wav.stat().st_size <= 44:
        raise SynthError(f"FluidSynth failed:\n{proc.stdout}\n{proc.stderr}")


def peak_db(ffmpeg: Path, audio: Path, end_s: float | None = None) -> float | None:
    cmd = [str(ffmpeg), "-hide_banner", "-nostats", "-i", str(audio)]
    if end_s:
        cmd += ["-t", f"{end_s:.3f}"]
    cmd += ["-af", "volumedetect", "-f", "null", "-"]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    m = re.search(r"max_volume:\s*(-?[\d.]+|-inf) dB", proc.stderr)
    if not m or m.group(1) == "-inf":
        return None
    return float(m.group(1))


def audio_filter(gain_db: float, end_s: float) -> str:
    """Peak-normalise, and fade out over the last FADE_S before `end_s`.

    FluidSynth keeps rendering until every voice has died away, which for the
    highest piano notes can be half a minute of near-silence; the audio is cut
    at `end_s` instead."""
    return f"volume={gain_db:.2f}dB,afade=t=out:st={max(0.0, end_s - FADE_S):.3f}:d={FADE_S}"


def encode(ffmpeg: Path, raw_wav: Path, outputs: dict[str, Path],
           codecs: dict[str, list[str]], end_s: float) -> float:
    """Write each requested format. Returns the gain applied, in dB."""
    peak = peak_db(ffmpeg, raw_wav, end_s)
    gain = 0.0 if peak is None else PEAK_TARGET_DB - peak
    for fmt, dest in outputs.items():
        part = partial_name(dest)  # never leave a half-written file under the final name
        cmd = [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y", "-i", str(raw_wav),
               "-t", f"{end_s:.3f}", "-af", audio_filter(gain, end_s), *codecs[fmt], str(part)]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True)
            if proc.returncode != 0:
                raise SynthError(f"FFmpeg could not write {dest.name}:\n{proc.stderr}")
            os.replace(part, dest)
        finally:
            part.unlink(missing_ok=True)
    return gain


def partial_name(dest: Path) -> Path:
    """A hidden sibling with the same extension (FFmpeg picks the format from it)."""
    return dest.with_name(f".{dest.stem}.partial{dest.suffix}")


# ---------------------------------------------------------------- sample sheet

# Programs whose notes sustain (organ, strings, choir, winds, pads): their
# samples get a steady middle section the player can loop for long notes.
SUSTAINED = set(range(16, 24)) | set(range(40, 56)) | set(range(56, 80)) | set(range(80, 96))
_PRE_S = 0.05


def sample_sheet_midi(needs: dict[int, set[int]]) -> tuple[bytes, dict[str, dict[str, list]]]:
    """A MIDI file that plays every needed (program, pitch) once, one after the
    other, and where each sample sits in the rendered audio:
    {program: {pitch: [start s, length s, loop start s, loop end s]}} (loop
    points only for sustaining programs)."""
    import mido

    tpb, ticks_per_s = 960, 1920
    events = []
    layout: dict[str, dict[str, list]] = {}
    channels = [c for c in range(16) if c != 9]
    t = 0.0
    for k, (prog, pitches) in enumerate(sorted(needs.items())):
        ch = channels[k % len(channels)]
        events.append((0, 0, mido.Message("program_change", channel=ch, program=prog)))
        sustained = prog in SUSTAINED
        hold, window = (6.0, 6.6) if sustained else (3.6, 4.4)
        for pitch in sorted(pitches):
            on = t + _PRE_S
            events.append((int(on * ticks_per_s), 2, mido.Message("note_on", channel=ch,
                                                                   note=pitch, velocity=90)))
            events.append((int((on + hold) * ticks_per_s), 1,
                           mido.Message("note_off", channel=ch, note=pitch)))
            entry = [round(on, 4), round(window - _PRE_S - 0.05, 4)]
            if sustained:
                entry += [round(on + 1.5, 4), round(on + 5.5, 4)]
            layout.setdefault(str(prog), {})[str(pitch)] = entry
            t += window
    events.sort(key=lambda e: (e[0], e[1]))
    mf = mido.MidiFile(type=0, ticks_per_beat=tpb)
    tr = mido.MidiTrack()
    mf.tracks.append(tr)
    tr.append(mido.MetaMessage("set_tempo", tempo=500000))
    now = 0
    for tick, _, msg in events:
        tr.append(msg.copy(time=tick - now))
        now = tick
    tr.append(mido.MetaMessage("end_of_track", time=ticks_per_s))
    import io

    buf = io.BytesIO()
    mf.save(file=buf)
    return buf.getvalue(), layout


def render_sample_sheet(fluidsynth: Path, ffmpeg: Path, soundfont: Path,
                        needs: dict[int, set[int]], dest: Path, tmp: Path) -> dict:
    """Render the sample sheet to a mono MP3 (`dest`); returns its layout."""
    midi, layout = sample_sheet_midi(needs)
    mid = tmp / "samples.mid"
    wav = tmp / "samples.wav"
    mid.write_bytes(midi)
    render_wav(fluidsynth, soundfont, mid, wav)
    peak = peak_db(ffmpeg, wav)
    gain = 0.0 if peak is None else PEAK_TARGET_DB - peak
    part = partial_name(dest)
    try:
        proc = subprocess.run(
            [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y", "-i", str(wav),
             "-af", f"volume={gain:.2f}dB", "-ac", "1", "-ar", "32000",
             "-c:a", "libmp3lame", "-b:a", "96k", str(part)],
            capture_output=True, text=True)
        if proc.returncode != 0:
            raise SynthError(f"FFmpeg could not write the sample sheet:\n{proc.stderr}")
        os.replace(part, dest)
    finally:
        part.unlink(missing_ok=True)
    return layout


def silent_mp3(ffmpeg: Path, dest: Path) -> None:
    """Half a second of silence. Playing it (looped) lets iPhones play the
    page's Web Audio even when the ring/silent switch is on silent."""
    subprocess.run([str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                    "-i", "anullsrc=r=22050:cl=mono", "-t", "0.5", "-c:a", "libmp3lame",
                    "-b:a", "32k", str(dest)], capture_output=True, check=True)
