\version "2.24.0"
\header { title = "Repeats Across Systems" composer = "Test fixture (original)" tagline = ##f }
\paper { #(set-paper-size "letter") }
% 20 written bars, 4 systems: forward repeat mid-system, a volta pair split
% across a system break, a :||: barline and a final repeat. Plays 32 bars.
upper = \fixed c'' {
  \clef treble \key c \major \time 4/4 \tempo 4 = 120
  c4 e g e | d4 f g2 |
  \repeat volta 2 {
    e4 d c e | f4 e d f | g4 f e d | c4 d e2 | \break
    a,4 b, c d | e4 f g2 |
  }
  \alternative {
    { f4 e d c | b,2 g,2 | \break }
    { f4 d b, g, | c1 | }
  }
  e4 g c' g | a4 f d2 |
  \repeat volta 2 { g4 a g f | e4 f e d | \break c4 e d b, | c2 g,2 | }
  \repeat volta 2 { e4 d c b, | c1 | }
}
lower = \fixed c {
  \clef bass \key c \major \time 4/4
  c2 <e g> | b,2 <d g> |
  \repeat volta 2 {
    c2 <e g> | d2 <f a> | e2 <g b> | a,2 <c e> |
    f,2 <a, c> | c2 <e g> |
  }
  \alternative {
    { g,2 <b, d> | g,2 r2 | }
    { g,2 <b, f> | c1 | }
  }
  c2 <e g> | f,2 <a, c> |
  \repeat volta 2 { e2 <g b> | c2 <e g> | f2 g2 | e2 g,2 | }
  \repeat volta 2 { g,2 <b, d> | c1 | }
}
music = \new PianoStaff << \new Staff = "up" \upper \new Staff = "down" \lower >>
\score { \music \layout { } }
\score { \unfoldRepeats \music \midi { } }
