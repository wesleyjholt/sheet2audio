\version "2.24.0"
\header { title = "End Repeats Only" composer = "Test fixture (original)" tagline = ##f }
\paper { #(set-paper-size "letter") }
% A :| B :| with NO start-repeat sign before B (common hymn/folk engraving).
% By convention the second :| returns to the previous :|, so it plays A A B B.
aUp = \fixed c'' { d4 fis a fis | g4 e cis2 | d4 b, a, cis | d2 a,2 | }
bUp = \fixed c'' { fis4 g a b | a4 fis e2 | g4 fis e cis | d1 | }
aLo = \fixed c { d2 <fis a> | a,2 <e g> | b,2 a, | d2 a,2 | }
bLo = \fixed c { d2 <fis a> | d2 <cis e> | e2 a, | d1 | }
hdr = { \clef treble \key d \major \time 4/4 \tempo 4 = 120 }
hdrLo = { \clef bass \key d \major \time 4/4 }
\score {
  \new PianoStaff <<
    \new Staff { \hdr \aUp \bar ":|." \bUp \bar ":|." }
    \new Staff { \hdrLo \aLo \bLo }
  >>
  \layout { }
}
\score {
  \new PianoStaff << \new Staff { \hdr \aUp \aUp \bUp \bUp } \new Staff { \hdrLo \aLo \aLo \bLo \bLo } >>
  \midi { }
}
