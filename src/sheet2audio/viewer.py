"""Write the self-contained HTML play-along page."""

from __future__ import annotations

import base64
import html
import json
import re
from importlib.resources import files
from pathlib import Path

from .render import Rendered

MIME = {".mp3": "audio/mpeg", ".ogg": "audio/ogg", ".wav": "audio/wav", ".flac": "audio/flac"}


def write_viewer(
    dest: Path,
    title: str,
    movements: list[tuple[Rendered, float]],  # (rendered movement, offset in seconds)
    audio: Path,
    notes: list[str],
    embed_audio: bool = True,
) -> None:
    template = files("sheet2audio").joinpath("viewer_template.html").read_text(encoding="utf-8")
    data = {
        "title": title,
        "notes": notes,
        "movements": [
            {
                "title": r.title,
                "svgs": r.svgs,
                "timemap": [
                    {k: e[k] for k in ("tstamp", "on", "off", "measureOn") if k in e}
                    for e in r.timemap
                ],
                "offset_ms": round(offset * 1000, 3),
            }
            for r, offset in movements
        ],
    }
    # `</` inside a <script> block would end it early; `<\/` is the same JSON string.
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    if embed_audio:
        mime = MIME.get(audio.suffix.lower(), "application/octet-stream")
        src = f"data:{mime};base64," + base64.b64encode(audio.read_bytes()).decode("ascii")
    else:
        src = html.escape(audio.name, quote=True)
    values = {"TITLE": html.escape(title), "AUDIO_SRC": src, "DATA": payload}
    page = re.sub(r"__(TITLE|AUDIO_SRC|DATA)__", lambda m: values[m.group(1)], template)
    dest.write_text(page, encoding="utf-8")
