\version "2.24.0"
\header { title = "Repeat Across a Page Turn" composer = "Test fixture (original)" tagline = ##f }
\paper { #(set-paper-size "letter") }
% 12 written bars on 2 pages; the repeat |: m3 ... m10 :| spans the page break. Plays 20 bars.
upper = \fixed c'' {
  \clef treble \key c \major \time 4/4 \tempo 4 = 120
  c4 e g e | d4 f g2 |
  \repeat volta 2 {
    e4 d c e | f4 e d f | \break g4 f e d | c4 d e2 | \pageBreak
    a,4 b, c d | e4 f g2 | \break f4 d b, g, | c2 g,2 |
  }
  e4 d c b, | c1 \bar "|."
}
lower = \fixed c {
  \clef bass \key c \major \time 4/4
  c2 <e g> | b,2 <d g> |
  \repeat volta 2 {
    c2 <e g> | d2 <f a> | e2 <g b> | a,2 <c e> |
    f,2 <a, c> | c2 <e g> | g,2 <b, f> | c2 g,2 |
  }
  g,2 <b, d> | c1 \bar "|."
}
music = \new PianoStaff << \new Staff = "up" \upper \new Staff = "down" \lower >>
\score { \music \layout { } }
\score { \unfoldRepeats \music \midi { } }
