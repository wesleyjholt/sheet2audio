# sheet2audio

Turn a PDF (or a scan or photo) of piano sheet music into:

- **audio**: MP3 and WAV by default; FLAC, M4A and OGG on request.
- **a score video (MP4)**: the engraved score, one screen at a time, with each note lit up as it sounds. It plays on an **iPhone or iPad in the built-in Photos or Files app**, so no extra app is needed. It also plays on TVs and in any video player.
- **a play-along page (HTML)**: a single file that shows the score and highlights every note as it sounds. You can click a note to jump there. It has speed control and auto-scroll, and re-flows the music for phone screens.
- **MIDI and MusicXML**: open these in MuseScore to play the score with highlighting, or to correct mistakes.

Every component is free and open-source software:

| Step | Tool | License |
|---|---|---|
| Optical music recognition (PDF/image → MusicXML) | [Audiveris](https://github.com/Audiveris/audiveris) 5.11 | AGPL-3.0 |
| Engraving, MIDI, note timing map | [Verovio](https://www.verovio.org) 6.3 (Python) | LGPL-3.0 |
| MIDI → audio | [FluidSynth](https://www.fluidsynth.org) 2.x | LGPL-2.1 |
| Piano sound | "MS Basic" SoundFont bundled with MuseScore 4 (from FluidR3) | MIT |
| Audio/video encoding | [FFmpeg](https://ffmpeg.org) | LGPL/GPL |
| Video frames | [librsvg](https://wiki.gnome.org/Projects/LibRsvg) | LGPL-2.1+ |
| Image and PDF preparation | [Pillow](https://python-pillow.org), [pypdf](https://github.com/py-pdf/pypdf) | HPND, BSD |
| MIDI files, music font | [mido](https://github.com/mido/mido), [fontTools](https://github.com/fonttools/fonttools) | MIT |
| Optional: view/edit/fix the score | [MuseScore 4](https://musescore.org) | GPL-3.0 |
| Test fixtures only | [LilyPond](https://lilypond.org) | GPL-3.0 |

This project's own code is MIT.

## Install (macOS)

```bash
scripts/setup-macos.sh
```

The script does four things:

- installs FluidSynth, FFmpeg, librsvg and uv with Homebrew;
- downloads Audiveris from its GitHub releases into `tools/`;
- accepts Audiveris' AGPL-3.0 license, which its disk image shows before mounting;
- creates the Python environment.

The piano sound comes from MuseScore 4's SoundFont if MuseScore is installed. If it isn't, put any General-MIDI `.sf2`/`.sf3` file in `soundfonts/`.

On Linux, install `fluidsynth`, `ffmpeg`, `librsvg2-bin`, the Audiveris `.deb` from its releases page and a SoundFont (e.g. `fluid-soundfont-gm`). Then run `uv sync`.

## Use

```bash
uv run sheet2audio my-piece.pdf --open
```

The output goes to `my-piece_sheet2audio/`, next to the PDF:

| File | What it is |
|---|---|
| `my-piece.mp4` | score video with sound, for phones, tablets, TVs |
| `my-piece.html` | play-along page (audio embedded; double-click to open) |
| `my-piece.mp3`, `my-piece.wav` | the audio |
| `my-piece.mid` | MIDI |
| `my-piece.musicxml` | the recognised score, after clean-up and repair |
| `omr/score.omr` | Audiveris project; open it in Audiveris to correct recognition errors |
| `report.json` | what was done, and everything worth checking |

### On an iPhone or iPad (free, nothing to install)

- **Video.** AirDrop `my-piece.mp4` to the phone, or save it to iCloud Drive. It opens in Photos or Files and plays with the notes lighting up. Turn the phone sideways for a bigger score.
  - Format: 1080p H.264 with AAC sound, about 6 MB per 3 minutes of music.
  - Speed: drawing it adds roughly 10 s per minute of music.
- **Audio only.** `my-piece.mp3` or `my-piece.m4a` (`--formats mp3,m4a`) plays in the Files app.
- **Interactive page.** You can tap notes and change the speed. iOS won't run the page's script from the Files app, so serve it from the Mac instead:

  ```bash
  uv run sheet2audio my-piece.pdf --serve
  ```

  This prints a link such as `http://192.168.1.20:8000/my-piece.html`. Open it in Safari on a phone on the same Wi-Fi. Press Ctrl-C to stop sharing. While it runs, anyone on the network can open the files in that output folder.

#### If the phone can't open the link

- **Firewall.** `--serve` checks itself and warns when the Mac's firewall turns other devices away. The usual cause is that macOS asked "Do you want Python to accept incoming network connections?" and the answer was Deny. You can fix it in either of these ways:
  - In System Settings > Network > Firewall > Options…, set "Python" to "Allow incoming connections".
  - Share the output folder with Apple's own Python instead, which the firewall allows:

    ```bash
    /usr/bin/python3 -m http.server 8000 --directory my-piece_sheet2audio
    ```

- **Guest, school or work Wi-Fi.** These networks often block devices from reaching each other. Turn on the iPhone's Personal Hotspot, join it from the Mac, and run `--serve` again; the link it prints then works on the phone.
- **Same network?** The phone must be on the same Wi-Fi as the Mac, and the Mac must stay awake while you use the page.

### Options

```bash
uv run sheet2audio piece.pdf --bpm 80            # starting tempo (quarter notes/minute)
uv run sheet2audio piece.pdf --tempo-scale 0.75  # 75 % of the printed tempo
uv run sheet2audio piece.pdf --time 3/4          # time signature, if none was recognised
uv run sheet2audio piece.pdf --key 29:C          # a key change OMR missed (29:C,41:Eb; Am for minor)
uv run sheet2audio piece.pdf --clef 14:Piano:2:treble   # a clef change OMR missed (part, staff from the top)
uv run sheet2audio piece.pdf --sheets "1 3-4"    # only some pages
uv run sheet2audio piece.pdf --formats mp3,m4a,flac
uv run sheet2audio piece.pdf --no-video          # faster; skip the MP4
uv run sheet2audio piece.pdf --soundfont ~/SoundFonts/SalamanderGrandPiano.sf2
uv run sheet2audio piece.pdf --musescore         # also open the result in MuseScore
uv run sheet2audio --help
```

Input can also be an image (`.png`, `.jpg`, `.tif`, `.bmp`, `.gif`, `.webp`, including multi-page TIFF), MusicXML (`.mxl`, `.musicxml`, `.xml`), or an Audiveris book (`.omr`).

## How accurate is it?

The pipeline was checked against 140 test files, each with known correct notes.

- **Test files:** original pieces engraved with LilyPond, plus scanned and degraded copies of them. There are also 15 choral scores (two voice staves with lyrics, plus piano) engraved by Verovio in five music fonts and by MuseScore. Audiveris reads these about as well as it reads real Finale, Sibelius and Dorico output, which is worse than it reads LilyPond.
- **Musical features covered:** repeats, first and second endings, D.C. al Fine, pickups, ties, tuplets, 6/8 and 9/8, two voices per hand, clef changes, several pieces in one PDF, and multi-page scores.
- **Scan conditions covered:** 75–600 dpi, rotation, noise, JPEG artefacts, uneven light, phone-photo simulations, blank pages, and CMYK, 16-bit and WebP images.

The table below gives the share of notes with the right pitch, in the right order. Timing accuracy is close to it except where noted.

| Input (number of test files) | Median | Range |
|---|---|---|
| Clean engraved PDFs (38) | 100 % | 96–100 %, except: real 8va passage 72 % (flagged); D.C./D.S. text not recognised 80–84 %; many grace notes and arpeggios 85 % |
| Scans 150–600 dpi, incl. rotation ≤ 1.5°, JPEG q ≥ 20, blur, uneven light, CMYK/16-bit/WebP (54) | 99 % | 69–100 % |
| Scans 100–120 dpi (6) | 97 % | 94–99 % |
| Mild phone-photo simulation (6) | 97 % | 94–98 % |
| Harsh phone-photo simulation: noise, blur, skew and shadows together (12) | 41 % | 19–57 %: use a scanner app instead |
| 75 dpi (2) | — | 33 % or no result |
| Choral scores engraved by Verovio (5 fonts) and MuseScore (15) | 99 % | 82–100 %. Timing median 98 %; one at 56 % (time-signature digits read as notes). See `docs/known-failures.md` |

Timing is sample-accurate between the MIDI, the audio, the video and the page highlighting. The audio lags the MIDI by 2–3 ms, and the MP3 by 0 samples.

What the tool does about common recognition errors:

- **Missed time signatures.** Audiveris can miss a time signature printed in the middle of a line (in one Finale-engraved score it missed all three). When the bars get longer (e.g. 2/4 → 4/4), its rhythm step then silently drops every chord that does not fit, on all the bars that follow. The tool finds the dropped chords in the Audiveris book, puts the time signature back, and lets Audiveris re-time those pages. It keeps the change only if chords come back. Any bar that is still missing chords is named for you to check.
- **Too-short bars.** If a bar plays shorter than its time signature (usually a missed rest), a rest is added and the bar is named for you to check. If three or more staves all stop at the same point, the bar is treated as a missed time signature instead (e.g. one 2/4 bar in 4/4). Pickups, bars split by a repeat, and short bars that complete a pickup at a repeat are left alone.
- **Repeats.** Repeats drawn as ":\|\|:" and "A :\| B :\|" are made to play in the order a pianist would.
- **Recognition debris.** Stray 8va marks and empty bars from courtesy signatures are removed, and you are told where.
- **Dropped key changes.** Audiveris recognises a key change made of natural signs (e.g. B-flat major → C major) but leaves it out of its MusicXML. The key is read back from the Audiveris book, and the notes after it are re-spelled. For other misses, use `--key`.
- **Parts split by their printed name.** Audiveris makes a new part whenever a part's name is printed differently (e.g. 'Part II' on the first line, 'II' after it). Such parts are joined again.
- **1st/2nd-ending brackets.** Brackets are made the same in every part, misread brackets (often lyric extenders) are removed, a misread number is corrected, and a 2nd ending that was not read is assumed after a 1st ending.
- **Rests or dots OMR missed inside a bar.** When one staff ends early while the others fill the bar, the missing time is put back where its notes line up again with the other staves on the page. When a staff runs past the bar line while the others fit, it is cut back to the bar line. Every note removed this way is named.
- **Tempo written as text.** Something like "= 132" is used as the tempo.
- **Several pieces on one page** are split, each with its own tempo.

Known limitations:

- D.S./Coda jumps aren't played; D.C. al Fine is, when the words were recognised.
- Real 8va passages play as printed, an octave off, and are flagged.
- Fermatas aren't held.
- A rest missed in the middle of a bar is added at the end of the bar instead.

## When the recognition is wrong

The tool lists everything worth checking at the end of each run, and in the HTML page. To fix mistakes:

1. **In Audiveris.** Open `omr/score.omr` in `tools/Audiveris.app`, correct the symbols, save, then run `uv run sheet2audio my-piece_sheet2audio/omr/score.omr`. The export keeps your corrections, and the results replace the old ones.
2. **In MuseScore.** Open `my-piece.musicxml`, fix the notes, export MusicXML, then run `uv run sheet2audio fixed.musicxml`. MuseScore's own Play button also plays the score and highlights notes.

Clean, engraved PDFs work best. Scans should be 300 dpi, straight, and evenly lit.

## How it works

```
PDF/image ─▶ Pillow/pypdf ─▶ Audiveris ─▶ MusicXML ─▶ clean-up + repair ─▶ Verovio
                                                                             │
                ┌──────────────────────────┬─────────────────────────────────┤
                ▼                          ▼                                 ▼
        MIDI ─▶ FluidSynth ─▶ audio   SVG pages + timemap ─▶ HTML    16:9 screens ─▶ librsvg
                                │                                                 │
                                └──────────────▶ FFmpeg ◀─────────────────────────┘ ─▶ MP4
```

Verovio makes the MIDI, the timemap (note id → start and stop time) and all three layouts (page, phone and video) from one document with the same note ids. The page highlighting and the video therefore stay in step with the audio.

## No silent note loss

Every run counts the notes at each stage: noteheads Audiveris recognised, notes in its MusicXML, notes after clean-up, notes drawn, and notes played. The counts are in `report.json` under `ledger`.

- **Audiveris dropped notes it recognised.** Named bar by bar in the notes.
- **A later stage lost notes.** This means a bug in this tool, and you are told so. The corpus run fails on any such loss.

The ledger cannot see a notehead that Audiveris never recognised. For that, the tool checks for staves with nothing written in part of a bar. `tests/audit_materials.py` puts each printed line next to its rendering for a full check by eye.

## Tests

```bash
uv run pytest -q                           # fast unit tests
uv run python tests/corpus.py --jobs 4     # all fixtures through the CLI, scored; exits 1 if the note ledger breaks
uv run python tests/evaluate.py GROUND_TRUTH.midi OUTPUT.musicxml --bar 3
uv run python tests/audit_materials.py OUTDIR PIECE.pdf DEST   # printed line vs our rendering, per line
```
