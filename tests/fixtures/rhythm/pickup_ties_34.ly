\version "2.24.0"
\header { title = "Pickup and Ties in F" composer = "Rhythm fixture (original)" tagline = ##f }
\paper { #(set-paper-size "letter") }
upper = \fixed c' {
  \clef treble \key f \major \time 3/4 \tempo 4 = 100
  \partial 4 c4 |
  f4. g8 a4 | c'2 a4 ~ | a4 g4 f4 | e2. ~ |
  e2 c4 | d4. e8 f4 | g4. a8 bes4 | a2 r4 |
  a4. bes8 c'4 ~ | c'4 bes4 a4 | g8. a16 bes4 g4 | f2. ~ |
  f4 a4 c'4 | bes4. a8 g4 | a4 g4 e4 | f2 \bar "|."
}
lower = \fixed c {
  \clef bass \key f \major \time 3/4
  \partial 4 r4 |
  f2. ~ | f4 a4 c'4 | bes,2. | c2 bes,4 |
  a,2. | bes,4. c8 d4 | e2 c4 | f4 c4 f,4 |
  f2. | c2 e4 | c2 c4 | f2 c4 |
  f,2. | g,2 bes,4 | c2 c4 | f,2 \bar "|."
}
music = \new PianoStaff << \new Staff = "up" \upper \new Staff = "down" \lower >>
\score { \music \layout { } }
\score { \unfoldRepeats \music \midi { } }
