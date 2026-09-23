\version "2.24.0"
% Original test piece: two independent voices in the right hand (\voiceOne
% melody + \voiceTwo inner line), and two voices in the left hand in bars 9-12.
% Voice two has rests at the start (bar 4) and end (bar 8) of some bars.
\header { title = "Two Voices in F" composer = "Test fixture (original)" tagline = ##f }
\paper { #(set-paper-size "letter") }

melody = \fixed c'' {
  c4 d e f | g2 e | f4 e d c | d1 |
  e4 f g a | bes2 a | g4 f e d | c1 |
  a4 g f e | f2 d | e4 d c bes, | a,2 g, |
  c4 d e f | g2 bes | a4 g f e | f1 |
}
inner = \fixed c' {
  a2 bes | c'2 c' | a4 g f a | r4 bes a g |
  c'2 e' | d'4 e' f' c' | e'4 d' c' bes | a2 r2 |
  f'4 e' d' c' | c'2 bes | c'4 bes a g | f2 e |
  a2 c' | e'4 c' g'2 | f'2 c'4 bes | a1 |
}
lower = \fixed c {
  f,2 c | e,2 c | f,2 bes, | bes,2 g, |
  c2 c | g,2 f, | c2 c, | f,2 r2 |
  << { f2 f | f2 f | g2 e | c2 c } \\ { d4 c bes, a, | a,4 g, f, bes, | c1 | f,2 c, } >> |
  f,2 a, | c2 e, | f,4 a, c c, | f,1 |
}

\score {
  \new PianoStaff <<
    \new Staff = "up" { \clef treble \key f \major \time 4/4 \tempo 4 = 100
      << \melody \\ \inner >> \bar "|." }
    \new Staff = "down" { \clef bass \key f \major \time 4/4 \lower \bar "|." }
  >>
  \layout { }
  \midi { }
}
