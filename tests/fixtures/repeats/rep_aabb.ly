\version "2.24.0"
\header { title = "Repeat Study AABB" composer = "Test fixture (original)" tagline = ##f }
\paper { #(set-paper-size "letter") }
upper = \relative c'' {
  \clef treble \key c \major \time 4/4 \tempo 4 = 120
  \repeat volta 2 {
    c4 e g e | f4 a g2 | e4 g f d | c2 g2 |
  }
  \repeat volta 2 {
    g'4 a b c | d4 c b g | a4 f e d | c1 |
  }

}
lower = \relative c {
  \clef bass \key c \major \time 4/4
  \repeat volta 2 {
    c2 <e g> | f,2 <c' e> | c2 <f a> | e2 b2 |
  }
  \repeat volta 2 {
    e2 <g c> | f2 <g b> | f2 g2 | c,1 |
  }

}
music = \new PianoStaff << \new Staff = "up" \upper \new Staff = "down" \lower >>
\score { \music \layout { } }
\score { \unfoldRepeats \music \midi { } }
