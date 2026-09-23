\version "2.24.0"
% Three short original movements, one per page, two systems per page.
% Tempo marks are "Word (quarter = N)"; Audiveris 5.11 reads these (60/126/144),
% unlike the bare metronome marks in suite3*. Ground truth: trio.midi, trio-1.midi, trio-2.midi.
\header { title = "Three Small Pieces" composer = "Test fixture (original)" tagline = ##f }
\paper { #(set-paper-size "letter") }

\score {
  \header { piece = "Chorale" }
  \new PianoStaff <<
    \new Staff { \clef treble \key bes \major \time 4/4 \tempo "Slowly" 4 = 60
      <d' f'>2 <f' bes'>2 | <ees' g'>2 <f' a'>2 | <d' bes'>4 <f' a'> <g' bes'> <f' c''> | <f' d''>1 | \break
      <g' bes'>2 <f' a'>2 | <ees' g'>4 <d' f'> <c' ees'>2 | <d' f'>2 <c' ees'>2 | <d' f'>1 \bar "|." }
    \new Staff { \clef bass \key bes \major \time 4/4
      bes,2 d2 | c2 f,2 | bes,4 f ees a, | bes,1 |
      ees2 f2 | c4 d f2 | bes,2 f,2 | bes,1 \bar "|." }
  >>
  \layout { }
  \midi { }
}
\pageBreak
\score {
  \header { piece = "Waltz" }
  \new PianoStaff <<
    \new Staff { \clef treble \key e \minor \time 3/4 \tempo "Moderato" 4 = 126
      e'4 g' b' | c''4 b' a' | g'4 fis' e' | dis'2. | \break
      e'4 b' e'' | d''4 c'' b' | a'4 fis' dis' | e'2. \bar "|." }
    \new Staff { \clef bass \key e \minor \time 3/4
      e4 <g b> <g b> | a,4 <e a> <e a> | e4 <g b> <g b> | b,4 <fis a> <fis a> |
      e4 <g b> <g b> | g4 <c' e'> <c' e'> | b,4 <fis a> <fis a> | e2. \bar "|." }
  >>
  \layout { }
  \midi { }
}
\pageBreak
\score {
  \header { piece = "March" }
  \new PianoStaff <<
    \new Staff { \clef treble \key a \major \time 2/4 \tempo "Brisk" 4 = 144
      a'8 cis'' e'' cis'' | d''4 b' | cis''8 a' e' a' | b'2 | \break
      a'8 cis'' e'' a'' | fis''4 d'' | e''8 d'' cis'' b' | a'2 \bar "|." }
    \new Staff { \clef bass \key a \major \time 2/4
      a,4 e | d4 e | a,4 cis | e4 e, |
      a,4 cis | d4 fis | e4 e, | a,2 \bar "|." }
  >>
  \layout { }
  \midi { }
}
