\version "2.24.0"
% Original composition for the sheet2audio "multipage" test area.
% 36 measures on 3 forced pages.
%   page 1  m1-12  4/4  D major -> F major at m7 (mid-system); tempo 4=100, 4=120 at m9
%   page 2  m13-24 3/4  (2/4 at m19-20, mid-system), tempo 4=144
%   page 3  m25-36 6/8  G major, tempo 4.=60 (= quarter 90)
\header { title = "Three Moods" composer = "Test fixture (original)" tagline = ##f }
\paper { #(set-paper-size "letter") ragged-last-bottom = ##t }

global = {
  \time 4/4 \key d \major
  s1*4 \break
  s1*2 \key f \major s1*2 \break
  s1*4 \pageBreak
  \time 3/4
  s2.*4 \break
  s2.*2 \time 2/4 s2*2 \break
  \time 3/4 s2.*4 \pageBreak
  \time 6/8 \key g \major
  s2.*4 \break
  s2.*4 \break
  s2.*4 \bar "|."
}

tempi = {
  \tempo "Moderato" 4 = 100 s1*8
  \tempo "Piu mosso" 4 = 120 s1*4
  \tempo "Allegro" 4 = 144 s2.*6 s2*2 s2.*4
  \tempo "Andante" 4. = 60 s2.*12
}

upper = {
  \clef treble
  % page 1 (4/4)
  d'4 fis' a' d'' | cis''4 b' a'2 | b'4 a' g' fis' | e'2 a'2 |
  fis'8 g' a' b' a'4 d'' | cis''4 e'' d''2 |
  c''4 a' f' a' | bes'4 g' e'2 |
  f'8 g' a' bes' c''4 f'' | e''4 d'' c''2 | d''4 bes' g' e' | f'2. r4 |
  % page 2 (3/4, 2/4)
  a'4 c'' f'' | e''4 d'' c'' | bes'8 a' g'4 c'' | a'2. |
  c''8 d'' e'' f'' g''4 | f''4 e'' d'' |
  c''4 a' | bes'4 g' |
  a'4 f' a' | c''4 bes' g' | a'8 bes' c''4 e' | f'2. |
  % page 3 (6/8)
  d''8 b' g' d''4 b'8 | c''4 a'8 fis'4. | g'8 a' b' c''4 d''8 | e''4. d''4. |
  b'8 c'' d'' g''4 e''8 | d''4 b'8 a'4. | g'8 b' d'' c''4 a'8 | fis'4. d'4. |
  g'8 a' b' c'' b' a' | d''4. fis'4. | g'4 b'8 a'4 fis'8 | g'4. r4. |
}

lower = {
  \clef bass
  % page 1
  d4 a d' a | a,4 e a2 | g,4 d b,2 | a,2 e2 |
  d4 a fis a | a,4 a, d2 |
  f4 c' a c' | c4 g bes2 |
  f,4 c f c | c4 g c'2 | bes,4 d g c | f,2. r4 |
  % page 2
  f4 <a c'> <a c'> | c4 <g c'> <g c'> | c4 <e bes> <e bes> | f4 <a c'> <a c'> |
  bes,4 <d f> <d f> | c4 <f a> <f a> |
  f4 c | e4 c |
  f4 <a c'> <a c'> | e4 <g c'> <g c'> | c4 <e g> <e bes> | f,2. |
  % page 3
  g,8 d g b4. | a,8 e a d4. | g,8 d g e4. | c8 g c' d4. |
  g,8 d g b4. | d8 a d' fis4. | e8 b e' a,4. | d8 a d' d4. |
  e8 b e' c4. | d8 a c' d4. | c4. d4. | g,4. r4. |
}

music = \new PianoStaff <<
  \new Staff = "up" << \global \tempi \upper >>
  \new Staff = "down" << \global \lower >>
>>

\score { \music \layout { } }
\score { \unfoldRepeats \music \midi { } }
