\version "2.24.0"
\header { title = "Scan March in C" composer = "Test fixture (original)" tagline = ##f }
\paper { #(set-paper-size "letter") }
upper = \relative c'' {
  \clef treble \key c \major \time 4/4 \tempo 4 = 100
  c4 e g e | f4 d b2 | c8 d e f g4 g | a4 g e2 |
  f4 a c a | g4 e c2 | d8 e f g a4 g | g1 |
  e4 g c b | a4 f d2 | g8 f e d c4 e | d4 b g2 |
  c4 e g c | b4 g f d | e4 d8 c b4 d | c1 \bar "|."
}
lower = \relative c {
  \clef bass \key c \major \time 4/4
  c4 <e g> c <e g> | d4 <f a> g2 | c,4 <e g> c <e g> | f4 <a c> c2 |
  f,4 <a c> f <a c> | c4 <e g> c2 | d4 <f a> f <a c> | g,2 <b d> |
  c4 <e g> e <g c> | f,4 <a c> d2 | e4 <g c> a <c e> | g,4 <b d> b2 |
  a4 <c e> e <g c> | g,4 <b d> d <f g> | c4 <e g> g, <b f'> | c1 \bar "|."
}
\score {
  \new PianoStaff << \new Staff = "up" \upper \new Staff = "down" \lower >>
  \layout { }
}
\score {
  \unfoldRepeats \new PianoStaff << \new Staff = "up" \upper \new Staff = "down" \lower >>
  \midi { }
}
