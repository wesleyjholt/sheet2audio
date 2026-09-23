\version "2.24.0"
\header { title = "Da Capo Study in F" composer = "Test fixture (original)" tagline = ##f }
\paper { #(set-paper-size "letter") }
upper = \fixed c'' {
  \clef treble \key f \major \time 4/4 \tempo 4 = 120
  \repeat segno 2 {
    f,4 a, c a, | bes,4 d c2 | a,4 c bes, g, | f,1 |
    \volta 2 \fine
    \volta 1 {
      c4 d e f | g4 e c2 | d4 bes, g, e, | c,2 r2 |
    }
  }
}
lower = \fixed c {
  \clef bass \key f \major \time 4/4
  \repeat segno 2 {
    f,2 <a, c> | bes,2 <a, c> | f,2 c | f,1 |
    \volta 2 \fine
    \volta 1 {
      c2 <e g> | c2 <e g> | bes,2 <c e> | c,2 r2 |
    }
  }
}
music = \new PianoStaff << \new Staff = "up" \upper \new Staff = "down" \lower >>
\score { \music \layout { } }
\score { \unfoldRepeats \music \midi { } }
