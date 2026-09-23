\version "2.24.0"
\header { title = "Jig-like Tune in D" composer = "Rhythm fixture (original)" tagline = ##f }
\paper { #(set-paper-size "letter") }
upper = \fixed c' {
  \clef treble \key d \major \time 6/8 \tempo 4. = 60
  \partial 8 a8 |
  d'4 fis'8 a'4 fis'8 | e'4. ~ e'4 d'8 | cis'8. d'16 e'8 fis'4 e'8 | d'4. ~ d'4 a8 |
  b4 cis'8 d'4 e'8 | fis'4. ~ fis'8 r8 fis'8 | g'4 fis'8 e'4 d'8 | cis'4. r4 a8 |
  d'4 e'8 fis'4 g'8 | a'4. ~ a'4 fis'8 ~ | fis'8 e'8 d'8 cis'4 e'8 | fis'4 e'8 d'4 \bar "|."
}
lower = \fixed c {
  \clef bass \key d \major \time 6/8
  \partial 8 r8 |
  d4. a4. | a,4. cis4. | a,4 r8 a,4 r8 | d4. ~ d4 r8 |
  g,4. r4. | R2. | e4. g4. | a,4. r4. |
  d4. d4. | fis4. ~ fis4. | g4. a4. | d4 r8 d4 \bar "|."
}
music = \new PianoStaff << \new Staff = "up" \upper \new Staff = "down" \lower >>
\score { \music \layout { } }
\score { \unfoldRepeats \music \midi { } }
