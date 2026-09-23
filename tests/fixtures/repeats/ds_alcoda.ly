\version "2.24.0"
\header { title = "Dal Segno Study in D" composer = "Test fixture (original)" tagline = ##f }
\paper { #(set-paper-size "letter") }
upper = \fixed c'' {
  \clef treble \key d \major \time 4/4 \tempo 4 = 120
  d,4 fis, a, d | cis2 a,2 |
  \repeat segno 2 {
    b,4 d fis d | e4 cis a,2 | fis,4 a, g, e, | d,1 |
    \alternative {
      \volta 1 { a,4 b, cis d | e2 a,2 | }
      \volta 2 \volta #'() { \section \sectionLabel "Coda" }
    }
  }
  fis4 e d cis | d1 \bar "|."
}
lower = \fixed c {
  \clef bass \key d \major \time 4/4
  d2 <fis a> | a,2 <e g> |
  \repeat segno 2 {
    b,2 <d fis> | a,2 <cis e> | d2 a, | d1 |
    \alternative {
      \volta 1 { a,2 <cis e> | a,2 <cis e g> | }
      \volta 2 \volta #'() { \section }
    }
  }
  a,2 <cis e g> | d1 \bar "|."
}
music = \new PianoStaff << \new Staff = "up" \upper \new Staff = "down" \lower >>
\score { \music \layout { } }
\score { \unfoldRepeats \music \midi { } }
