import sys
sys.path.insert(0, "/Users/holtw/.local/share/project-worktrees/sheet-music-to-audio/src")
from sheet2audio.musicxml import repair_underfull_measures
from sheet2audio.render import render_musicxml

def score(measures, divisions):
    body = "".join(f'<measure number="{i+1}">' + ("<attributes><divisions>%d</divisions><time><beats>2</beats><beat-type>4</beat-type></time><clef><sign>G</sign><line>2</line></clef></attributes>" % divisions if i == 0 else "") + m + "</measure>" for i, m in enumerate(measures))
    return ('<?xml version="1.0" encoding="UTF-8"?><score-partwise version="4.0"><part-list><score-part id="P1"><part-name>P</part-name></score-part></part-list>'
            f'<part id="P1">{body}</part></score-partwise>').encode()

def n(step, dur, typ, extra=""):
    return f"<note><pitch><step>{step}</step><octave>5</octave></pitch><duration>{dur}</duration><voice>1</voice><type>{typ}</type>{extra}</note>"

full = n("C", 2, "quarter") * 2
def bars(xml):
    r = render_musicxml(xml, "t")
    s = [e["qstamp"] for e in r.timemap if "measureOn" in e]
    return s, [round(b - a, 4) for a, b in zip(s, s[1:])]

# Case A: chord note whose duration differs from its main note (inconsistent), measure short.
chord = n("C", 1, "quarter") + n("E", 2, "half", "<chord/>")  # main note 1 div, chord note 2 div (divisions=2 -> 0.5/1 ql)
xa = score([full, chord, full], 2)
ya, rep = repair_underfull_measures(xa)
print("A padded report:", [(m.number, m.actual_beats) for m in rep.padded], "| xml changed:", ya.count(b"<rest") > 0)
print("A verovio bar starts/lengths after 'repair':", bars(ya))

# Case B: eighth-triplet written with rounded-down durations (divisions=4: 1+1+1 instead of 4/3 each)
tm = "<time-modification><actual-notes>3</actual-notes><normal-notes>2</normal-notes></time-modification>"
trip = (n("C", 1, "eighth", tm + '<notations><tuplet type="start"/></notations>') + n("D", 1, "eighth", tm)
        + n("E", 1, "eighth", tm + '<notations><tuplet type="stop"/></notations>') + n("F", 4, "quarter"))
xb = score([n("C", 4, "quarter") * 2, trip, n("C", 4, "quarter") * 2], 4)
print("B no-repair verovio:", bars(xb))
yb, rep = repair_underfull_measures(xb)
print("B padded report:", [(m.number, m.actual_beats) for m in rep.padded])
print("B repaired verovio:", bars(yb))
