\version "2.24.0"
\header { title = "Slip Tune in C" composer = "Rhythm fixture (original)" tagline = ##f }
\paper { #(set-paper-size "letter") }
upper = \fixed c' {
  \clef treble \key c \major \time 9/8 \tempo 4. = 60
  e'4. d'8 c'8 d'8 e'4 r8 | g'4 e'8 c'4 e'8 g'4. | a'4. ~ a'4. g'8 f'8 e'8 | d'2. r4. |
  f'8 e'8 d'8 c'4 d'8 e'4 f'8 | g'4. c''4. ~ c''4. ~ | c''4 b'8 a'4 g'8 f'8 e'8 d'8 | c'4. r4. r4. \bar "|."
}
lower = \fixed c {
  \clef bass \key c \major \time 9/8
  c4. g,4. c4. | r8 e8 g8 r8 e8 g8 r8 e8 g8 | f4. c4. f4. | g,4. r4. g,4. |
  R1*9/8 | e4. e4. a4. | f4. g4. g,4. | c4. r4. r4. \bar "|."
}
music = \new PianoStaff << \new Staff = "up" \upper \new Staff = "down" \lower >>
\score { \music \layout { } }
\score { \unfoldRepeats \music \midi { } }
