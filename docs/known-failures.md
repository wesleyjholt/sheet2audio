# Known recognition failures

This is what still goes wrong on the choral fixtures in `tests/fixtures/engravers/`. Those fixtures are engraved by Verovio (5 music fonts) and MuseScore, not LilyPond, so Audiveris reads them about as well as it reads a real score.

The numbers come from diagnosis runs on 2026-09-24. Each one was measured by re-running the pipeline on a fixture with the fix simulated or prototyped. Classes are ranked best value first, where value is gain × generality ÷ (effort × risk).

The note ledger (`src/sheet2audio/ledger.py`) already guarantees two things:
- None of our own stages loses a note.
- Every note that Audiveris recognises but does not export is named for the user.

The classes below are recognition and structure errors that the ledger cannot see. That is why they need fixing or at least a warning.

| # | Class | Stage | Cases | Gain if fixed | Effort / risk | Fix | Detect if not fixed |
|---|---|---|---|---|---|---|---|
| 5 | Time-signature digits read as notes or rests where the meter changes | Recognition | harbor_song ×2, evening_lanterns_mscore | Up to 0.55 → 0.98 onset | Large / low–moderate | When injecting a meter (`omr.repair_meters`), also try deleting doubtful chords inside the time-signature box; keep it only if Audiveris re-times cleanly | Warn when the bar is still abnormal after the injection |
| 6 | Treble clef misread as octave-down treble (G_CLEF_8VB) | Recognition | harbor_song_vrv_petaluma | Pitch 0.82 → 0.97 | Small / low | A low-grade octave clef that differs from that staff's clef on its other lines is replaced | Warn about an octave jump in clef between lines |
| 6b | Small mid-line clef change not recognised at all (the staff is read in the old clef) | Recognition | Little Baby in a Manger m14–16 (piano left hand a 6th low) | 10 chords | Medium / low | A `--clef MEASURE:PART:STAFF:CLEF` override (like `--key`) that re-reads the staff positions | Hard to detect in general |
| 7 | Whole-note chords missed | Recognition | 5 cases | About +0.05 | Small / moderate | `NoteHeadsBuilder.stemLessBoost=0.25`, for born-digital PDFs only | Reported as empty staves (the staff-gap check) |
| 8 | Tempo mark recognised but lost at export | Audiveris export | evening_lanterns ×2 | Audio 25 % too fast | Small / low | Read the metronome words from the book | "A metronome mark was seen but not read" |
| 9 | Ties across a line break read as slurs, or tied only partly | Recognition | 4 cases, Little Baby m3 | About +0.02 | Small / low–medium | Turn a slur between identical chords into ties. At line breaks only, complete a partly tied chord. | Warn about a tie start with no stop |
| 10 | Ties at a page turn or line break discarded by Audiveris | Recognition | 4 cases, the Miracle, Little Baby m16, m28 | About +0.01 per case | No safe fix from the book | None | Same chord both sides of a break on 2+ staves, untied: warn |
| 11 | Short last line not found as a line of music | Recognition | evening_lanterns_mscore | Pitch 0.95 → 0.97 | Medium / low–moderate | When the log shows discarded short staves, re-run that page with relaxed `LinesRetriever.minStaffLength` / `ClustersRetriever.minClusterLengthRatio` | "Page N: short staves set aside" |
| 12b | An inner-voice note nudged right (to clear a dot) is read an eighth late, overlapping the next note | Audiveris rhythm | Little Baby m20, m21, m51 | 3 notes | Medium / moderate | Extend the position re-timing to voices that overlap themselves | — |
| 13 | One notehead shared by two voices is played twice | Audiveris export | harbor_song_vrv_petaluma | +0.0025 | Small / low | Play it once in the full mix | — |

Fixed on 2026-09-25:
- #1: ending brackets are merged across parts and renumbered. An open bracket needs corroboration, a missing 2nd ending is assumed only after a trusted 1st ending, and a bracket read in pieces is joined.
- #2 and #3: whole-bar rests of hidden staves no longer block padding. A first bar that most staves agree is short is a pickup. An overfull bar in a minority of staves is cut back at the bar line, with every removed note counted in the note ledger and named for the user.
- #4: parts split by printed name are joined. Printed names without voice words ('Part II') are kept, and a bare numeral becomes 'Part I'.
- #12, for rests and dots: a staff that ends early while others fill the bar gets the missing time back, where its notes (by their x position) line up with the other staves again.

Fixed on 2026-09-24:
- Missed mid-line time signature, and the chords Audiveris then dropped (`omr.repair_meters`).
- Key change by natural signs (`omr.book_keys`).
- A lone short bar in 3+ staves treated as a meter change.
- Repeats shared across parts.
- Ending brackets with no repeat dropped (#1 generalises this).
- A 10th part played on the drum channel.
- False timing warning for a final tie.
- Note ledger.
