\version "2.24.0"
% Original test piece: block chords with sharps, flats, naturals, double sharps
% and double flats against a 4-sharp key signature, plus courtesy accidentals
% (forced "!" and parenthesised "?") after altered notes.
\header { title = "Accidental Chords" composer = "Test fixture (original)" tagline = ##f }
\paper { #(set-paper-size "letter") }

upper = \fixed c' {
  <e gis b>4 <e a cis'> <dis fis b> <e gis b> |
  <fisis ais cis'>4 <gis bis dis'> <g b d'> <fis a c'> |
  <e gis! b>4 <cis' e' a'> <bes d' f'> <a c' e'> |
  <b dis' fis'>2 <e gis b>2 |
  <g bes des'>4 <aes c' ees'> <beses des' f'> <a cis' e'> |
  <gis b? e'>4 <fis a! dis'> <dis fis a b> <e gis b e'> |
  <a cis' e'>2 <gis bis dis' fisis'>2 |
  <cis' e' gis'>1 |
  <cis' eis' gis'>4 <d' fis' a'> <dis' fisis' ais'> <e' gis' b'> |
  <f' a' c''>2 <fis'? a' c''>2 |
  <b dis' fis' a'>2 <b d' f' aes'>2 |
  <e gis b e'>1 |
}
lower = \fixed c {
  e,2 b,2 | <dis a>2 <d aes>2 | cis2 c2 | b,2 e,2 |
  <ees bes>2 <f c'>2 | e2 b,2 | a,2 gis,2 | cis1 |
  cis2 dis2 | f2 fis2 | b,1 | e,1 |
}

\score {
  \new PianoStaff <<
    \new Staff = "up" { \clef treble \key e \major \time 4/4 \tempo 4 = 90 \upper \bar "|." }
    \new Staff = "down" { \clef bass \key e \major \time 4/4 \lower \bar "|." }
  >>
  \layout { }
  \midi { }
}
