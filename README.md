# sheet2audio

Turn a PDF (or a photo/scan) of piano sheet music into:

- **audio**: MP3/WAV (optionally FLAC/OGG),
- **a play-along page**: one self-contained HTML file that shows the score and highlights every note as it sounds. Click a note to jump there. Speed control and auto-scroll are built in.
- **MIDI and MusicXML**, so you can open the music in MuseScore, which also highlights notes during playback and lets you correct mistakes.

Every component is free and open-source software:

| Step | Tool | License |
|---|---|---|
| Optical music recognition (PDF/image → MusicXML) | [Audiveris](https://github.com/Audiveris/audiveris) 5.11 | AGPL-3.0 |
| Engraving, MIDI, note timing map | [Verovio](https://www.verovio.org) 6.3 (Python) | LGPL-3.0 |
| MIDI → audio | [FluidSynth](https://www.fluidsynth.org) 2.x | LGPL-2.1 |
| Piano sound | "MS Basic" SoundFont bundled with MuseScore 4 (from FluidR3) | MIT |
| Encoding | [FFmpeg](https://ffmpeg.org) | LGPL/GPL |
| MIDI file handling | [mido](https://github.com/mido/mido) | MIT |
| Optional: view/edit/fix the score | [MuseScore 4](https://musescore.org) | GPL-3.0 |
| Test fixtures only | [LilyPond](https://lilypond.org) | GPL-3.0 |

This project's own code is MIT.

## Install (macOS)

```bash
scripts/setup-macos.sh
```

This installs FluidSynth, FFmpeg and uv with Homebrew, puts Audiveris into `tools/`, and creates the Python environment. For the piano sound it uses the SoundFont from MuseScore 4 if MuseScore is installed. If not, put any General-MIDI `.sf2`/`.sf3` file in `soundfonts/`.

On Linux, install `fluidsynth`, `ffmpeg`, the Audiveris `.deb` from its releases page and a SoundFont (e.g. `fluid-soundfont-gm`), then run `uv sync`.

## Use

```bash
uv run sheet2audio my-piece.pdf --open
```

The output goes to `my-piece_sheet2audio/`, next to the PDF:

| File | What it is |
|---|---|
| `my-piece.html` | play-along page (audio embedded; double-click to open) |
| `my-piece.mp3`, `my-piece.wav` | the audio |
| `my-piece.mid` | MIDI (any synth, or Neothesia/PianoBooster-style falling-notes apps) |
| `my-piece.musicxml` | the recognised score, after repair |
| `omr/score.omr` | Audiveris project; open it in Audiveris to correct recognition errors |
| `report.json` | what was done, and warnings |

Useful options:

```bash
uv run sheet2audio piece.pdf --bpm 80            # set the tempo (quarter notes per minute)
uv run sheet2audio piece.pdf --tempo-scale 0.75  # 75 % of the printed tempo
uv run sheet2audio piece.pdf --sheets "1 3-4"    # only some pages
uv run sheet2audio piece.pdf --formats mp3,flac
uv run sheet2audio piece.pdf --soundfont ~/SoundFonts/SalamanderGrandPiano.sf2
uv run sheet2audio piece.pdf --musescore         # also open the result in MuseScore
uv run sheet2audio --help
```

Input can also be an image (`.png`, `.jpg`, `.tif`), MusicXML (`.mxl`, `.musicxml`, `.xml`), or an Audiveris book (`.omr`).

## When the recognition is wrong

Optical music recognition is not perfect. The tool reports what it noticed, for example a measure that came out shorter than its time signature. It pads that measure with a rest so the rhythm stays in time, and names it so you can check it. To fix mistakes:

1. **In Audiveris.** Open `omr/score.omr` in `tools/Audiveris.app`, correct the symbols, save, then run `uv run sheet2audio path/to/score.omr`. The export keeps your corrections.
2. **In MuseScore.** Open `my-piece.musicxml`, fix the notes, export MusicXML, then run `uv run sheet2audio fixed.musicxml`. MuseScore's own Play button also plays the score and highlights the notes.

Clean, engraved PDFs work best. Scans should be at least 300 dpi, straight, and evenly lit.

## How it works

```
PDF/image ──Audiveris──▶ MusicXML ──repair──▶ MusicXML ──Verovio──▶ SVG pages + MIDI + timemap
                                                                     │          │
                                                   FluidSynth+FFmpeg ◀┘          └──▶ HTML viewer
                                                        │                              ▲
                                                        └────────── MP3 ───────────────┘
```

Verovio produces the SVG, the MIDI and the timemap (note id → start and stop time) from a single layout. The viewer's highlighting therefore follows the audio exactly.

## Tests

```bash
uv run pytest -q                      # unit tests + end-to-end fixture tests
uv run python tests/evaluate.py GROUND_TRUTH.midi OUTPUT.musicxml --bar 3
```

Fixtures in `tests/fixtures/` are original pieces engraved with LilyPond. Each has a ground-truth MIDI, and `evaluate.py` scores the pipeline's note output against it.
