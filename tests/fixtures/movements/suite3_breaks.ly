\version "2.24.0"
% Three short original movements in one PDF, each starting on a new page,
% with a forced line break after bar 4 so every page has two (indented-first) systems.
% Each \score has its own key, meter and tempo. Ground-truth MIDI:
% suite3_breaks.midi, suite3_breaks-1.midi, suite3_breaks-2.midi.
\header { title = "Little Suite" composer = "Test fixture (original)" tagline = ##f }
\paper { #(set-paper-size "letter") }

\score {
  \header { piece = "I. Prelude" }
  \new PianoStaff <<
    \new Staff { \clef treble \key c \major \time 4/4 \tempo 4 = 96
      c'4 e' g' e' | f'4 a' g'2 | e'4 g' c'' b' | a'4 f' g'2 | \break
      c''4 b' a' g' | f'4 e' d' c' | d'4 e' f' d' | c'1 \bar "|." }
    \new Staff { \clef bass \key c \major \time 4/4
      c2 g, | f,2 c | c2 e | f2 g | a2 e | f2 c | g2 g, | c1 \bar "|." }
  >>
  \layout { }
  \midi { }
}

\pageBreak

\score {
  \header { piece = "II. Minuet" }
  \new PianoStaff <<
    \new Staff { \clef treble \key f \major \time 3/4 \tempo 4 = 132
      f'4 a' c'' | bes'4 a' g' | a'8 bes' c''4 f' | e'2. | \break
      d'4 f' bes' | a'4 g' f' | g'8 a' g'4 e' | f'2. \bar "|." }
    \new Staff { \clef bass \key f \major \time 3/4
      f,4 <a c'> <a c'> | c4 <g bes> <g bes> | f,4 <a c'> <a c'> | c4 <g bes> <g bes> |
      bes,4 <d f> <d f> | f,4 <a c'> <a c'> | c4 <g bes> <e g> | f,2. \bar "|." }
  >>
  \layout { }
  \midi { }
}

\pageBreak

\score {
  \header { piece = "III. Jig" }
  \new PianoStaff <<
    \new Staff { \clef treble \key d \major \time 6/8 \tempo 4. = 80
      d'8 fis' a' d''4 a'8 | b'8 a' g' fis'4 e'8 | d'8 fis' a' b'4 a'8 | g'8 e' cis' e'4. | \break
      d'8 fis' a' d''4 e''8 | fis''8 e'' d'' cis''4 a'8 | b'8 g' e' a' g' e' | d'2. \bar "|." }
    \new Staff { \clef bass \key d \major \time 6/8
      d4. fis4. | g4. a4. | d4. g4. | a4. a,4. |
      d4. fis4. | a4. a4. | g4. a4. | d2. \bar "|." }
  >>
  \layout { }
  \midi { }
}
