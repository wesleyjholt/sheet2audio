\version "2.24.0"
% A text-only cover page, then two original movements, each on its own page
% with two systems. Movement II uses 6/8 with a dotted-quarter metronome mark.
% Ground truth: duo_cover.midi (I. Song), duo_cover-1.midi (II. Round).
\header { tagline = ##f }
\paper { #(set-paper-size "letter") print-page-number = ##f }

\markup \fill-line { \column {
  \vspace #12
  \fill-line { \abs-fontsize #36 \bold "Two Pieces for Piano" }
  \vspace #3
  \fill-line { \abs-fontsize #18 "I. Song" }
  \fill-line { \abs-fontsize #18 "II. Round" }
  \vspace #3
  \fill-line { \abs-fontsize #14 \italic "Test fixture (original)" }
} }
\pageBreak

\score {
  \header { piece = "I. Song" }
  \new PianoStaff <<
    \new Staff { \clef treble \key g \major \time 4/4 \tempo "Andante" 4 = 80
      g'4 b' d'' b' | c''4 a' fis'2 | g'4 a' b' c'' | d''2 d''2 | \break
      e''4 c'' a' c'' | b'4 g' e'2 | a'4 fis' d' fis' | g'1 \bar "|." }
    \new Staff { \clef bass \key g \major \time 4/4
      g2 d | a,2 d | g2 e | d2 fis |
      c2 a, | g,2 c | d2 d | g,1 \bar "|." }
  >>
  \layout { }
  \midi { }
}
\pageBreak
\score {
  \header { piece = "II. Round" }
  \new PianoStaff <<
    \new Staff { \clef treble \key d \minor \time 6/8 \tempo "Lively" 4. = 96
      d''8 a' f' d'4 f'8 | e'8 g' bes' a'4. | d''8 a' f' d'4 a'8 | g'8 f' e' d'4. | \break
      f'8 a' d'' c''4 a'8 | bes'8 g' e' a'4. | d''8 c'' bes' a' g' e' | d'2. \bar "|." }
    \new Staff { \clef bass \key d \minor \time 6/8
      d4. d4. | a,4. a,4. | d4. f4. | a,4. d4. |
      d4. f4. | g4. a4. | bes,4. a,4. | d2. \bar "|." }
  >>
  \layout { }
  \midi { }
}
