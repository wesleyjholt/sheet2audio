\version "2.24.0"
\header { title = "Trailing Rests in C" composer = "Rhythm fixture (original)" tagline = ##f }
\paper { #(set-paper-size "letter") }
upper = \fixed c' {
  \clef treble \key c \major \time 4/4 \tempo 4 = 100
  c'4 e'4 g'4 r4 | b'2 r2 | a'2 r2 | g'4 f'4 e'4 d'4 |
  c'2. r4 | e'4 g'4 c''4 r4 | d''2 b'4 r4 | c''1 \bar "|."
}
lower = \fixed c {
  \clef bass \key c \major \time 4/4
  c2 e4 r4 | g,2 r2 | f,2 r2 | g,1 |
  c2. r4 | c4 e4 a4 r4 | g,2 g,4 r4 | c1 \bar "|."
}
music = \new PianoStaff << \new Staff = "up" \upper \new Staff = "down" \lower >>
\score { \music \layout { } }
\score { \unfoldRepeats \music \midi { } }
