"""MIDI -> audio with FluidSynth (LGPL-2.1), then encode with FFmpeg (LGPL/GPL)."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

SAMPLE_RATE = 44100
PEAK_TARGET_DB = -1.0

AUDIO_CODECS = {
    "wav": ["-c:a", "pcm_s16le"],
    "mp3": ["-c:a", "libmp3lame", "-q:a", "2"],
    "flac": ["-c:a", "flac"],
    "ogg": ["-c:a", "libvorbis", "-q:a", "6"],
}


class SynthError(RuntimeError):
    pass


def render_wav(fluidsynth: Path, soundfont: Path, midi: Path, wav: Path) -> None:
    cmd = [
        str(fluidsynth),
        "-n",  # no MIDI input
        "-i",  # no interactive shell
        "-q",
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


def peak_db(ffmpeg: Path, audio: Path) -> float | None:
    proc = subprocess.run(
        [str(ffmpeg), "-hide_banner", "-nostats", "-i", str(audio), "-af", "volumedetect",
         "-f", "null", "-"],
        capture_output=True, text=True,
    )
    m = re.search(r"max_volume:\s*(-?[\d.]+|-inf) dB", proc.stderr)
    if not m or m.group(1) == "-inf":
        return None
    return float(m.group(1))


def encode(ffmpeg: Path, raw_wav: Path, outputs: dict[str, Path]) -> None:
    """Peak-normalise `raw_wav` to PEAK_TARGET_DB and write each requested format."""
    peak = peak_db(ffmpeg, raw_wav)
    gain = 0.0 if peak is None else PEAK_TARGET_DB - peak
    for fmt, dest in outputs.items():
        cmd = [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y", "-i", str(raw_wav),
               "-af", f"volume={gain:.2f}dB", *AUDIO_CODECS[fmt], str(dest)]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise SynthError(f"FFmpeg could not write {dest.name}:\n{proc.stderr}")
