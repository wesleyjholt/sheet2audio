"""Optical music recognition: PDF/image -> MusicXML via Audiveris (AGPL-3.0)."""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

IMAGE_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif"}
MUSICXML_SUFFIXES = {".mxl", ".musicxml", ".xml"}


class OMRError(RuntimeError):
    pass


@dataclass
class OMRResult:
    movements: list[Path]  # one MusicXML file per movement, in order
    book: Path | None  # Audiveris .omr book (open in Audiveris to correct errors)
    log: Path


_MVT = re.compile(r"\.mvt(\d+)\.mxl$", re.IGNORECASE)


def _movement_key(p: Path) -> int:
    m = _MVT.search(p.name)
    return int(m.group(1)) if m else 0


def run_audiveris(
    audiveris: Path,
    source: Path,
    outdir: Path,
    sheets: str | None = None,
    timeout: float | None = None,
    verbose: bool = False,
) -> OMRResult:
    """Transcribe `source` (a PDF or an image, or an Audiveris .omr book) and
    export MusicXML. Results are copied into `outdir/omr/`.

    Audiveris is run in a fresh temporary folder under a plain ASCII name so
    that stale books from earlier runs and unusual file names cannot interfere.
    """
    outdir = Path(outdir)
    omr_dir = outdir / "omr"
    omr_dir.mkdir(parents=True, exist_ok=True)
    stem = "score"
    with tempfile.TemporaryDirectory(prefix="sheet2audio-omr-") as tmp:
        tmp = Path(tmp)
        staged = tmp / f"{stem}{source.suffix.lower()}"
        shutil.copyfile(source, staged)
        work = tmp / "out"
        work.mkdir()
        # For an .omr book (e.g. one corrected by hand in the Audiveris GUI) this
        # only exports; no -force, so manual corrections are kept.
        cmd = [str(audiveris), "-batch", "-export", "-output", str(work)]
        if sheets:
            cmd += ["-sheets", *sheets.split()]
        cmd += ["--", str(staged)]
        log_path = omr_dir / "audiveris.log"
        with open(log_path, "w") as log:
            proc = subprocess.run(
                cmd, stdout=log, stderr=subprocess.STDOUT, timeout=timeout, text=True
            )
        mxls = sorted(work.rglob("*.mxl"), key=_movement_key)
        books = sorted(work.rglob("*.omr"))
        if not mxls:
            tail = log_path.read_text(errors="replace").splitlines()[-25:]
            hints = _diagnose("\n".join(log_path.read_text(errors="replace").splitlines()))
            raise OMRError(
                f"Audiveris produced no MusicXML (exit code {proc.returncode}).\n"
                + (hints + "\n" if hints else "")
                + f"Last log lines ({log_path}):\n  "
                + "\n  ".join(tail)
            )
        movements = []
        for i, m in enumerate(mxls, 1):
            name = "score.mxl" if len(mxls) == 1 else f"score.mvt{i}.mxl"
            dest = omr_dir / name
            shutil.copyfile(m, dest)
            movements.append(dest)
        book = None
        if books and source.suffix.lower() != ".omr":
            book = omr_dir / "score.omr"
            shutil.copyfile(books[0], book)
    return OMRResult(movements=movements, book=book, log=log_path)


def _diagnose(log_text: str) -> str:
    hints = []
    low = log_text.lower()
    if "interline" in low and ("too low" in low or "too small" in low):
        hints.append(
            "Hint: the staff lines are too close together in pixels. Re-scan at 300 dpi or more."
        )
    if "no staff" in low or "no system" in low or "0 systems" in low:
        hints.append("Hint: Audiveris found no staves. Is this page actually sheet music?")
    if "outofmemory" in low.replace(" ", ""):
        hints.append("Hint: Java ran out of memory. Try processing fewer pages with --sheets.")
    return "\n".join(hints)


def log_warnings(log: Path, limit: int = 20) -> list[str]:
    """Return the WARN lines Audiveris printed (deduplicated, trimmed)."""
    out: list[str] = []
    seen = set()
    for line in log.read_text(errors="replace").splitlines():
        if line.startswith("WARN"):
            msg = re.sub(r"^WARN\s+\[[^\]]*\]\s+\S+\s+\d+\s+\|\s*", "", line).strip()
            if msg and msg not in seen:
                seen.add(msg)
                out.append(msg)
    return out[:limit]
