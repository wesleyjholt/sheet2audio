# Known recognition failures

This is what still goes wrong on the choral fixtures in `tests/fixtures/engravers/`. Those fixtures are engraved by Verovio (5 music fonts) and MuseScore, not LilyPond, so Audiveris reads them about as well as it reads a real score.

The numbers come from diagnosis runs on 2026-09-24. Each one was measured by re-running the pipeline on a fixture with the fix simulated or prototyped. Classes are ranked best value first, where value is gain × generality ÷ (effort × risk).

The note ledger (`src/sheet2audio/ledger.py`) already guarantees two things:
- None of our own stages loses a note.
- Every note that Audiveris recognises but does not export is named for the user.

The classes below are recognition and structure errors that the ledger cannot see. That is why they need fixing or at least a warning.

| # | Class | Stage | Cases | Gain if fixed | Effort / risk | Fix | Detect if not fixed |
|---|---|---|---|---|---|---|---|
| 1 | Ending brackets lost, extra or misnumbered (lyric extender read as a bracket, "2" read as "1", brackets in some parts only) | Audiveris export, our clean-up | morning_bell ×3 | Onset 0.52–0.72 → 0.997 | Small / low | Collect bracket spans from all parts. Keep the best-supported spans. Number each bracket after a repeat as the next ending. Write the brackets into every part. | Warn about a 1st ending with no 2nd, and about brackets that are not in part 1 |
| 2 | One staff has a missed rest, and a hidden staff's whole-bar rest blocks the padding | Our clean-up | evening_lanterns_vrv_bravura | Onset 0.23 → 0.96 | Small / moderate | Pad voices that have notes, and ignore whole-bar rests. Mark a short first bar as a pickup instead of padding it. | Add to the note: "every later bar plays early" |
| 3 | One staff is overfull (a misread dot or flag), which shifts every later bar | Our clean-up (the misread is recognition) | evening_lanterns_mscore, spring_procession_mscore | Onset 0.42 → 0.95, 0.09 → 0.96 | Medium / moderate | If most staves fill the bar exactly, trim the overrunning voice | Add to the note: "every later bar sounds late" |
| 4 | Parts split by printed name (full name, abbreviation, OCR variants), giving 5–12 parts | Audiveris export | 6 cases | About +0.015; also needed before 9 | Medium / low | Merge parts that are never on the same system and share name tokens, clef and rank | Warn when there are more parts than staves on any system |
| 5 | Time-signature digits read as notes or rests where the meter changes | Recognition | harbor_song ×2, evening_lanterns_mscore | Up to 0.55 → 0.98 onset | Large / low–moderate | When injecting a meter (`omr.repair_meters`), also try deleting doubtful chords inside the time-signature box, and keep that only if Audiveris re-times cleanly | Warn when the bar is still abnormal after the injection |
| 6 | Treble clef misread as octave-down treble (G_CLEF_8VB) | Recognition | harbor_song_vrv_petaluma | Pitch 0.82 → 0.97 | Small / low | A low-grade octave clef that differs from that staff's clef on its other lines is replaced | Warn about an octave jump in clef between lines |
| 7 | Whole-note chords missed | Recognition | 5 cases | About +0.05 | Small / moderate | `NoteHeadsBuilder.stemLessBoost=0.25` for born-digital PDFs only; it adds false heads on photos | Report empty staves (the staff-gap check) |
| 8 | Tempo mark recognised but lost at export | Audiveris export | evening_lanterns ×2 | Audio 25 % too fast | Small / low | Read the metronome words from the book | "A metronome mark was seen but not read" |
| 9 | Ties across a line break read as slurs, or tied only partly | Recognition | 4 cases | About +0.02 | Small / low–medium | Turn a slur between identical chords into ties. At line breaks only, complete a partly tied chord. | Warn about a tie start with no stop |
| 10 | Ties at a page turn discarded by Audiveris | Recognition | 4 cases, and the Miracle | About +0.01 per case | No safe fix from the book | None | Same chord both sides of a page turn on 2+ staves, untied: warn |
| 11 | Short last line not found as a line of music | Recognition | evening_lanterns_mscore | Pitch 0.95 → 0.97 | Medium / low–moderate | When the log shows discarded short staves, re-run that page with relaxed `LinesRetriever.minStaffLength` / `ClustersRetriever.minClusterLengthRatio` | "Page N: short staves set aside" |
| 12 | Missed dot or flag in one staff | Recognition | 3 cases | About +0.05 | Large / moderate | Re-time from note positions lined up with 2+ other staves | Point at the misaligned beats |
| 13 | One notehead shared by two voices is played twice | Audiveris export | harbor_song_vrv_petaluma | +0.0025 | Small / low | Play it once in the full mix | — |

Fixed on 2026-09-24:
- Missed mid-line time signature, and the chords Audiveris then dropped (`omr.repair_meters`).
- Key change by natural signs (`omr.book_keys`).
- A lone short bar in 3+ staves treated as a meter change.
- Repeats shared across parts.
- Ending brackets with no repeat dropped (#1 generalises this).
- A 10th part played on the drum channel.
- False timing warning for a final tie.
- Note ledger.
