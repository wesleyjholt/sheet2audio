"""Locate the external free/open-source programs the pipeline drives.

Every lookup honours an explicit path first, then an environment variable,
then the usual install locations on macOS and Linux.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class MissingTool(RuntimeError):
    """Raised when a required external program cannot be found."""


def _first_existing(candidates: list[Path]) -> Path | None:
    for c in candidates:
        if c.is_file() and os.access(c, os.X_OK):
            return c
    return None


def find_audiveris(explicit: str | None = None) -> Path:
    if explicit:
        p = Path(explicit).expanduser()
        if p.is_dir() and p.suffix == ".app":
            p = p / "Contents" / "MacOS" / "Audiveris"
        if p.is_file():
            return p
        raise MissingTool(f"Audiveris not found at {explicit}")
    env = os.environ.get("AUDIVERIS")
    if env:
        return find_audiveris(env)
    mac_app = Path("Contents/MacOS/Audiveris")
    candidates = [
        PROJECT_ROOT / "tools" / "Audiveris.app" / mac_app,
        Path("/Applications/Audiveris.app") / mac_app,
        Path.home() / "Applications" / "Audiveris.app" / mac_app,
        Path("/opt/audiveris/bin/Audiveris"),  # Linux .deb
    ]
    found = _first_existing(candidates)
    if found:
        return found
    for name in ("audiveris", "Audiveris"):
        w = shutil.which(name)
        if w:
            return Path(w)
    raise MissingTool(
        "Audiveris (open-source OMR, AGPL-3.0) was not found.\n"
        "  macOS: download the .dmg from https://github.com/Audiveris/audiveris/releases\n"
        "         and put Audiveris.app in /Applications or <project>/tools/.\n"
        "  Linux: install the .deb from the same page (or the flatpak).\n"
        "  Or pass --audiveris /path/to/Audiveris, or set AUDIVERIS=..."
    )


def find_executable(name: str, hint: str) -> Path:
    w = shutil.which(name)
    if w:
        return Path(w)
    for d in ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin"):
        p = Path(d) / name
        if p.is_file():
            return p
    raise MissingTool(f"'{name}' was not found on PATH. {hint}")


def find_fluidsynth() -> Path:
    return find_executable(
        "fluidsynth",
        "Install FluidSynth (LGPL-2.1): `brew install fluid-synth` or `apt install fluidsynth`.",
    )


def find_ffmpeg() -> Path:
    return find_executable(
        "ffmpeg", "Install FFmpeg (LGPL/GPL): `brew install ffmpeg` or `apt install ffmpeg`."
    )


SOUNDFONT_SUFFIXES = (".sf2", ".sf3")


def find_soundfont(explicit: str | None = None) -> Path:
    """Return a General-MIDI SoundFont with a piano preset.

    Preference order: --soundfont, $SHEET2AUDIO_SOUNDFONT, <project>/soundfonts/,
    the MIT-licensed "MS Basic" font bundled with MuseScore, then common
    Linux/Homebrew locations of FluidR3_GM / GeneralUser GS.
    """
    if explicit:
        p = Path(explicit).expanduser()
        if p.is_file():
            return p
        raise MissingTool(f"SoundFont not found: {explicit}")
    env = os.environ.get("SHEET2AUDIO_SOUNDFONT")
    if env:
        return find_soundfont(env)
    local = PROJECT_ROOT / "soundfonts"
    if local.is_dir():
        fonts = sorted(p for p in local.iterdir() if p.suffix.lower() in SOUNDFONT_SUFFIXES)
        if fonts:
            return fonts[0]
    candidates = [
        Path("/Applications/MuseScore 4.app/Contents/Resources/sound/MS Basic.sf3"),
        Path.home() / "Applications/MuseScore 4.app/Contents/Resources/sound/MS Basic.sf3",
        Path("/usr/share/mscore-4.0/sound/MS Basic.sf3"),
        Path("/usr/share/sounds/sf2/FluidR3_GM.sf2"),
        Path("/usr/share/soundfonts/FluidR3_GM.sf2"),
        Path("/usr/share/sounds/sf3/MuseScore_General.sf3"),
        Path("/usr/share/soundfonts/default.sf2"),
        Path("/opt/homebrew/share/soundfonts/default.sf2"),
        Path("/usr/local/share/soundfonts/default.sf2"),
    ]
    for c in candidates:
        if c.is_file():
            return c
    raise MissingTool(
        "No SoundFont found. Any free General-MIDI .sf2/.sf3 works, e.g. FluidR3_GM (MIT),\n"
        "GeneralUser GS, or MuseScore's 'MS Basic.sf3' (MIT). Put one in <project>/soundfonts/\n"
        "or pass --soundfont PATH."
    )


def find_musescore() -> Path | None:
    candidates = [
        Path("/Applications/MuseScore 4.app/Contents/MacOS/mscore"),
        Path.home() / "Applications/MuseScore 4.app/Contents/MacOS/mscore",
    ]
    found = _first_existing(candidates)
    if found:
        return found
    for name in ("mscore4portable", "mscore", "musescore", "mscore4"):
        w = shutil.which(name)
        if w:
            return Path(w)
    return None


def find_rsvg() -> Path | None:
    """librsvg's rsvg-convert (LGPL-2.1+), used to draw the video frames."""
    try:
        return find_executable("rsvg-convert", "")
    except MissingTool:
        return None
