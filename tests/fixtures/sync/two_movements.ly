\version "2.24.0"
% Original composition written for the sheet2audio "sync" tests (public domain / CC0).
% Two \score blocks (-> two movements if OMR splits them); movement II ends on a high
% chord (E6/G6, MIDI 88/91) to exercise the synthesizer tail. Each piece has two systems
% (\break) so the indented first system of piece II marks a new movement for OMR.
\header { title = "Two Small Pieces" composer = "Sync test fixture (original)" tagline = ##f }
\paper { #(set-paper-size "letter") }
\score {
  \new PianoStaff <<
    \new Staff { \clef treble \key c \major \time 3/4 \tempo 4 = 100
      e''4 d'' c'' | g'2 c''4 | d''4 e'' f'' | e''2. | \break
      a''4 g'' f'' | e''4 d'' c'' | d''4 b' g' | c''2. \bar "|." }
    \new Staff { \clef bass \key c \major \time 3/4
      c4 <e g> <e g> | e4 <g c'> <g c'> | f4 <a c'> <a c'> | c4 <e g> <e g> |
      f4 <a c'> <a c'> | c4 <e g> <e g> | g,4 <d f> <d f> | c2. \bar "|." }
  >>
  \header { piece = "I. Minuet" }
  \layout { } \midi { }
}
\score {
  \new PianoStaff <<
    \new Staff { \clef treble \key c \major \time 2/4 \tempo 4 = 132
      c''8 e'' g'' c''' | b''8 g'' d'' g'' | a''8 f'' c'' f'' | g''8 e'' c'' e'' | \break
      f''8 a'' c''' a'' | g''8 b'' d''' b'' | c'''8 e''' g''' e''' | <c''' e''' g'''>2 \bar "|." }
    \new Staff { \clef bass \key c \major \time 2/4
      c4 <e g> | g,4 <d g> | f,4 <c f> | c4 <e g> |
      f,4 <c f> | g,4 <d g> | c4 <e g> | <c, c>2 \bar "|." }
  >>
  \header { piece = "II. Finale" }
  \layout { } \midi { }
}
