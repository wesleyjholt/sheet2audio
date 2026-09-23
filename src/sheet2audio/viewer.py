"""Write the self-contained HTML play-along page."""

from __future__ import annotations

import base64
import html
import json
import re
import urllib.parse
from dataclasses import dataclass, field
from importlib.resources import files
from pathlib import Path

from .render import Rendered, track_names, track_notes

MIME = {".mp3": "audio/mpeg", ".ogg": "audio/ogg", ".wav": "audio/wav", ".flac": "audio/flac",
        ".m4a": "audio/mp4"}


@dataclass
class Player:
    """What the page's audio engine needs."""

    samples: Path  # the sample sheet (MP3)
    layout: dict  # {program: {pitch: [start, length, loop start?, loop end?]}}
    silent: Path  # half a second of silence (lets iPhones play with the silent switch on)
    voice_program: int  # 52 choir or 0 piano, for voice parts
    programs: dict[str, int]  # part name -> program used for non-voice parts
    downloads: list[tuple[str, str]] = field(default_factory=list)  # (label, file name)


def _src(path: Path, embed: bool) -> str:
    if embed:
        mime = MIME.get(path.suffix.lower(), "application/octet-stream")
        return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii")
    return urllib.parse.quote(path.name)


def write_viewer(
    dest: Path,
    title: str,
    movements: list[tuple[Rendered, float]],  # (rendered movement, offset in seconds)
    player: Player,
    notes: list[str],
    duration_s: float,
    embed_audio: bool = True,
) -> None:
    template = files("sheet2audio").joinpath("viewer_template.html").read_text(encoding="utf-8")
    names = track_names([r for r, _ in movements])
    kinds = {t.name: t.kind for r, _ in movements for t in r.tracks}
    per_track = track_notes(movements)
    id_tracks: dict[str, list[int]] = {}
    for mi, (r, _) in enumerate(movements):
        for t in r.tracks:
            k = names.index(t.name)
            for i in t.ids:
                id_tracks.setdefault(f"{mi}:{i}", []).append(k)
    data = {
        "title": title,
        "notes": notes,
        "duration_ms": round(duration_s * 1000),
        "movements": [
            {
                "title": r.title,
                "svgs": r.svgs,
                "svgs_narrow": r.svgs_narrow,
                "timemap": [
                    {k: e[k] for k in ("tstamp", "on", "off", "measureOn") if k in e}
                    for e in r.timemap
                ],
                "offset_ms": round(offset * 1000, 3),
            }
            for r, offset in movements
        ],
        "tracks": [
            {"name": n, "kind": kinds[n], "program": player.programs.get(n, 0),
             "notes": per_track.get(n, [])}
            for n in names
        ],
        "id_tracks": id_tracks,
        "voice_program": player.voice_program,
        "samples": {"src": _src(player.samples, embed_audio), "layout": player.layout},
        "silent_src": _src(player.silent, True),
        "downloads": [{"label": label, "href": urllib.parse.quote(name)}
                      for label, name in player.downloads],
    }
    # `</` inside a <script> block would end it early; `<\/` is the same JSON string.
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    script = ""
    if not embed_audio:
        # The sounds live in '<name>.js' next to the page: a script tag loads
        # it from disk as well as over the network (fetch() cannot read
        # file:// pages' neighbours).
        js = player.samples.with_suffix(".js")
        js.write_text("window.__sheet2audioSamples = " + json.dumps(_src(player.samples, True))
                      + ";\n", encoding="utf-8")
        script = f'<script src="{html.escape(urllib.parse.quote(js.name), quote=True)}"></script>'
    values = {"TITLE": html.escape(title), "DATA": payload, "SAMPLES_SCRIPT": script}
    page = re.sub(r"__(TITLE|DATA|SAMPLES_SCRIPT)__", lambda m: values[m.group(1)], template)
    dest.write_text(page, encoding="utf-8")
