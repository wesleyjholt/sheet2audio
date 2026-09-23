\version "2.24.0"
\header { title = "Little Waltz in G" composer = "Test fixture (original)" tagline = ##f }
\paper { #(set-paper-size "letter") }
upper = \relative c'' {
  \clef treble \key g \major \time 3/4 \tempo 4 = 120
  d4 g, a | b8 c d4 d | e4 c8 b a g | fis2. |
  g4 b d | c4 a fis | g8 a b c d e | d2 r4 |
  e4 fis g | d2 b4 | c4 b a | g4 fis e |
  d4 fis a | cis4 d e | fis4 e8 d cis4 | d2. \bar "|."
}
lower = \relative c {
  \clef bass \key g \major \time 3/4
  g4 <b d> <b d> | g4 <b d> <b d> | c4 <e g> <e g> | d4 <fis a> <fis a> |
  g,4 <b d> <b d> | a4 <c d> <c d> | g4 <b d> <g b> | d'4 <fis a> r4 |
  c4 <e g> <e g> | b4 <d g> <d g> | a4 <c e> <c e> | e4 <g b> <g b> |
  d4 <fis a> <fis a> | a,4 <e' g> <e g> | d4 <fis a> <e g> | d2. \bar "|."
}
\score {
  \new PianoStaff << \new Staff = "up" \upper \new Staff = "down" \lower >>
  \layout { }
  \midi { }
}
