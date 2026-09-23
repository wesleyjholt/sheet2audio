\version "2.24.0"
\header { title = "Pickup Repeat in C" composer = "Rhythm fixture (original)" tagline = ##f }
\paper { #(set-paper-size "letter") }
upper = \fixed c' {
  \clef treble \key c \major \time 4/4 \tempo 4 = 100
  \repeat volta 2 {
    \partial 4 g4 |
    c'4 d'4 e'4 c'4 | f'2 e'4 d'4 | c'4 e'4 d'4 b4 | c'2.
  }
  \set Timing.measurePosition = #(ly:make-moment 0)
  e'4 f'4 g'2 | a'4 g'4 f'4 e'4 | d'4 e'4 f'4 d'4 | c'1 \bar "|."
}
lower = \fixed c {
  \clef bass \key c \major \time 4/4
  \repeat volta 2 {
    \partial 4 r4 |
    c2 g,2 | f,2 g,2 | a,2 g,2 | c2.
  }
  \set Timing.measurePosition = #(ly:make-moment 0)
  c2 e2 | f2 c2 | g,2 g,2 | c1 \bar "|."
}
music = \new PianoStaff << \new Staff = "up" \upper \new Staff = "down" \lower >>
\score { \music \layout { } }
\score { \unfoldRepeats \music \midi { } }
