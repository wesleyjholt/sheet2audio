\version "2.24.0"
% Original composition for the sheet2audio "meters" test area.
% Voice + piano, 8 measures, 4 per line.
%   m1-2   4/4
%   m3     2/4, mid-line, NOT PRINTED (one short bar in every staff)
%   m4-8   4/4, NOT PRINTED either: OMR sees a 4/4 piece with one short bar,
%          which must not be padded with rests.
\header { title = "Unprinted Two" composer = "Test fixture (original)" tagline = ##f }
\paper { #(set-paper-size "letter") ragged-last-bottom = ##t }

global = {
  \time 4/4
  s1*2 \once \omit Staff.TimeSignature \time 2/4 s2
  \once \omit Staff.TimeSignature \time 4/4 s1 \break
  s1*4 \bar "|."
}

voice = {
  \tempo 4 = 90
  g'4 a' b' c'' | d''2 c''4 b' | a'4 g' |
  f'4. e'8 d'4 g' |
  e'4 f' g' a' | b'8 a' g'4 f' e' | d'4 e'8 f' g'4 b' | c''1 |
}

upper = {
  <e' g'>4 <f' a'> <g' b'> <e' c''> | <f' b' d''>2 <e' g' c''>4 <d' g' b'> | <c' f' a'>4 <b d' g'> |
  <a d' f'>4. e'8 <b d'>4 <b d' g'> |
  <c' e'>4 <c' f'> <e' g'> <c' f' a'> | <d' g' b'>8 a' <b d' g'>4 <a d' f'> <g c' e'> | <b d'>4 e'8 f' <b d' g'>4 <d' g' b'> | <e' g' c''>1 |
}

lower = {
  \clef bass
  c4 f e a, | g,2 c4 g, | f,4 g, |
  d4. c8 b,4 g, |
  c4 a, e f | g4 b, c c | g,4 c g, g | c1 |
}

music = <<
  \new Staff << \global \voice >>
  \new PianoStaff <<
    \new Staff << \global \upper >>
    \new Staff << \global \lower >>
  >>
>>

\score { \music \layout { } }
\score { \music \midi { } }
