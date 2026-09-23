\version "2.24.0"
\header { title = "Running Sixteenths in G" composer = "Rhythm fixture (original)" tagline = ##f }
\paper { #(set-paper-size "letter") }
upper = \fixed c' {
  \clef treble \key g \major \time 2/4 \tempo 4 = 80
  g16 a b c' d'8 g'8 | fis'16 e' d' c' b a g fis | g8. a16 b8. c'16 | d'16 r16 d'16 r16 e'8 r8 |
  e'16 fis' g' a' b'8 g'8 | a'16 g' fis' e' d' c' b a | b8 r16 b16 c'8 r16 c'16 | d'4 r4 |
  g'32 fis' e' d' c' b a g fis16 a16 d'8 | g16 b d' g' b'8 r8 | a'16 fis' d' a d'8. c'16 | b8 r8 g4 \bar "|."
}
lower = \fixed c {
  \clef bass \key g \major \time 2/4
  g,8 d8 b,8 d8 | c4 d4 | g,4 e,4 | b,8 r8 c8 r8 |
  e4 e,4 | d4 r4 | g,8. g,16 a,8. a,16 | d8 fis8 a8 r8 |
  R2 | g,4 d4 | d4 d,4 | g,8 r8 g,4 \bar "|."
}
music = \new PianoStaff << \new Staff = "up" \upper \new Staff = "down" \lower >>
\score { \music \layout { } }
\score { \unfoldRepeats \music \midi { } }
