"""Score-following MP4 video: the engraved score, a screen at a time, with the
notes lit up as they sound. H.264 + AAC plays in the iPhone Photos and Files
apps (and any browser or video player) with no extra software.

Frames are Verovio SVG pages laid out for a 16:9 screen and rasterized with
librsvg (LGPL-2.1+); FFmpeg stitches them to the audio.
"""

from __future__ import annotations

import base64
import io
import os
import re
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import verovio


HIGHLIGHT = "#e4572e"
FPS = 30


class VideoError(RuntimeError):
    pass


@dataclass
class VideoMovement:
    pages: list[str]  # 16:9 SVG screens (render.Rendered.svgs_video)
    timemap: list[dict]  # tstamp in ms at the final tempo (render.Rendered.timemap)
    offset_s: float  # where the movement starts in the audio


def _cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    d = Path(base) / "sheet2audio"
    d.mkdir(parents=True, exist_ok=True)
    return d


def music_font_env() -> dict[str, str]:
    """Environment that lets librsvg draw Verovio's SMuFL text glyphs.

    Verovio embeds its music font (Leipzig, SIL OFL) in the SVG as WOFF2, which
    librsvg ignores; metronome marks and dynamics would render as boxes. We
    unpack the font once into a cache folder and point fontconfig at it.
    """
    cache = _cache_dir()
    fonts = cache / "fonts"
    font = fonts / "Leipzig.otf"
    if not font.is_file():
        from fontTools.ttLib import TTFont

        css = Path(verovio.__file__).with_name("data").joinpath("Leipzig.css").read_text()
        m = re.search(r"base64,([A-Za-z0-9+/=]+)\)", css)
        if not m:
            raise VideoError("Could not find the embedded Leipzig font in Verovio's data.")
        fonts.mkdir(parents=True, exist_ok=True)
        tt = TTFont(io.BytesIO(base64.b64decode(m.group(1))))
        tt.flavor = None
        tmp = font.with_suffix(".tmp")
        tt.save(tmp)
        tmp.replace(font)
    conf = cache / "fonts.conf"
    system_confs = [
        p for p in ("/opt/homebrew/etc/fonts/fonts.conf", "/usr/local/etc/fonts/fonts.conf",
                    "/etc/fonts/fonts.conf") if Path(p).is_file()
    ]
    includes = "".join(f'<include ignore_missing="yes">{p}</include>' for p in system_confs[:1])
    text = (
        '<?xml version="1.0"?><!DOCTYPE fontconfig SYSTEM "fonts.dtd"><fontconfig>'
        f"{includes}<dir>{fonts}</dir><cachedir>{cache / 'fccache'}</cachedir></fontconfig>"
    )
    if not conf.is_file() or conf.read_text() != text:
        conf.write_text(text)
    env = dict(os.environ)
    env["FONTCONFIG_FILE"] = str(conf)
    env["PANGOCAIRO_BACKEND"] = "fc"  # use fontconfig (not CoreText) so our font is found
    return env


_DATA_ID = re.compile(r'data-id="([^"]+)"')


@dataclass
class _Frame:
    start: float
    page: int
    active: frozenset[str]


def plan_frames(movements: list[VideoMovement]) -> tuple[list[str], list[_Frame]]:
    """List the (screen, lit notes) state over time for all movements."""
    pages: list[str] = []
    frames: list[_Frame] = []
    for k, mv in enumerate(movements):
        mv_pages, timemap = mv.pages, mv.timemap
        # A movement's trailing events (e.g. the end of a final rest) can fall
        # after the next movement has started; they must not flip the screen back.
        until = movements[k + 1].offset_s if k + 1 < len(movements) else float("inf")
        first = len(pages)
        page_of: dict[str, int] = {}
        for i, svg in enumerate(mv_pages):
            for ident in _DATA_ID.findall(svg):
                page_of.setdefault(ident, first + i)
        pages.extend(mv_pages)
        active: set[str] = set()
        page = first
        frames.append(_Frame(mv.offset_s, page, frozenset()))
        for e in sorted(timemap, key=lambda e: e["tstamp"]):
            active.difference_update(e.get("off", []))
            active.update(e.get("on", []))
            if "measureOn" in e and e["measureOn"] in page_of:
                page = page_of[e["measureOn"]]
            elif e.get("on"):
                page = page_of.get(e["on"][0], page)
            start = mv.offset_s + e["tstamp"] / 1000.0
            if start < until:
                frames.append(_Frame(start, page, frozenset(active)))
    # Merge frames that start together or show the same thing.
    merged: list[_Frame] = []
    for f in sorted(frames, key=lambda f: f.start):
        if merged and abs(f.start - merged[-1].start) < 1e-6:
            merged[-1] = f
        elif merged and (f.page, f.active) == (merged[-1].page, merged[-1].active):
            continue
        else:
            merged.append(f)
    return pages, merged


def _highlight(svg: str, ids: frozenset[str]) -> str:
    if not ids:
        return svg
    sel = ",".join(f'[data-id="{i}"]' for i in sorted(ids))
    style = f"<style>{sel}{{fill:{HIGHLIGHT};color:{HIGHLIGHT}}}</style>"
    return svg.replace("</defs>", "</defs>" + style, 1) if "</defs>" in svg else re.sub(
        r"(<svg[^>]*>)", r"\1" + style, svg, count=1)


def render_video(
    movements: list[VideoMovement],
    audio_wav: Path,
    audio_filter: str,
    dest: Path,
    ffmpeg: Path,
    rsvg: Path,
    end_s: float,
    width: int = 1920,
    height: int = 1080,
    jobs: int | None = None,
) -> int:
    """Write `dest` (MP4) with the audio cut at `end_s`. Returns the number of
    distinct frames drawn."""
    pages, frames = plan_frames(movements)
    if not pages:
        raise VideoError("Nothing to draw: the score has no pages.")
    env = music_font_env()
    with tempfile.TemporaryDirectory(prefix="sheet2audio-video-") as tmp:
        tmp = Path(tmp)
        states = sorted({(f.page, f.active) for f in frames}, key=lambda s: (s[0], sorted(s[1])))
        png_of: dict[tuple[int, frozenset[str]], Path] = {}

        def rasterize(k: int, state: tuple[int, frozenset[str]]) -> None:
            page, active = state
            svg_path = tmp / f"s{k:06d}.svg"
            png_path = tmp / f"s{k:06d}.png"
            svg_path.write_text(_highlight(pages[page], active), encoding="utf-8")
            proc = subprocess.run(
                [str(rsvg), "-w", str(width), "-h", str(height), "-b", "white",
                 "-o", str(png_path), str(svg_path)],
                capture_output=True, text=True, env=env,
            )
            if proc.returncode != 0 or not png_path.is_file():
                raise VideoError(f"rsvg-convert failed: {proc.stderr.strip()[:500]}")
            svg_path.unlink()
            png_of[state] = png_path

        with ThreadPoolExecutor(max_workers=jobs or max(1, (os.cpu_count() or 2) - 1)) as pool:
            list(pool.map(lambda ks: rasterize(*ks), enumerate(states)))

        # ffmpeg concat list: each still shown from its start to the next start;
        # the last one until the audio ends.
        end = max(end_s, frames[-1].start + 1.0 / FPS)
        lines = ["ffconcat version 1.0"]
        for i, f in enumerate(frames):
            nxt = frames[i + 1].start if i + 1 < len(frames) else end
            lines.append(f"file '{png_of[(f.page, f.active)].name}'")
            lines.append(f"duration {max(nxt - f.start, 0.0):.6f}")
        lines.append(f"file '{png_of[(frames[-1].page, frames[-1].active)].name}'")
        (tmp / "frames.ffconcat").write_text("\n".join(lines) + "\n")

        part = dest.with_name(f".{dest.stem}.partial{dest.suffix}")
        cmd = [
            str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y",
            "-f", "concat", "-safe", "0", "-i", str(tmp / "frames.ffconcat"),
            "-i", str(audio_wav),
            "-map", "0:v", "-map", "1:a",
            "-vf", f"fps={FPS},format=yuv420p",
            "-c:v", "libx264", "-preset", "medium", "-tune", "stillimage", "-crf", "22",
            "-profile:v", "high", "-level", "4.1",
            "-af", audio_filter,
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart",
            "-t", f"{end:.3f}",
            str(part),
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True)
            if proc.returncode != 0:
                raise VideoError(f"FFmpeg could not write the video:\n{proc.stderr[-2000:]}")
            os.replace(part, dest)
        finally:
            part.unlink(missing_ok=True)
    return len(png_of)

