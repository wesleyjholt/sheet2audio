\version "2.24.0"
\header { title = "Volta Study in G" composer = "Test fixture (original)" tagline = ##f }
\paper { #(set-paper-size "letter") }
upper = \fixed c'' {
  \clef treble \key g \major \time 3/4 \tempo 4 = 120
  g,4 b, d | c2 a,4 |
  \repeat volta 2 {
    b,4 g, b, | a,4 fis, d, | g,4 a, b, | c4 b, a, |
  }
  \alternative {
    { b,4 a, g, | fis,2 d,4 | }
    { a,4 b, c | g,2. | }
  }
  e4 d c | g,2. \bar "|."
}
lower = \fixed c {
  \clef bass \key g \major \time 3/4
  g,4 <b, d> <b, d> | a,4 <c e> <c e> |
  \repeat volta 2 {
    g,4 <b, d> <b, d> | d4 <fis a> <fis a> | e4 <g b> <g b> | a,4 <c e> <c e> |
  }
  \alternative {
    { d4 <g b> <g b> | d2 r4 | }
    { d4 <fis c'> <fis c'> | g,2. | }
  }
  c4 <e g> <e g> | g,2. \bar "|."
}
music = \new PianoStaff << \new Staff = "up" \upper \new Staff = "down" \lower >>
\score { \music \layout { } }
\score { \unfoldRepeats \music \midi { } }
