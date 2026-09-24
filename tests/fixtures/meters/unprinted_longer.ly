\version "2.24.0"
% Original composition for the sheet2audio "meters" test area.
% Voice + piano, 12 measures on 2 pages, 4 per line.
%   m1-2   2/4
%   m3     4/4, mid-line, NOT PRINTED: OMR keeps expecting 2/4, as when it
%          misses a printed time signature, and must not lose the notes that
%          do not fit (they continue over the page break).
\header { title = "Unprinted Four" composer = "Test fixture (original)" tagline = ##f }
\paper { #(set-paper-size "letter") ragged-last-bottom = ##t }

global = {
  \time 2/4
  s2*2 \once \omit Staff.TimeSignature \time 4/4 s1*2 \break
  s1*4 \pageBreak
  s1*4 \bar "|."
}

voice = {
  \tempo 4 = 100
  r4 c''8 b' | a'4 g' |
  e'4. f'8 g'4 a' | b'8 c'' d''4 c'' b' |
  a'4 g'8 f' e'4 g' | c''2 b'4 r | a'4 a'8 b' c''4 d'' | e''2 d''4 c'' |
  b'4. a'8 g'4 f' | e'8 f' g'4 a' b' | c''4 e'' d'' b' | c''1 |
}

upper = {
  <e' g'>4 <f' a'> | <e' c''>4 <d' b'> |
  <c' e' g'>4 <c' f'> <e' g'>8 c' <f' a'>4 | <g' b'>4 <f' d''> <e' c''>8 g' <d' b'>4 |
  <c' a'>4 <b g'> <c' e'>8 g <e' g'>4 | <e' c''>2 <d' g' b'>4 r | <c' f' a'>4 f'8 g' <e' a' c''>4 <f' b' d''> | <g' c'' e''>2 <f' b' d''>4 <e' g' c''> |
  <d' g' b'>4 <c' e' a'> <b d' g'>4 <a d' f'> | <g c' e'>4 g'8 f' <c' e' a'>4 <d' f' b'> | <e' a' c''>4 <g' c'' e''> <f' b' d''> <d' g' b'> | <e' g' c''>1 |
}

lower = {
  \clef bass
  c2 | g,2 |
  c4 g e g | g,4 d g g, |
  f,4 c a, c | g,2 g4 r | f,4 c f a, | c2 g,4 c |
  g,4 a, b, d | c4 e f d | a,4 c g, g | c1 |
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
