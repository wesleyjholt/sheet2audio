\version "2.24.0"
\header { title = "Da Capo Study in F (marks above)" composer = "Test fixture (original)" tagline = ##f }
\paper { #(set-paper-size "letter") }
% Same music as dc_alfine.ly, but "Fine" and "D.C. al Fine" are plain text
% above the treble staff (where most editors put them). The PDF is engraved
% from the written form; the MIDI plays A B A explicitly.
aUp = \fixed c'' { f,4 a, c a, | bes,4 d c2 | a,4 c bes, g, | f,1 | }
bUp = \fixed c'' { c4 d e f | g4 e c2 | d4 bes, g, e, | c,2 r2 | }
aLo = \fixed c { f,2 <a, c> | bes,2 <a, c> | f,2 c | f,1 | }
bLo = \fixed c { c2 <e g> | c2 <e g> | bes,2 <c e> | c,2 r2 | }
hdr = { \clef treble \key f \major \time 4/4 \tempo 4 = 120 }
hdrLo = { \clef bass \key f \major \time 4/4 }
writtenUp = \fixed c'' {
  \hdr
  f,4 a, c a, | bes,4 d c2 | a,4 c bes, g, | f,1^\markup \italic \bold "Fine" \bar "||"
  c4 d e f | g4 e c2 | d4 bes, g, e, | c,2 r2^\markup \italic \bold "D.C. al Fine" \bar "|."
}
writtenLo = { \hdrLo \aLo \bLo \bar "|." }
\score { \new PianoStaff << \new Staff \writtenUp \new Staff \writtenLo >> \layout { } }
\score {
  \new PianoStaff << \new Staff { \hdr \aUp \bUp \aUp } \new Staff { \hdrLo \aLo \bLo \aLo } >>
  \midi { }
}
