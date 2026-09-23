\version "2.24.0"
% Two original movements on ONE page (Audiveris merges them into one movement).
% Movement II starts with a one-beat pickup, so after merging the pickup sits
% mid-score. Ground truth: pickup_onepage.midi (I, 4/4, 4 bars),
% pickup_onepage-1.midi (II, 3/4, pickup 1 quarter + 7 bars + 2-beat final bar).
\header { title = "Hymn and Dance" composer = "Test fixture (original)" tagline = ##f }
\paper { #(set-paper-size "letter") }

\score {
  \header { piece = "I. Hymn" }
  \new PianoStaff <<
    \new Staff { \clef treble \key f \major \time 4/4 \tempo "Calm" 4 = 72
      f'4 g' a' f' | bes'4 a' g'2 | a'4 c'' bes' g' | f'1 \bar "|." }
    \new Staff { \clef bass \key f \major \time 4/4
      f2 c | bes,2 c | f2 c | f,1 \bar "|." }
  >>
  \layout { }
  \midi { }
}

\score {
  \header { piece = "II. Dance" }
  \new PianoStaff <<
    \new Staff { \clef treble \key c \major \time 3/4 \tempo "Quick" 4 = 132
      \partial 4 g'4 | c''4 e'' d'' | c''4 b' a' | g'4 e' f' | g'2 g'4 |
      a'4 f' d' | g'4 e' c' | d'4 b g | c'2 \bar "|." }
    \new Staff { \clef bass \key c \major \time 3/4
      \partial 4 r4 | c4 <e g> <e g> | f4 <a c'> <a c'> | c4 <e g> <e g> | g,4 <b, d> <b, d> |
      f4 <a c'> <a c'> | c4 <e g> <e g> | g,4 <b, f> <b, f> | c2 \bar "|." }
  >>
  \layout { }
  \midi { }
}
