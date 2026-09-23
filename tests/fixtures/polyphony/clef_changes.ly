\version "2.24.0"
% Original test piece: the left hand changes clef bass -> treble -> bass, both
% at barlines and in the middle of a bar; the right hand briefly goes into
% bass clef (bars 13-14) and back.
\header { title = "Changing Clefs" composer = "Test fixture (original)" tagline = ##f }
\paper { #(set-paper-size "letter") }

upper = \fixed c'' {
  g4 b d' | c'2 a4 | b4 g e | d2. |
  e4 g c' | b2 g4 | a4 fis d | g2. |
  d'4 b g | c'4 a fis | g4 d b, | c2. |
  \clef bass
  g,,4 b,, d, | c,2. |
  \clef treble
  g4 d' b | g2. |
}
lower = \fixed c {
  \clef bass
  g,4 d b, | a,2 d4 | g,4 b, c | d2. |
  \clef treble
  c''4 e'' g' | d''2 b'4 | c''4 a' fis' | g'2. |
  b'4 g' \clef bass d | e4 c a, | b,4 g, d, | e,2. |
  g4 d b, | e4 c a, |
  \clef treble
  b'4 d'' \clef bass g, | g,,2. |
}

\score {
  \new PianoStaff <<
    \new Staff = "up" { \clef treble \key g \major \time 3/4 \tempo 4 = 110 \upper \bar "|." }
    \new Staff = "down" { \key g \major \time 3/4 \lower \bar "|." }
  >>
  \layout { }
  \midi { }
}
