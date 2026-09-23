\version "2.24.0"
% Original test piece: notes with many ledger lines above/below both staves,
% an 8va passage in the right hand (bars 5-8) and an 8vb passage in the left
% hand (bars 9-10). Pitches are written as they SOUND; LilyPond prints the
% ottava brackets and shifts the display.
\header { title = "Ledger Lines and Ottava" composer = "Test fixture (original)" tagline = ##f }
\paper { #(set-paper-size "letter") }

upper = \fixed c'' {
  c4 e g c' | e'2 g' | a'4 g' e' c' | g1 |
  \ottava #1
  c'4 e' g' c'' | b'4 g' e' c' | d'4 f' a' c'' | c''1 |
  \ottava #0
  e4 g e c | d4 f d b, |
  c,4 a,, f,, a,, | c,2 e, | g,,4 c, e, g, | c1 |
}
lower = \fixed c {
  c,2 g, | c,2 e, | f,,2 a,, | g,,1 |
  c2 e | g2 c | f2 a, | c1 |
  \ottava #-1
  c,,4 e,, g,, c, | g,,4 b,, d, g,, |
  \ottava #0
  c'4 e' g' e' | c'2 g | e'4 c' g e | c1 |
}

\score {
  \new PianoStaff <<
    \new Staff = "up" { \clef treble \key c \major \time 4/4 \tempo 4 = 100 \upper \bar "|." }
    \new Staff = "down" { \clef bass \key c \major \time 4/4 \lower \bar "|." }
  >>
  \layout { }
  \midi { }
}
