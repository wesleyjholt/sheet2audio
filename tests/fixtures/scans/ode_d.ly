\version "2.24.0"
\header { title = "Ode to Joy (theme)" composer = "L. van Beethoven (1824), arr. test fixture" tagline = ##f }
\paper { #(set-paper-size "letter") }
upper = \relative c'' {
  \clef treble \key d \major \time 4/4 \tempo 4 = 108
  fis4 fis g a | a4 g fis e | d4 d e fis | fis4. e8 e2 |
  fis4 fis g a | a4 g fis e | d4 d e fis | e4. d8 d2 |
  e4 e fis d | e4 fis8 g fis4 d | e4 fis8 g fis4 e | d4 e a,2 |
  fis'4 fis g a | a4 g fis e | d4 d e fis | e4. d8 d2 \bar "|."
}
lower = \relative c {
  \clef bass \key d \major \time 4/4
  d4 <fis a> d <fis a> | a,4 <cis e> d <fis a> | b,4 <d fis> g, <b d> | a4 <cis e> a2 |
  d4 <fis a> d <fis a> | a,4 <cis e> d <fis a> | b,4 <d fis> g, <b d> | a4 <cis g'> d2 |
  a4 <cis e> d <fis a> | a,4 <cis e> d <fis a> | a,4 <cis e> d <fis a> | fis4 g a2 |
  d4 <fis a> d <fis a> | a,4 <cis e> d <fis a> | b,4 <d fis> g, <b d> | a4 <cis g'> d2 \bar "|."
}
\score {
  \new PianoStaff << \new Staff = "up" \upper \new Staff = "down" \lower >>
  \layout { }
}
\score {
  \unfoldRepeats \new PianoStaff << \new Staff = "up" \upper \new Staff = "down" \lower >>
  \midi { }
}
