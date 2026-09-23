\version "2.24.0"
\header { title = "Pickup and Repeats in G" composer = "Test fixture (original)" tagline = ##f }
\paper { #(set-paper-size "letter") }
upper = \fixed c' {
  \clef treble \key g \major \time 4/4 \tempo 4 = 120
  \partial 4 d4 |
  \repeat volta 2 { g4 a b g | c'4 b a g | fis4 a d' c' | }
  \alternative { { b4 a g d | } { b4 a g } }
  \repeat volta 2 { d'4 | e'4 d' c' b | a4 b c' a | g4 fis e fis | g2. }
}
lower = \fixed c {
  \clef bass \key g \major \time 4/4
  \partial 4 r4 |
  \repeat volta 2 { g,2 <b, d> | e,2 <g, c> | d,2 <fis, c> | }
  \alternative { { g,2 <b, d>4 r4 | } { g,2 <b, d>4 } }
  \repeat volta 2 { r4 | c2 <e g> | d2 <fis c'> | c2 d | g,2. }
}
music = \new PianoStaff << \new Staff = "up" \upper \new Staff = "down" \lower >>
\score { \music \layout { } }
\score { \unfoldRepeats \music \midi { } }
