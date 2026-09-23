\version "2.24.0"
% Original composition written for the sheet2audio "sync" tests (public domain / CC0).
% Tempo 90 -> 140, a volta repeat (bars 5-8), a tie (bar 7) and a final fermata.
\header { title = "Tempo Study" composer = "Sync test fixture (original)" tagline = ##f }
\paper { #(set-paper-size "letter") }
upper = {
  \clef treble \key c \major \time 4/4
  \tempo 4 = 90
  c''4 e'' g'' e'' | f''4 a'' g'' f'' | e''4 d'' c'' b' | c''2 g'2 |
  \repeat volta 2 {
    e''4 f'' g'' e'' | a''4 f'' d'' f'' | g''2 ~ g''4 b'4 | c''1 |
  }
  \tempo 4 = 140
  c''8 d'' e'' f'' g''4 g'' | a''8 g'' f'' e'' d''4 d'' | e''8 f'' g'' a'' b''4 g'' | c'''2 g''2 |
  a''8 g'' f'' e'' f''4 d'' | e''4 c'' d'' b' | c''4 e'' d'' b' | c''1\fermata \bar "|."
}
lower = {
  \clef bass \key c \major \time 4/4
  c4 <e g> c <e g> | f4 <a c'> f <a c'> | g4 <b d'> g <b d'> | c2 g,2 |
  \repeat volta 2 {
    c4 <e g> c <e g> | d4 <f a> d <f a> | g,4 <d f> g, <d f> | c1 |
  }
  c4 <e g> <e g> <e g> | f4 <a c'> g <b d'> | c4 <e g> g <b d'> | e4 <g c'> e <g c'> |
  f4 <a c'> d <f a> | g4 <b d'> g <b d'> | a4 <c' e'> g <b d'> | <c g c'>1\fermata \bar "|."
}
music = \new PianoStaff << \new Staff = "up" \upper \new Staff = "down" \lower >>
\score { \music \layout { } }
\score { \unfoldRepeats \music \midi { } }
