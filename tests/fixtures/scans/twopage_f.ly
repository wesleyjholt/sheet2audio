\version "2.24.0"
\header { title = "Two-Page Ländler in F" composer = "Test fixture (original)" tagline = ##f }
\paper { #(set-paper-size "letter") }
#(set-global-staff-size 24)
upper = \relative c'' {
  \clef treble \key f \major \time 3/4 \tempo 4 = 120
  c4 a f | g4 bes d | c4 a f | e2 g4 |
  f4 a c | d4 c bes | a4 g f | g2. |
  a4 c f | e4 d c | bes4 a g | a2 c4 |
  d4 c bes | a4 g f | e4 f g | f2. | \pageBreak
  a4 bes c | d4 c bes | a4 g f | g2 e4 |
  f4 g a | bes4 a g | f4 e d | c2. |
  c'4 bes a | g4 a bes | a4 g f | e2 g4 |
  a4 bes c | bes4 a g | g4 e c | f2. \bar "|."
}
lower = \relative c {
  \clef bass \key f \major \time 3/4
  f4 <a c> <a c> | c,4 <e bes'> <e bes'> | f4 <a c> <a c> | c,4 <e bes'> <e bes'> |
  f4 <a c> <a c> | bes,4 <d f> <d f> | f4 <a c> <a c> | c,4 <e g> <e g> |
  f4 <a c> <a c> | c,4 <e g> <e g> | c4 <e g> <e bes'> | f4 <a c> <a c> |
  bes,4 <d f> <d f> | f4 <a c> <a c> | c,4 <e bes'> <e bes'> | f2. |
  f4 <a c> <a c> | bes,4 <d f> <d f> | f4 <a c> <a c> | c,4 <e g> <e g> |
  f4 <a c> <a c> | g4 <bes d> <bes d> | a4 <d f> <d f> | c4 <e g> <e g> |
  f4 <a c> <a c> | c,4 <e bes'> <e bes'> | f4 <a c> <a c> | c,4 <e g> <e g> |
  f4 <a c> <a c> | c,4 <e bes'> <e bes'> | c4 <e g> <e bes'> | f2. \bar "|."
}
\score {
  \new PianoStaff << \new Staff = "up" \upper \new Staff = "down" \lower >>
  \layout { }
}
\score {
  \unfoldRepeats \new PianoStaff << \new Staff = "up" \upper \new Staff = "down" \lower >>
  \midi { }
}
