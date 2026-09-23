\version "2.24.0"
% Original test piece for the rest-repair logic in multi-voice bars: two
% voices in the right hand (whole piece) and in the left hand (bars 9-12),
% and most bars END WITH A REST IN EVERY VOICE, so a missed rest leaves the
% whole bar short. Bar 9 also has a rest in the middle of every voice.
\header { title = "Resting Voices" composer = "Test fixture (original)" tagline = ##f }
\paper { #(set-paper-size "letter") }

sop = \fixed c'' {
  e4 f g r | a4 g f r | e2 d4 r | c2 r2 |
  g4 a b r | c'2 b4 r | a4 g f e | d2 r2 |
  e4 r g r | f2 e4 r | d4 e d r | c2 r2 |
}
alto = \fixed c' {
  c'2 e'4 r | f'4 e' d' r | c'4 b a r | g2 r2 |
  b4 c' d' r | e'4 d' d' r | f'4 e' d' c' | b2 r2 |
  c'4 r e' r | d'2 c'4 r | b4 c' b r | g2 r2 |
}
tenor = \fixed c {
  e4 r e r | a2 e4 r | d4 e d r | e2 r2 |
}
\score {
  \new PianoStaff <<
    \new Staff = "up" { \clef treble \key c \major \time 4/4 \tempo 4 = 100
      << \sop \\ \alto >> \bar "|." }
    \new Staff = "down" { \clef bass \key c \major \time 4/4
      \fixed c {
        c2 c4 r | f,2 g,4 r | c2 g,4 r | c2 r2 |
        g,2 g,4 r | c2 g,4 r | f,2 g,2 | g,2 r2 |
      }
      << \tenor \\ \fixed c { c4 r c r | f,2 c4 r | g,4 c g, r | c2 r2 | } >>
      \bar "|." }
  >>
  \layout { }
  \midi { }
}
