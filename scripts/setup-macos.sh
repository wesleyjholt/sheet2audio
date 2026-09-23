#!/usr/bin/env bash
# Install everything sheet2audio needs on macOS (Apple Silicon or Intel).
# All of it is free/open-source software:
#   Audiveris (AGPL-3.0), FluidSynth (LGPL-2.1), FFmpeg (LGPL/GPL), librsvg (LGPL-2.1+),
#   Verovio (LGPL-3.0), mido/Pillow/pypdf/fontTools (MIT/HPND/BSD/MIT);
#   optional: MuseScore 4 (GPL-3.0), LilyPond (GPL-3.0, test fixtures only).
set -euo pipefail

AUDIVERIS_VERSION="${AUDIVERIS_VERSION:-5.11.0}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

command -v brew >/dev/null || { echo "Homebrew is required: https://brew.sh" >&2; exit 1; }
command -v uv >/dev/null || brew install uv
brew list fluid-synth >/dev/null 2>&1 || brew install fluid-synth
command -v ffmpeg >/dev/null || brew install ffmpeg
command -v rsvg-convert >/dev/null || brew install librsvg

if [ ! -x tools/Audiveris.app/Contents/MacOS/Audiveris ] && [ ! -d /Applications/Audiveris.app ]; then
  arch="$(uname -m)"; [ "$arch" = "arm64" ] || arch="x86_64"
  dmg="Audiveris-${AUDIVERIS_VERSION}-macosx-${arch}.dmg"
  mkdir -p tools
  echo "Downloading $dmg from github.com/Audiveris/audiveris ..."
  curl -fL -o "tools/$dmg" \
    "https://github.com/Audiveris/audiveris/releases/download/${AUDIVERIS_VERSION}/${dmg}"
  # The image shows Audiveris' AGPL-3.0 licence before mounting; `yes` accepts it.
  mnt="$(mktemp -d)"
  yes | hdiutil attach -nobrowse -readonly -mountpoint "$mnt" "tools/$dmg" >/dev/null
  cp -R "$mnt/Audiveris.app" tools/
  hdiutil detach "$mnt" >/dev/null
  rm -f "tools/$dmg"
fi

uv sync

if [ ! -f "/Applications/MuseScore 4.app/Contents/Resources/sound/MS Basic.sf3" ] \
   && [ -z "$(ls soundfonts/*.sf[23] 2>/dev/null)" ]; then
  echo
  echo "No SoundFont found. Either install MuseScore 4 (free, https://musescore.org; its"
  echo "'MS Basic.sf3' is used automatically) or put any General-MIDI .sf2/.sf3 file in"
  echo "$ROOT/soundfonts/."
fi
echo
echo "Ready. Try:  uv run sheet2audio tests/fixtures/waltz_g.pdf --open"
