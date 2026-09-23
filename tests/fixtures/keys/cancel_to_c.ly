\version "2.24.0"
% Original composition for the sheet2audio "keys" test area.
% 16 measures of 4/4, 4 per line.
%   m1-5   B-flat major
%   m6     C major, mid-line: the old key is cancelled with natural signs only
%          (Audiveris' MusicXML export drops this change)
%   m9     F major at the start of a line
%   m11    D major, mid-line: natural sign, then two sharps
\header { title = "Cancelled Flats" composer = "Test fixture (original)" tagline = ##f }
\paper { #(set-paper-size "letter") ragged-last-bottom = ##t }

global = {
  \time 4/4 \key bes \major
  s1*4 \break
  s1 \key c \major s1*3 \break
  \key f \major s1*2 \key d \major s1*2 \break
  s1*4 \bar "|."
}

upper = {
  \tempo 4 = 96
  bes'4 d'' f'' d'' | ees''4 c'' a' f' | g'4 bes' ees'' g'' | f''2 d'' |
  c''4 ees'' bes' g' | e''4 g'' b' e' | b'4 d'' g'' b'' | c'''2 e'' |
  a'4 c'' f'' bes' | g'4 bes' e'' c'' | fis'4 a' d'' fis'' | e''4 cis'' a' e' |
  d'4 fis' a' cis'' | b'4 g' e' b | a4 d' fis' a' | d''1 |
}

lower = {
  \clef bass
  bes2 f | ees2 f | ees2 c | bes,1 |
  f2 ees | c2 e | g,2 b, | c1 |
  f2 a | c2 bes, | d2 fis | a,2 cis |
  d2 fis | g,2 b, | a,2 cis | d1 |
}

music = \new PianoStaff <<
  \new Staff = "up" << \global \upper >>
  \new Staff = "down" << \global \lower >>
>>

\score { \music \layout { } }
\score { \music \midi { } }
