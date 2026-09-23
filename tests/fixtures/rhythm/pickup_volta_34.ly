\version "2.24.0"
\header { title = "Pickup Repeats in G" composer = "Rhythm fixture (original)" tagline = ##f }
\paper { #(set-paper-size "letter") }
upper = \fixed c' {
  \clef treble \key g \major \time 3/4 \tempo 4 = 120
  \repeat volta 2 {
    \partial 4 d'4 |
    g'4 fis'4 g'4 | a'4 b'4 c''4 | b'4 a'4 g'4 | fis'2 d'4 |
    e'4 fis'4 g'4 | a'2 b'4 | a'4 g'4 fis'4 |
  }
  \alternative { { g'2 } { g'2 } }
  \repeat volta 2 {
    b'4 |
    d''4 c''4 b'4 | a'2 g'4 | fis'4 g'4 a'4 | d'2 d'4 |
    e'4 g'4 b'4 | a'4 g'4 fis'4 | g'2
  }
}
lower = \fixed c {
  \clef bass \key g \major \time 3/4
  \repeat volta 2 {
    \partial 4 r4 |
    g,4 <b, d>4 <b, d>4 | fis,4 <a, d>4 <a, d>4 | g,4 <b, d>4 <b, d>4 | d4 <fis a>4 r4 |
    c4 <e g>4 <e g>4 | d4 <fis a>4 <fis a>4 | d4 <fis a>4 <fis a>4 |
  }
  \alternative { { g,2 } { g,2 } }
  \repeat volta 2 {
    r4 |
    g,4 <b, d>4 <b, d>4 | d4 <fis a>4 <fis a>4 | d4 <fis a>4 <fis a>4 | d2 r4 |
    c4 <e g>4 <e g>4 | d4 <fis a>4 <fis a>4 | g,2
  }
}
music = \new PianoStaff << \new Staff = "up" \upper \new Staff = "down" \lower >>
\score { \music \layout { } }
\score { \unfoldRepeats \music \midi { } }
