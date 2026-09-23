"""Fast unit tests (no Audiveris, no audio)."""

from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET
from fractions import Fraction

import mido
import verovio

from sheet2audio import musicxml as mx
from sheet2audio.render import (_heard_from_root, _normalize_ids, combine_midi,
                                heard_measure_lengths, render_musicxml)


def note(step, octave, dur, typ, voice="1", staff="1", chord=False, dot=False):
    return (
        f"<note>{'<chord/>' if chord else ''}<pitch><step>{step}</step><octave>{octave}</octave></pitch>"
        f"<duration>{dur}</duration><voice>{voice}</voice><type>{typ}</type>{'<dot/>' if dot else ''}"
        f"<staff>{staff}</staff></note>"
    )


def backup(d):
    return f"<backup><duration>{d}</duration></backup>"


def barline(location, style=None, repeat=None):
    s = f'<barline location="{location}">'
    if style:
        s += f"<bar-style>{style}</bar-style>"
    if repeat:
        s += f'<repeat direction="{repeat}"/>'
    return s + "</barline>"


ATTR = ("<attributes><divisions>2</divisions><key><fifths>0</fifths></key>"
        "<time><beats>{beats}</beats><beat-type>4</beat-type></time><staves>2</staves>"
        "<clef number=\"1\"><sign>G</sign><line>2</line></clef>"
        "<clef number=\"2\"><sign>F</sign><line>4</line></clef></attributes>")


def score(measures: list[str], beats=3, attrs: dict[int, str] | None = None,
          software="Audiveris", mattrs: dict[int, str] | None = None) -> bytes:
    body = []
    for i, m in enumerate(measures, 1):
        attr = ATTR.format(beats=beats) if i == 1 else (attrs or {}).get(i, "")
        extra = (mattrs or {}).get(i, "")
        body.append(f'<measure number="{i}"{extra}>{attr}{m}</measure>')
    ident = f"<identification><encoding><software>{software}</software></encoding></identification>"
    return (
        '<?xml version="1.0" encoding="UTF-8"?><score-partwise version="4.0">' + ident +
        '<part-list><score-part id="P1"><part-name>Piano</part-name></score-part></part-list>'
        f'<part id="P1">{"".join(body)}</part></score-partwise>'
    ).encode()


# A full 3/4 bar (divisions=2, so 6 divisions)
FULL = note("C", 5, 2, "quarter") + note("D", 5, 4, "half") + backup(6) + note("C", 3, 6, "half", staff="2", dot=True)
SHORT = (note("D", 5, 4, "half") + backup(4) + note("D", 3, 2, "quarter", voice="5", staff="2")
         + note("F", 3, 2, "quarter", voice="5", staff="2"))


def root_of(xml: bytes) -> ET.Element:
    return mx.parse(xml)


def sanitize(root) -> list[str]:
    mx.tag_measures(root)
    notes = mx.resolve_notes(mx.sanitize(root), [root], ["t"])
    mx.untag(root)
    return notes


def lengths(root) -> list[Fraction]:
    return heard_measure_lengths(mx.to_bytes(root))


def mei_measure(xml: bytes, n: int) -> str:
    verovio.enableLog(verovio.LOG_OFF)
    tk = verovio.toolkit()
    assert tk.loadData(xml.decode())
    return re.findall(rf'<measure[^>]*n="{n}".*?</measure>', tk.getMEI(), re.S)[0]


# ---------------------------------------------------------------- repair


def test_missed_rest_in_both_staves_is_padded_in_the_right_staff():
    root = root_of(score([FULL, SHORT, FULL]))
    assert lengths(root)[1] == 2
    rep = mx.repair(root, _heard_from_root)
    assert rep.padded == ["2"]
    assert lengths(root) == [3, 3, 3]
    mei = mei_measure(mx.to_bytes(root), 2)
    assert "staff=" not in re.sub(r"<staff n=", "", mei)  # no cross-staff rest
    assert mei.count("<rest") == 2


def test_no_repair_reports_but_does_not_pad():
    root = root_of(score([FULL, SHORT, FULL]))
    rep = mx.repair(root, _heard_from_root, apply=False)
    assert rep.padded == [] and rep.unfixed == ["2"]
    assert lengths(root)[1] == 2


def test_pickup_and_final_measures_are_left_alone():
    pickup = note("G", 4, 2, "quarter") + backup(2) + note("G", 3, 2, "quarter", staff="2")
    final = note("C", 5, 4, "half") + backup(4) + note("C", 3, 4, "half", staff="2")
    root = root_of(score([pickup, FULL, final], mattrs={1: ' implicit="yes"'}))
    rep = mx.repair(root, _heard_from_root)
    assert rep.padded == []


def test_short_first_measure_that_is_not_a_pickup_is_padded():
    root = root_of(score([SHORT, FULL, FULL]))
    rep = mx.repair(root, _heard_from_root)
    assert rep.padded == ["1"]


def test_measure_split_by_mid_bar_repeat_is_left_alone():
    a = note("C", 5, 2, "quarter") + backup(2) + note("C", 3, 2, "quarter", staff="2") \
        + barline("right", "light-heavy", "backward")
    b = note("D", 5, 4, "half") + backup(4) + note("D", 3, 4, "half", staff="2")
    rep = mx.repair(root_of(score([FULL, a, b, FULL])), _heard_from_root)
    assert rep.padded == []


def test_two_short_neighbours_without_a_barline_between_are_padded():
    a = note("C", 5, 2, "quarter") + backup(2) + note("C", 3, 2, "quarter", staff="2")
    b = note("D", 5, 4, "half") + backup(4) + note("D", 3, 4, "half", staff="2")
    rep = mx.repair(root_of(score([FULL, a, b, FULL])), _heard_from_root)
    assert rep.padded == ["2", "3"]


def test_short_bar_before_end_repeat_completing_the_pickup_is_left_alone():
    pickup = note("G", 4, 2, "quarter") + backup(2) + note("G", 3, 2, "quarter", staff="2")
    before_repeat = SHORT + barline("right", "light-heavy", "backward")
    root = root_of(score([pickup, FULL, before_repeat, FULL, FULL], mattrs={1: ' implicit="yes"'}))
    rep = mx.repair(root, _heard_from_root)
    assert rep.padded == []


def test_run_of_equally_short_bars_gets_its_missed_time_signature():
    root = root_of(score([FULL, SHORT, SHORT, SHORT, FULL]))
    rep = mx.repair(root, _heard_from_root)
    assert rep.padded == [] and rep.overfull == []
    assert any("time signature was probably missed" in n for n in rep.notes)
    times = [(m.get("number"), m.findtext("attributes/time/beats"))
             for m in root.iter("measure") if m.find("attributes/time") is not None]
    assert times == [("1", "3"), ("2", "2"), ("5", "3")]


def test_run_of_equally_short_bars_is_only_reported_without_repair():
    root = root_of(score([FULL, SHORT, SHORT, SHORT, FULL]))
    rep = mx.repair(root, _heard_from_root, apply=False)
    assert any("may have been misread" in n for n in rep.notes)
    assert len([m for m in root.iter("measure") if m.find("attributes/time") is not None]) == 1


# A full 3/4 bar as real files write it: the lower staff in voice 5
FULL5 = (note("C", 5, 2, "quarter") + note("D", 5, 4, "half") + backup(6)
         + note("C", 3, 6, "half", voice="5", staff="2", dot=True))


def test_parts_that_disagree_on_barlines_are_rebarred():
    # Part 2 missed the barline between bars 2 and 3, so its bar 2 is 6 beats
    # long; written the way OMR writes it (all of staff 1, back up, staff 2).
    long_bar = (note("C", 5, 2, "quarter") + note("D", 5, 4, "half")
                + note("C", 5, 2, "quarter") + note("D", 5, 4, "half") + backup(12)
                + note("C", 3, 6, "half", voice="5", staff="2", dot=True)
                + note("C", 3, 6, "half", voice="5", staff="2", dot=True))
    two_part = (
        '<?xml version="1.0" encoding="UTF-8"?><score-partwise version="4.0"><part-list>'
        '<score-part id="P1"><part-name>A</part-name></score-part>'
        '<score-part id="P2"><part-name>B</part-name></score-part></part-list>'
        '<part id="P1">'
        + "".join(f'<measure number="{i}">{ATTR.format(beats=3) if i == 1 else ""}{FULL5}</measure>'
                  for i in range(1, 5))
        + '</part><part id="P2">'
        + f'<measure number="1">{ATTR.format(beats=3)}{FULL5}</measure>'
        + f'<measure number="2">{long_bar}</measure>'
        + f'<measure number="4">{FULL5}</measure></part></score-partwise>')
    root = root_of(two_part.encode())
    assert lengths(root) != [3, 6, 3]  # misaligned: Verovio pairs bars by position
    notes = sanitize(root)
    counts = [len(p.findall("measure")) for p in root.findall("part")]
    assert counts[0] == counts[1] == 3
    assert any("disagreed about bar lines" in n for n in notes)
    assert lengths(root) == [3, 6, 3]
    r = render_musicxml(mx.to_bytes(root), "t")
    assert abs(r.duration_s - 4 * 1.5) < 0.01


def test_overfull_measure_is_reported_not_changed():
    over = note("C", 5, 8, "whole") + backup(8) + note("C", 3, 6, "half", staff="2", dot=True)
    rep = mx.repair(root_of(score([FULL, over, FULL])), _heard_from_root)
    assert rep.overfull == ["2"] and rep.padded == []


def test_time_signature_change_is_respected():
    four = note("C", 5, 8, "whole") + backup(8) + note("C", 3, 8, "whole", staff="2")
    change = {2: "<attributes><time><beats>4</beats><beat-type>4</beat-type></time></attributes>"}
    rep = mx.repair(root_of(score([FULL, four, four, four], attrs=change)), _heard_from_root)
    assert rep.padded == [] and rep.overfull == []


def test_heard_length_follows_note_type_not_duration():
    # <duration> says a quarter, <type> says half: Verovio plays a half note.
    lying = note("C", 5, 2, "half") + note("D", 5, 2, "quarter") + backup(4) + note("C", 3, 6, "half", staff="2", dot=True)
    root = root_of(score([FULL, lying, FULL]))
    assert lengths(root)[1] == 3


def test_missing_time_signature_is_reported():
    xml = score([FULL, FULL]).replace(b"<time><beats>3</beats><beat-type>4</beat-type></time>", b"")
    rep = mx.repair(root_of(xml), _heard_from_root)
    assert any("No time signature" in n for n in rep.notes)
    root = root_of(xml)
    assert mx.set_time(root, 3, 4)
    assert mx.repair(root, _heard_from_root).notes == []


def test_decompose_rest_values():
    assert mx._decompose(Fraction(3, 2)) == [(Fraction(3, 2), "quarter", True)]
    assert sum(q for q, _, _ in mx._decompose(Fraction(5, 2))) == Fraction(5, 2)
    assert mx._decompose(Fraction(1, 3)) == []  # not expressible: never pad a wrong amount


# ---------------------------------------------------------------- sanitize


def _octave(kind):
    return (f'<direction><direction-type><octave-shift type="{kind}" size="8" number="1"/>'
            "</direction-type><staff>1</staff></direction>")


def test_unterminated_octave_shift_from_audiveris_is_dropped_and_reported():
    root = root_of(score([FULL, _octave("down") + FULL, FULL]))
    notes = sanitize(root)
    assert root.find(".//octave-shift") is None
    assert any("8va" in n and "2" in n for n in notes)
    render_musicxml(mx.to_bytes(root), "t")  # would segfault with the open 8va line


def test_unterminated_octave_shift_from_other_software_is_closed():
    root = root_of(score([FULL, _octave("down") + FULL, FULL], software="MuseScore 4"))
    sanitize(root)
    assert [o.get("type") for o in root.iter("octave-shift")] == ["down"]  # kept by sanitize
    assert mx.close_octave_lines(root)
    kinds = [o.get("type") for o in root.iter("octave-shift")]
    assert kinds == ["down", "stop"]
    render_musicxml(mx.to_bytes(root), "t")


def test_repeat_barlines_are_drawn_so_verovio_plays_them():
    a = FULL + barline("right", "light-light", "backward")
    b = barline("left", "light-light", "forward") + FULL
    root = root_of(score([FULL, a, b, FULL + barline("right", "light-heavy", "backward")]))
    sanitize(root)
    styles = [bl.findtext("bar-style") for bl in root.iter("barline")]
    assert styles == ["light-heavy", "heavy-light", "light-heavy"]
    r = render_musicxml(mx.to_bytes(root), "t")
    assert abs(r.duration_s - 8 * 1.5) < 0.01  # A A B B = bars 1 2 1 2 3 4 3 4, 1.5 s each
    # every timemap id is drawn, so the viewer lights the repeated notes too
    svg_ids = set(re.findall(r'data-id="([^"]+)"', "".join(r.svgs)))
    assert all(i in svg_ids for e in r.timemap for i in e.get("on", []))


def test_implied_start_repeat():
    a = FULL + barline("right", "light-heavy", "backward")
    b = FULL + barline("right", "light-heavy", "backward")
    root = root_of(score([FULL, a, FULL, b]))
    notes = sanitize(root)
    assert any("start-repeat" in n for n in notes)
    r = render_musicxml(mx.to_bytes(root), "t")
    assert abs(r.duration_s - 8 * 1.5) < 0.01  # A A B B, 2 bars each


def test_empty_courtesy_measure_is_removed_and_numbers_shift():
    nxt_attr = "<attributes><key><fifths>1</fifths></key><time><beats>3</beats><beat-type>4</beat-type></time></attributes>"
    xml = score([FULL, "", FULL, FULL], attrs={3: nxt_attr})
    xml = xml.replace(b'<measure number="3">', b'<measure number="3"><print new-system="yes"/>')
    root = root_of(xml)
    notes = sanitize(root)
    assert [m.get("number") for m in root.iter("measure")] == ["1", "2", "3"]
    assert any("empty measure" in n for n in notes)


def test_tempo_from_words():
    d = '<direction placement="above"><direction-type><words>= 132</words></direction-type></direction>'
    root = root_of(score([d + FULL, FULL]))
    notes = sanitize(root)
    assert root.find(".//sound").get("tempo") == "132"
    assert notes and "132" in notes[0]
    fingering = '<direction><direction-type><words>Op. 100</words></direction-type></direction>'
    root = root_of(score([fingering + FULL, FULL]))
    sanitize(root)
    assert root.find(".//sound") is None


def test_da_capo_al_fine_is_played():
    fine = FULL + barline("right", "light-heavy")
    dc = ('<direction><direction-type><words>D.C. al Fine</words></direction-type></direction>'
          + FULL + barline("right", "light-heavy"))
    root = root_of(score([FULL, fine, FULL, dc]))
    notes = sanitize(root)
    assert any("D.C." in n for n in notes)
    r = render_musicxml(mx.to_bytes(root), "t")
    assert abs(r.duration_s - 6 * 1.5) < 0.01  # 4 bars, then back to bar 1 until the Fine at bar 2


def test_different_keys_on_the_two_staves_are_reported():
    xml = score([FULL, FULL]).replace(b"<key><fifths>0</fifths></key>",
                                      b'<key number="1"><fifths>1</fifths></key><key number="2"><fifths>0</fifths></key>')
    assert any("different keys" in n for n in sanitize(root_of(xml)))


def test_split_movements_at_final_barline_with_new_time_signature():
    new_piece = "<attributes><time><beats>3</beats><beat-type>4</beat-type></time></attributes>"
    xml = score([FULL, FULL + barline("right", "light-heavy"), FULL, FULL], attrs={3: new_piece})
    xml = xml.replace(b'<measure number="3">', b'<measure number="3"><print new-system="yes"/>')
    pieces = mx.split_movements(root_of(xml))
    assert len(pieces) == 2
    second = pieces[1].find("part")
    assert [m.get("number") for m in second.findall("measure")] == ["1", "2"]
    assert second.find("measure/attributes/divisions").text == "2"  # state carried over
    assert second.find("measure/attributes/clef") is not None


def test_utf16_musicxml_is_read():
    xml = score([FULL]).decode().replace('encoding="UTF-8"', 'encoding="UTF-16"').encode("utf-16")
    assert mx.parse(xml).tag == "score-partwise"


# ---------------------------------------------------------------- render / MIDI


def _midi(notes, bpm=120) -> bytes:
    mf = mido.MidiFile(ticks_per_beat=480)
    tr = mido.MidiTrack()
    mf.tracks.append(tr)
    tr.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(bpm)))
    evs = []
    for on, off, p in notes:
        evs += [(on, 1, mido.Message("note_on", note=p, velocity=80)),
                (off, 0, mido.Message("note_off", note=p, velocity=0))]
    now = 0
    for beat, _, m in sorted(evs, key=lambda e: (e[0], e[1])):
        tick = int(beat * 480)
        tr.append(m.copy(time=tick - now))
        now = tick
    buf = io.BytesIO()
    mf.save(file=buf)
    return buf.getvalue()


def _onsets(data: bytes) -> list[tuple[float, int]]:
    t, out = 0.0, []
    for m in mido.MidiFile(file=io.BytesIO(data)):
        t += m.time
        if m.type == "note_on" and m.velocity > 0:
            out.append((round(t, 4), m.note))
    return out


def test_combine_midi_offsets_tempo_and_factor():
    a = _midi([(0, 1, 60), (1, 2, 62)], bpm=60)  # 1 s per beat
    b = _midi([(0, 1, 64)], bpm=120)
    assert _onsets(combine_midi([(a, 0.0, 1.0), (b, 4.0, 1.0)])) == [(0.0, 60), (1.0, 62), (4.0, 64)]
    assert _onsets(combine_midi([(a, 0.0, 2.0)])) == [(0.0, 60), (0.5, 62)]


def test_combine_midi_puts_note_off_before_repeated_note_on():
    a = _midi([(0, 1, 60), (1, 2, 60)])
    msgs = [m for m in mido.MidiFile(file=io.BytesIO(combine_midi([(a, 0.0, 1.0)])))
            if m.type.startswith("note")]
    assert [m.velocity > 0 for m in msgs] == [True, False, True, False]


def test_tempo_beyond_verovio_range_is_applied():
    xml = mx.to_bytes(root_of(score([FULL, FULL])))
    r = render_musicxml(xml, "t", bpm=600)  # factor 5; Verovio alone would ignore it
    assert abs(r.duration_s - 3.0 / 5) < 0.002  # Verovio ends notes one tick early
    tm_on = sorted({round(e["tstamp"]) for e in r.timemap if e.get("on")})
    assert tm_on[-1] == round(4 * 500 / 5)


def test_render_timemap_matches_midi():
    xml = mx.to_bytes(root_of(score([FULL, FULL])))
    r = render_musicxml(xml, "t", bpm=90)
    midi = combine_midi([(r.midi, 0.0, r.tempo_factor)])
    midi_on = sorted({round(t * 1000) for t, _ in _onsets(midi)})
    tm_on = sorted({round(e["tstamp"]) for e in r.timemap if e.get("on")})
    assert midi_on == tm_on
    assert abs(r.bpm - 90) < 1e-6


def test_layouts_share_note_ids():
    r = render_musicxml(mx.to_bytes(root_of(score([FULL] * 12))), "t", video=True)
    ids = [set(re.findall(r'data-id="([^"]+)"', "".join(s))) for s in (r.svgs, r.svgs_narrow, r.svgs_video)]
    on = {i for e in r.timemap for i in e.get("on", [])}
    assert all(on <= s for s in ids)


def test_rend_ids_are_mapped_back():
    tm = [{"tstamp": 0, "on": ["a-rend2", "b"], "measureOn": "m-rend3"}]
    assert _normalize_ids(tm, {"a", "b", "m"})[0] == {"tstamp": 0, "on": ["a", "b"], "measureOn": "m"}


# ---------------------------------------------------------------- review regressions


def test_start_repeat_survives_dropping_an_empty_courtesy_measure():
    nxt_attr = "<attributes><key><fifths>1</fifths></key></attributes>"
    a = FULL + barline("right", "light-heavy", "backward")
    b = FULL + barline("right", "light-heavy", "backward")
    xml = score([FULL, a, "", FULL, b], attrs={4: nxt_attr})
    xml = xml.replace(b'<measure number="4">', b'<measure number="4"><print new-system="yes"/>')
    root = root_of(xml)
    sanitize(root)
    r = render_musicxml(mx.to_bytes(root), "t")
    assert abs(r.duration_s - 8 * 1.5) < 0.01  # A A B B


def test_expression_al_fine_is_not_a_da_capo():
    dim = '<direction><direction-type><words>dim. al fine</words></direction-type></direction>'
    root = root_of(score([FULL, dim + FULL, FULL]))
    assert not any("D.C." in n for n in sanitize(root))
    assert root.find(".//sound") is None


def test_bare_dc_does_not_stop_at_an_end_repeat():
    a = FULL + barline("right", "light-heavy", "backward")
    dc = '<direction><direction-type><words>D.C.</words></direction-type></direction>' + FULL
    root = root_of(score([a, FULL, dc]))
    sanitize(root)
    assert not any(s.get("fine") for s in root.iter("sound"))


def test_split_keeps_da_capo_and_its_fine_together():
    new_meter = "<attributes><time><beats>2</beats><beat-type>4</beat-type></time></attributes>"
    fine = '<direction><direction-type><words>Fine</words></direction-type></direction>'
    dc = '<direction><direction-type><words>D.C. al Fine</words></direction-type></direction>'
    two = note("C", 5, 4, "half") + backup(4) + note("C", 3, 4, "half", staff="2")
    xml = score([FULL, fine + FULL + barline("right", "light-heavy"), two, dc + two],
                attrs={3: new_meter})
    xml = xml.replace(b'<measure number="3">', b'<measure number="3"><print new-system="yes"/>')
    root = root_of(xml)
    sanitize(root)
    assert len(mx.split_movements(root)) == 1


def test_split_does_not_carry_measure_style():
    rest_style = ("<attributes><measure-style><multiple-rest>2</multiple-rest></measure-style>"
                  "</attributes>")
    new_piece = "<attributes><time><beats>3</beats><beat-type>4</beat-type></time></attributes>"
    xml = score([FULL, FULL, FULL + barline("right", "light-heavy"), FULL, FULL],
                attrs={2: rest_style, 4: new_piece})
    xml = xml.replace(b'<measure number="4">', b'<measure number="4"><print new-system="yes"/>')
    pieces = mx.split_movements(root_of(xml))
    assert len(pieces) == 2 and pieces[1].find(".//measure-style") is None


def test_octave_lines_closed_per_piece_after_split():
    new_piece = "<attributes><time><beats>3</beats><beat-type>4</beat-type></time></attributes>"
    xml = score([FULL, _octave("down") + FULL, FULL + barline("right", "light-heavy"), FULL, FULL],
                attrs={4: new_piece}, software="MuseScore 4")
    xml = xml.replace(b'<measure number="4">', b'<measure number="4"><print new-system="yes"/>')
    root = root_of(xml)
    sanitize(root)
    pieces = mx.split_movements(root)
    for p in pieces:
        mx.close_octave_lines(p)
        render_musicxml(mx.to_bytes(p), "t")  # would crash with an open line


def test_tempo_words_catalogue_numbers_and_dotted_beats():
    def tempo_of(words, beats=3):
        d = f'<direction><direction-type><words>{words}</words></direction-type></direction>'
        root = root_of(score([d + FULL, FULL], beats=beats))
        sanitize(root)
        s = root.find(".//sound")
        return None if s is None else s.get("tempo")
    assert tempo_of("K. 283") is None
    assert tempo_of("L. 33") is None
    assert tempo_of("#64") is None
    assert tempo_of("= 132") == "132"
    assert tempo_of("♩. = 60") == "90"


def test_implausible_tempo_mark_is_ignored():
    d = ('<direction><direction-type><metronome><beat-unit>quarter</beat-unit>'
         '<per-minute>1oo</per-minute></metronome></direction-type><sound tempo="1"/></direction>')
    root = root_of(score([d + FULL, FULL]))
    notes = sanitize(root)
    assert any("misread" in n for n in notes)
    r = render_musicxml(mx.to_bytes(root), "t")
    assert r.base_tempo == 120 and not r.warnings


def test_three_note_tie_chain_sounds_to_the_end():
    def tied(step, types):
        t = "".join(f'<tie type="{x}"/>' for x in types)
        n = "".join(f'<tied type="{x}"/>' for x in types)
        return (f"<note><pitch><step>{step}</step><octave>5</octave></pitch><duration>6</duration>{t}"
                f"<voice>1</voice><type>half</type><dot/><staff>1</staff><notations>{n}</notations></note>"
                + backup(6) + note("C", 3, 6, "half", staff="2", dot=True))
    root = root_of(score([tied("C", ["start"]), tied("C", ["start", "stop"]), tied("C", ["stop"])]))
    sanitize(root)
    r = render_musicxml(mx.to_bytes(root), "t")
    midi = combine_midi([(r.midi, 0.0, 1.0)])
    t, on, off = 0.0, None, None
    for m in mido.MidiFile(file=io.BytesIO(midi)):
        t += m.time
        if m.type == "note_on" and m.velocity and m.note == 72:
            on = t
        elif m.type in ("note_off", "note_on") and m.note == 72 and not getattr(m, "velocity", 0):
            off = t
    assert on == 0 and off > 4.4  # held through all three bars (4.5 s)


def test_unison_notes_in_two_voices_both_sound():
    a = _midi([(0, 4, 72)])
    b = _midi([(0, 1, 72)])
    merged = mido.MidiFile(ticks_per_beat=480)
    for data in (a, b):
        merged.tracks += mido.MidiFile(file=io.BytesIO(data)).tracks
    buf = io.BytesIO()
    merged.save(file=buf)
    out = combine_midi([(buf.getvalue(), 0.0, 1.0)])
    t, offs = 0.0, []
    for m in mido.MidiFile(file=io.BytesIO(out)):
        t += m.time
        if m.type == "note_off" or (m.type == "note_on" and m.velocity == 0):
            offs.append(round(t, 3))
    # struck twice at 0 (released and re-struck), never released at 0.5 s where the
    # short note ends, released when the long note ends (4 beats at 120 BPM)
    assert 0.5 not in offs and offs[-1] == 2.0


def test_pedal_keeps_the_last_chord_ringing():
    from sheet2audio.render import _midi_extent
    mf = mido.MidiFile(ticks_per_beat=480)
    tr = mido.MidiTrack()
    mf.tracks.append(tr)
    tr += [mido.Message("control_change", control=64, value=127),
           mido.Message("note_on", note=60, velocity=80),
           mido.Message("note_off", note=60, time=480),
           mido.Message("control_change", control=64, value=0, time=4 * 480)]
    buf = io.BytesIO()
    mf.save(file=buf)
    last_off, ring, n = _midi_extent(buf.getvalue())
    assert n == 1 and abs(last_off - 0.5) < 1e-6 and abs(ring - 2.5) < 1e-6


# ---------------------------------------------------------------- choral parts


def _satb_score(extra_part: str = "", extra_list: str = "") -> bytes:
    """An original 3-bar SATB piece: S+A on one treble staff (two-note chords,
    one unison), T+B on one bass staff, and a piano part on two staves."""
    def v(step, octv, dur, typ, chord=False, staff="1", voice="1", lyric=None):
        ly = f"<lyric><text>{lyric}</text></lyric>" if lyric else ""
        return note(step, octv, dur, typ, voice=voice, staff=staff, chord=chord,
                    dot=dur == 6).replace("</note>", ly + "</note>")
    vocal_attr = lambda sign, line: (f'<attributes><divisions>2</divisions><key><fifths>0</fifths></key>'
                                     f'<time><beats>3</beats><beat-type>4</beat-type></time>'
                                     f'<clef><sign>{sign}</sign><line>{line}</line></clef></attributes>')
    sa = [v("E", 5, 2, "quarter", lyric="la") + v("C", 5, 2, "quarter", chord=True)
          + v("D", 5, 2, "quarter") + v("B", 4, 2, "quarter", chord=True)
          + v("C", 5, 2, "quarter"),  # unison C5
          v("G", 5, 6, "half") + v("E", 5, 6, "half", chord=True),
          v("F", 5, 6, "half") + v("D", 5, 6, "half", chord=True)]
    tb = [v("G", 3, 2, "quarter", lyric="la") + v("C", 3, 2, "quarter", chord=True)
          + v("G", 3, 2, "quarter") + v("G", 2, 2, "quarter", chord=True)
          + v("E", 3, 2, "quarter"),  # unison E3
          v("C", 4, 6, "half") + v("C", 3, 6, "half", chord=True),
          v("B", 3, 6, "half") + v("G", 2, 6, "half", chord=True)]
    piano = [note("C", 5, 6, "half", dot=True) + backup(6) + note("C", 3, 6, "half", voice="5", staff="2", dot=True)] * 3
    def part(pid, bars, attr):
        return (f'<part id="{pid}">' + "".join(
            f'<measure number="{i}">{attr if i == 1 else ""}{b}</measure>' for i, b in enumerate(bars, 1))
            + "</part>")
    piano_attr = ATTR.format(beats=3)
    return (
        '<?xml version="1.0" encoding="UTF-8"?><score-partwise version="4.0"><part-list>'
        '<score-part id="P1"><part-name>Voice</part-name></score-part>'
        '<score-part id="P2"><part-name>Voice</part-name></score-part>'
        f'{extra_list}<score-part id="P3"><part-name>Piano</part-name></score-part></part-list>'
        + part("P1", sa, vocal_attr("G", 2)) + part("P2", tb, vocal_attr("F", 4)) + extra_part
        + part("P3", piano, piano_attr) + "</score-partwise>").encode()


def _pitches(midi: bytes) -> list[int]:
    return [m.note for m in mido.MidiFile(file=io.BytesIO(midi)) if m.type == "note_on" and m.velocity]


def test_satb_on_two_staves_is_split_into_four_voices_and_piano():
    r = render_musicxml(_satb_score(), "t", parts_names=None)
    names = [(t.name, t.kind) for t in r.tracks]
    assert names == [("Soprano", "voice"), ("Alto", "voice"), ("Tenor", "voice"),
                     ("Bass", "voice"), ("Piano", "accompaniment")]
    by = {t.name: t for t in r.tracks}
    assert _pitches(by["Soprano"].midi) == [76, 74, 72, 79, 77]
    assert _pitches(by["Alto"].midi) == [72, 71, 72, 76, 74]  # the unison C5 is sung by both
    assert _pitches(by["Tenor"].midi) == [55, 55, 52, 60, 59]
    assert _pitches(by["Bass"].midi) == [48, 43, 52, 48, 43]
    # every part keeps the whole score's time line
    ends = {t.name: _midi_extent_s(t.midi) for t in r.tracks}
    assert max(ends.values()) - min(ends.values()) < 0.01
    assert b"Soprano/Alto" in r.xml and b"Tenor/Bass" in r.xml  # staves renamed


def _midi_extent_s(midi: bytes) -> float:
    t = last = 0.0
    for m in mido.MidiFile(file=io.BytesIO(midi)):
        t += m.time
        if m.type in ("note_on", "note_off"):
            last = t
    return last


def test_part_names_can_be_given():
    r = render_musicxml(_satb_score(), "t", parts_names=["Women", "Men", "Organ"])
    assert [t.name for t in r.tracks] == ["Soprano", "Alto", "Tenor", "Bass", "Organ"]
    r = render_musicxml(_satb_score(), "t", parts_names=["Upper/Lower", "", ""])
    assert [t.name for t in r.tracks][:2] == ["Upper", "Lower"]


def test_mix_tracks_puts_each_part_on_its_own_channel():
    from sheet2audio.render import mix_tracks, track_notes
    r = render_musicxml(_satb_score(), "t", parts_names=None)
    out = mido.MidiFile(file=io.BytesIO(mix_tracks([(r, 0.0)], {"Piano": 0}, {"Alto": 0.0})))
    chans = {m.channel for m in out if m.type == "note_on"}
    assert len(chans) == 4 and 9 not in chans  # Alto left out; drum channel never used
    notes = track_notes([(r, 1.0)])
    assert notes["Soprano"][0][0] == 1000.0 and notes["Soprano"][0][2] == 76


def test_piano_piece_gets_right_and_left_hand_parts():
    r = render_musicxml(score([FULL5, FULL5]), "t", parts_names=None)
    assert [t.name for t in r.tracks] == ["Right hand", "Left hand"]


def test_music_read_into_the_wrong_voice_part_is_moved_back():
    # Three one-staff voice parts; on the last line only the top voice sings,
    # but OMR put its notes into the bass-clef part (with a treble clef).
    def vpart(pid, sign, line, bars):
        attr = (f'<attributes><divisions>2</divisions><time><beats>3</beats><beat-type>4</beat-type></time>'
                f'<clef><sign>{sign}</sign><line>{line}</line></clef></attributes>')
        return f'<part id="{pid}">' + "".join(
            f'<measure number="{i}">{attr if i == 1 else ""}{b}</measure>' for i, b in enumerate(bars, 1)) + "</part>"
    mel = note("E", 5, 6, "half", dot=True)
    low = note("C", 3, 6, "half", dot=True)
    rest = '<note><rest/><duration>6</duration><voice>1</voice><type>half</type><dot/><staff>1</staff></note>'
    newline = '<print new-system="yes"/>'
    moved = '<attributes><clef><sign>G</sign><line>2</line></clef></attributes>' + mel
    xml = ('<?xml version="1.0" encoding="UTF-8"?><score-partwise version="4.0"><part-list>'
           + "".join(f'<score-part id="P{k}"><part-name>Voice</part-name></score-part>' for k in (1, 2, 3))
           + '</part-list>'
           + vpart("P1", "G", 2, [mel, mel, rest, rest])
           + vpart("P2", "G", 2, [mel, mel, rest, rest])
           + vpart("P3", "F", 4, [low, low, moved, mel]) + '</score-partwise>')
    xml = xml.replace('<measure number="3">', f'<measure number="3">{newline}')
    root = root_of(xml.encode())
    notes = sanitize(root)
    assert any("moved back" in n for n in notes)
    p1, p3 = root.findall("part")[0], root.findall("part")[2]
    assert p1.findall("measure")[2].find("note/pitch") is not None
    assert p3.findall("measure")[2].find("note/rest") is not None
