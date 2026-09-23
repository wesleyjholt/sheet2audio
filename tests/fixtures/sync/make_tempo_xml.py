"""Derive tempo_study_tempi.musicxml from the pipeline's OMR of tempo_study.pdf.

Audiveris 5.11 does not read the metronome marks of tempo_study.pdf, so this
adds them explicitly (and a written-out ritardando) to exercise the tempo-change
path of the pipeline:
  bar 1   quarter = 90
  bar 9   quarter = 140   (replaces the bare "140" <words> Audiveris produced)
  bar 15  rit.: quarter = 120 / 100 / 80 on beats 2 / 3 / 4 (mid-measure changes)
  bar 16  fermata (already read by Audiveris)

    uv run python tests/fixtures/sync/make_tempo_xml.py out/sync/tempo/tempo_study.musicxml \
        tests/fixtures/sync/tempo_study_tempi.musicxml
"""
import sys
import xml.etree.ElementTree as ET


def tempo_direction(bpm, words=None):
    d = ET.Element("direction", placement="above")
    dt = ET.SubElement(d, "direction-type")
    if words:
        ET.SubElement(dt, "words").text = words
    else:
        m = ET.SubElement(dt, "metronome")
        ET.SubElement(m, "beat-unit").text = "quarter"
        ET.SubElement(m, "per-minute").text = str(bpm)
    ET.SubElement(d, "staff").text = "1"
    ET.SubElement(d, "sound", tempo=str(bpm))
    return d


def main(src, dst):
    tree = ET.parse(src)
    part = tree.getroot().find("part")
    meas = {m.get("number"): m for m in part.findall("measure")}
    # bar 1: before the first <note>
    m1 = meas["1"]
    first_note = next(i for i, el in enumerate(m1) if el.tag == "note")
    m1.insert(first_note, tempo_direction(90))
    # bar 9: drop Audiveris' "140" words, add a real metronome mark
    m9 = meas["9"]
    for d in m9.findall("direction"):
        if "".join(d.itertext()).strip() == "140":
            m9.remove(d)
    first_note = next(i for i, el in enumerate(m9) if el.tag == "note")
    m9.insert(first_note, tempo_direction(140))
    # bar 15: ritardando written as tempo steps before staff-1 notes 2, 3, 4
    m15 = meas["15"]
    notes = [el for el in m15 if el.tag == "note" and (el.findtext("staff") or "1") == "1"
             and el.find("chord") is None]
    for n, bpm in zip(notes[1:4], (120, 100, 80)):
        idx = list(m15).index(n)
        m15.insert(idx, tempo_direction(bpm, words="rit." if bpm == 120 else None))
    ET.indent(tree)
    tree.write(dst, encoding="UTF-8", xml_declaration=True)


if __name__ == "__main__":
    main(*sys.argv[1:3])
