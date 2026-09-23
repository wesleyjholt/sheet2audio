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


def test_run_of_equally_short_bars_is_reported_as_meter_problem():
    root = root_of(score([FULL, SHORT, SHORT, SHORT, FULL]))
    rep = mx.repair(root, _heard_from_root)
    assert rep.padded == []
    assert any("time signature may have been misread" in n for n in rep.notes)


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
    notes = mx.sanitize(root)
    assert root.find(".//octave-shift") is None
    assert any("8va" in n and "2" in n for n in notes)
    render_musicxml(mx.to_bytes(root), "t")  # would segfault with the open 8va line


def test_unterminated_octave_shift_from_other_software_is_closed():
    root = root_of(score([FULL, _octave("down") + FULL, FULL], software="MuseScore 4"))
    mx.sanitize(root)
    kinds = [o.get("type") for o in root.iter("octave-shift")]
    assert kinds == ["down", "stop"]
    render_musicxml(mx.to_bytes(root), "t")


def test_repeat_barlines_are_drawn_so_verovio_plays_them():
    a = FULL + barline("right", "light-light", "backward")
    b = barline("left", "light-light", "forward") + FULL
    root = root_of(score([FULL, a, b, FULL + barline("right", "light-heavy", "backward")]))
    mx.sanitize(root)
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
    notes = mx.sanitize(root)
    assert any("start-repeat" in n for n in notes)
    r = render_musicxml(mx.to_bytes(root), "t")
    assert abs(r.duration_s - 8 * 1.5) < 0.01  # A A B B, 2 bars each


def test_empty_courtesy_measure_is_removed_and_numbers_shift():
    nxt_attr = "<attributes><key><fifths>1</fifths></key><time><beats>3</beats><beat-type>4</beat-type></time></attributes>"
    xml = score([FULL, "", FULL, FULL], attrs={3: nxt_attr})
    xml = xml.replace(b'<measure number="3">', b'<measure number="3"><print new-system="yes"/>')
    root = root_of(xml)
    notes = mx.sanitize(root)
    assert [m.get("number") for m in root.iter("measure")] == ["1", "2", "3"]
    assert any("empty measure" in n for n in notes)


def test_tempo_from_words():
    d = '<direction placement="above"><direction-type><words>= 132</words></direction-type></direction>'
    root = root_of(score([d + FULL, FULL]))
    notes = mx.sanitize(root)
    assert root.find(".//sound").get("tempo") == "132"
    assert notes and "132" in notes[0]
    fingering = '<direction><direction-type><words>Op. 100</words></direction-type></direction>'
    root = root_of(score([fingering + FULL, FULL]))
    mx.sanitize(root)
    assert root.find(".//sound") is None


def test_da_capo_al_fine_is_played():
    fine = FULL + barline("right", "light-heavy")
    dc = ('<direction><direction-type><words>D.C. al Fine</words></direction-type></direction>'
          + FULL + barline("right", "light-heavy"))
    root = root_of(score([FULL, fine, FULL, dc]))
    notes = mx.sanitize(root)
    assert any("D.C." in n for n in notes)
    r = render_musicxml(mx.to_bytes(root), "t")
    assert abs(r.duration_s - 6 * 1.5) < 0.01  # 4 bars, then back to bar 1 until the Fine at bar 2


def test_different_keys_on_the_two_staves_are_reported():
    xml = score([FULL, FULL]).replace(b"<key><fifths>0</fifths></key>",
                                      b'<key number="1"><fifths>1</fifths></key><key number="2"><fifths>0</fifths></key>')
    assert any("different keys" in n for n in mx.sanitize(root_of(xml)))


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
