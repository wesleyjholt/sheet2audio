\version "2.24.0"
% Original test piece: arpeggiated (rolled) chords in both hands, acciaccaturas,
% appoggiaturas and multi-note grace groups.
\header { title = "Rolled Chords and Graces" composer = "Test fixture (original)" tagline = ##f }
\paper { #(set-paper-size "letter") }

upper = \fixed c'' {
  <c e g c'>2\arpeggio e4 g |
  \acciaccatura b,8 c4 \acciaccatura fis8 g4 e2 |
  <f a c'>2\arpeggio \appoggiatura b8 a4 f |
  \grace { d16 e } d2 c2 |
  <e g c'>2\arpeggio \acciaccatura dis8 e4 g |
  \appoggiatura d'8 c'4 b a g |
  <d f a b>2\arpeggio \grace { g16 a b } c'4 b |
  <c e g c'>1\arpeggio |
}
lower = \fixed c {
  <c, g, e>2\arpeggio c4 e |
  g,2 c2 |
  <f, c f>2\arpeggio f,2 |
  \acciaccatura fis,8 g,2 g,2 |
  <c g e'>2\arpeggio c2 |
  a,2 e,2 |
  <g, d f>2\arpeggio g,2 |
  <c, c>1\arpeggio |
}

\score {
  \new PianoStaff <<
    \new Staff = "up" { \clef treble \key c \major \time 4/4 \tempo 4 = 90 \upper \bar "|." }
    \new Staff = "down" { \clef bass \key c \major \time 4/4 \lower \bar "|." }
  >>
  \layout { }
  \midi { }
}
