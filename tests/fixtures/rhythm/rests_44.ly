\version "2.24.0"
\header { title = "Rest Study in C" composer = "Rhythm fixture (original)" tagline = ##f }
\paper { #(set-paper-size "letter") }
upper = \fixed c' {
  \clef treble \key c \major \time 4/4 \tempo 4 = 100
  c'4 e'4 g'2 | R1 | r2 a'4 g'4 | f'4 r4 e'4 r4 |
  r8 d'8 e'8 f'8 g'4. r8 | e'16 r16 e'8 r8. f'16 g'4 r4 | R1 | r4. c'8 d'4 e'4 |
  f'2. r4 | g'4 f'4 e'4 d'4 | a'4 r16 a'8. g'4 f'4 | e'1 \bar "|."
}
lower = \fixed c {
  \clef bass \key c \major \time 4/4
  c2 g,2 | f,4 a,4 c4 a,4 | R1 | g,4 r4 c4 r4 |
  r4 g,4 c2 | c8 r8 c8 r8 r2 | R1 | a,1 |
  d4 r8 d8 r2 | r2. g,4 | f4 r4 g4 g,4 | c1 \bar "|."
}
music = \new PianoStaff << \new Staff = "up" \upper \new Staff = "down" \lower >>
\score { \music \layout { } }
\score { \unfoldRepeats \music \midi { } }
