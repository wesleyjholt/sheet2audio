\version "2.24.0"
\header { title = "Tuplet Study in C" composer = "Rhythm fixture (original)" tagline = ##f }
\paper { #(set-paper-size "letter") }
upper = \fixed c' {
  \clef treble \key c \major \time 4/4 \tempo 4 = 90
  \tuplet 3/2 { c'8 d' e' } \tuplet 3/2 { f' g' a' } g'4 e'4 |
  \tuplet 3/2 { f'4 e' d' } c'2 |
  e'8 f' g'4 \tuplet 3/2 { a'8 g' f' } e'4 |
  \tuplet 6/4 { d'16 e' f' g' a' b' } c''4 \tuplet 3/2 { b'8 a' g' } g'4 |
  \tuplet 5/4 { a'16 g' f' e' d' } c'4 d'4 e'4 |
  \tuplet 3/2 { f'8 r8 f'8 } \tuplet 3/2 { g'8 r8 g'8 } a'2 |
  \tuplet 3/2 { g'2 f'2 e'2 } |
  c'1 \bar "|."
}
lower = \fixed c {
  \clef bass \key c \major \time 4/4
  c2 g,2 |
  \tuplet 3/2 { f,4 a, c } c2 |
  c4 e4 f4 c4 |
  g,2 \tuplet 3/2 { g,8 b, d } g,4 |
  f,2 g,4 c4 |
  f,4 r4 e,4 r4 |
  \tuplet 3/2 { g,4 r4 g,4 } g,2 |
  c1 \bar "|."
}
music = \new PianoStaff << \new Staff = "up" \upper \new Staff = "down" \lower >>
\score { \music \layout { } }
\score { \unfoldRepeats \music \midi { } }
