"""Optical music recognition: PDF/image -> MusicXML via Audiveris (AGPL-3.0).

Before Audiveris sees the input, images are normalised with Pillow (grayscale,
EXIF rotation, at most 20 megapixels, which is Audiveris' limit) and page
selections are cut out of PDFs with pypdf. Audiveris' own page selection
(-sheets) makes every movement overwrite the previous one on export, so it is
not used. When Audiveris refuses a book because one page has no staves (a cover
or blank page), the run is repeated without those pages.
"""

from __future__ import annotations

import math
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

PDF_SUFFIXES = {".pdf"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif", ".webp"}
MUSICXML_SUFFIXES = {".mxl", ".musicxml", ".xml"}
BOOK_SUFFIX = ".omr"

MAX_PIXELS = 20_000_000  # org.audiveris.omr.step.LoadStep.maxPixelCount
C_PDF_RES = "org.audiveris.omr.image.ImageLoading.pdfResolution"
C_MIN_INTERLINE = "org.audiveris.omr.sheet.ScaleBuilder.minInterline"


class OMRError(RuntimeError):
    pass


@dataclass
class OMRResult:
    movements: list[Path]  # one MusicXML file per movement, in order
    book: Path | None  # Audiveris .omr book (open in Audiveris to correct errors)
    log: Path
    pages: int  # pages transcribed
    notes: list[str] = field(default_factory=list)


def parse_sheets(spec: str) -> list[int]:
    """'1 3-4' or '1,3-4' -> [1, 3, 4]."""
    pages: set[int] = set()
    for tok in re.split(r"[\s,]+", spec.strip()):
        if not tok:
            continue
        m = re.fullmatch(r"(\d+)(?:-(\d+))?", tok)
        if not m:
            raise ValueError(f"bad page selection '{tok}' (use e.g. \"1 3-4\")")
        a, b = int(m.group(1)), int(m.group(2) or m.group(1))
        if a < 1 or b < a:
            raise ValueError(f"bad page range '{tok}'")
        pages.update(range(a, b + 1))
    if not pages:
        raise ValueError("empty page selection")
    return sorted(pages)


# ---------------------------------------------------------------- input preparation


@dataclass
class _Staged:
    path: Path
    pages: list[int] | None  # original page numbers, in order; None for a book
    constants: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    is_image: bool = False


def _stage_pdf(source: Path, dest_dir: Path, pages: list[int] | None) -> _Staged:
    from pypdf import PdfReader, PdfWriter
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(str(source))
        if reader.is_encrypted and not reader.decrypt(""):
            raise OMRError(f"{source.name} is password-protected; save an unprotected copy first.")
        n = len(reader.pages)
    except (PdfReadError, OSError, ValueError, KeyError) as e:
        raise OMRError(f"{source.name} is not a readable PDF ({e}).") from None
    if n == 0:
        raise OMRError(f"{source.name} has no pages.")
    if pages and max(pages) > n:
        raise OMRError(f"{source.name} has {n} page{'s' if n > 1 else ''}; --sheets asked for "
                       f"page {max(pages)}.")
    selected = pages or list(range(1, n + 1))
    dest = dest_dir / "score.pdf"
    if pages:
        writer = PdfWriter()
        for p in selected:
            writer.add_page(reader.pages[p - 1])
        with open(dest, "wb") as f:
            writer.write(f)
    else:
        shutil.copyfile(source, dest)
    staged = _Staged(dest, selected)
    dpi = _scanned_pdf_dpi(reader, selected)
    # Audiveris draws PDF pages at 300 dpi. For a scan stored at 130-290 dpi
    # that resampling loses staves; reading at the scan's own resolution works
    # (below ~130 dpi the upsampling helps, so it is left alone).
    if dpi and 130 <= dpi < 290:
        staged.constants[C_PDF_RES] = str(int(round(dpi)))
        staged.constants[C_MIN_INTERLINE] = "8"
    return staged


def _scanned_pdf_dpi(reader, pages: list[int]) -> float | None:
    """Lowest resolution of the scanned images, if every page is one image."""
    dpis = []
    for p in pages:
        page = reader.pages[p - 1]
        try:
            xobjs = page["/Resources"].get_object().get("/XObject")
            xobjs = xobjs.get_object() if xobjs is not None else {}
            images = [x.get_object() for x in xobjs.values()
                      if x.get_object().get("/Subtype") == "/Image"]
            if len(images) != 1 or (page.extract_text() or "").strip():
                return None
            width_in = float(page.mediabox.width) / 72.0
            dpis.append(int(images[0]["/Width"]) / width_in)
        except Exception:  # noqa: BLE001 - any odd PDF structure: treat as not a scan
            return None
    return min(dpis) if dpis else None


def _stage_image(source: Path, dest_dir: Path, pages: list[int] | None) -> _Staged:
    from PIL import Image, ImageOps, UnidentifiedImageError

    try:
        img = Image.open(source)
        n = getattr(img, "n_frames", 1)
    except (UnidentifiedImageError, OSError) as e:
        raise OMRError(f"{source.name} is not a readable image ({e}).") from None
    if pages and max(pages) > n:
        raise OMRError(f"{source.name} has {n} page{'s' if n > 1 else ''}; --sheets asked for "
                       f"page {max(pages)}.")
    selected = pages or list(range(1, n + 1))
    notes = []
    frames = []
    for p in selected:
        img.seek(p - 1)
        frame = ImageOps.exif_transpose(img.copy())
        if frame.mode in ("RGBA", "LA", "PA") or (frame.mode == "P" and "transparency" in frame.info):
            frame = frame.convert("RGBA")
            white = Image.new("RGBA", frame.size, (255, 255, 255, 255))
            frame = Image.alpha_composite(white, frame)
        if frame.mode not in ("1", "L"):
            frame = frame.convert("L")  # also handles CMYK and 16-bit, which Audiveris rejects
        w, h = frame.size
        if w * h > MAX_PIXELS * 0.97:
            k = math.sqrt(MAX_PIXELS * 0.95 / (w * h))
            frame = frame.convert("L").resize((int(w * k), int(h * k)), Image.Resampling.LANCZOS)
            notes.append(f"The image was {w * h / 1e6:.0f} megapixels; it was shrunk to "
                         f"{frame.size[0]}x{frame.size[1]} (Audiveris' limit is 20).")
        frames.append(frame)
    if len(frames) == 1:
        dest = dest_dir / "score.png"
        frames[0].save(dest)
    else:
        dest = dest_dir / "score.tif"
        frames[0].save(dest, save_all=True, append_images=frames[1:], compression="tiff_lzw")
    return _Staged(dest, selected, notes=notes, is_image=True)


def _stage(source: Path, tmp: Path, work: Path, pages: list[int] | None) -> _Staged:
    suffix = source.suffix.lower()
    if suffix == BOOK_SUFFIX:
        # Audiveris exports a book next to the book file and ignores -output.
        dest = work / "score.omr"
        shutil.copyfile(source, dest)
        return _Staged(dest, None)
    if suffix in PDF_SUFFIXES:
        return _stage_pdf(source, tmp, pages)
    return _stage_image(source, tmp, pages)


# ---------------------------------------------------------------- running Audiveris


_MVT = re.compile(r"\.mvt(\d+)\.mxl$", re.IGNORECASE)
_INVALID = re.compile(r"Sheet \S*#(\d+) flagged as invalid")


def _movement_key(p: Path) -> tuple[int, str]:
    m = _MVT.search(p.name)
    return (int(m.group(1)) if m else 0, p.name)


def _run(audiveris: Path, staged: _Staged, work: Path, log_path: Path,
         timeout: float | None) -> tuple[int, str, list[Path]]:
    for old in work.iterdir():
        if old.suffix.lower() == ".mxl":
            old.unlink()
    cmd = [str(audiveris), "-batch", "-export", "-output", str(work)]
    for k, v in staged.constants.items():
        cmd += ["-constant", f"{k}={v}"]
    cmd += ["--", str(staged.path)]
    with open(log_path, "w") as log:
        proc = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, timeout=timeout)
    text = log_path.read_text(errors="replace")
    mxls = sorted(work.rglob("*.mxl"), key=_movement_key)
    return proc.returncode, text, mxls


def run_audiveris(audiveris: Path, source: Path, outdir: Path, sheets: list[int] | None = None,
                  timeout: float | None = None) -> OMRResult:
    """Transcribe `source` (a PDF, an image or an Audiveris .omr book) and
    export MusicXML into `outdir/omr/`."""
    omr_dir = Path(outdir) / "omr"
    omr_dir.mkdir(parents=True, exist_ok=True)
    log_path = omr_dir / "audiveris.log"
    notes: list[str] = []
    with tempfile.TemporaryDirectory(prefix="sheet2audio-omr-") as tmp_name:
        tmp = Path(tmp_name)
        work = tmp / "out"
        work.mkdir()
        staged = _stage(source, tmp, work, sheets)
        notes += staged.notes
        tried: set[tuple] = set()
        while True:
            tried.add(tuple(sorted(staged.constants.items())) + (tuple(staged.pages or ()),))
            code, log, mxls = _run(audiveris, staged, work, log_path, timeout)
            if mxls:
                break
            retry = _plan_retry(source, staged, log, notes, tmp, work)
            key = None if retry is None else (tuple(sorted(retry.constants.items()))
                                              + (tuple(retry.pages or ()),))
            if retry is None or key in tried:
                _keep_book(work, omr_dir, source)
                raise OMRError(_failure_message(log, code, log_path, staged))
            staged = retry
        movements = []
        for i, m in enumerate(mxls, 1):
            dest = omr_dir / ("score.mxl" if len(mxls) == 1 else f"score.mvt{i}.mxl")
            shutil.copyfile(m, dest)
            movements.append(dest)
        book = _keep_book(work, omr_dir, source)
    if any("mvtnull" in m.name for m in mxls):
        notes.append("Audiveris could not tell the movements apart; some may be missing.")
    notes += _log_notes(log)
    return OMRResult(movements=movements, book=book, log=log_path,
                     pages=len(staged.pages or []) or 1, notes=notes)


def _keep_book(work: Path, omr_dir: Path, source: Path) -> Path | None:
    if source.suffix.lower() == BOOK_SUFFIX:
        return None  # the user's own book; nothing new to keep
    books = sorted(work.rglob("*.omr"))
    if not books:
        return None
    dest = omr_dir / "score.omr"
    shutil.copyfile(books[0], dest)
    return dest


def _plan_retry(source: Path, staged: _Staged, log: str, notes: list[str], tmp: Path,
                work: Path) -> _Staged | None:
    if staged.pages is None:
        return None
    invalid = sorted({int(k) for k in _INVALID.findall(log)})
    if invalid and len(invalid) < len(staged.pages):
        bad = [staged.pages[k - 1] for k in invalid if k <= len(staged.pages)]
        keep = [p for k, p in enumerate(staged.pages, 1) if k not in invalid]
        notes.append(f"Page{'s' if len(bad) > 1 else ''} {', '.join(map(str, bad))} skipped: "
                     "no staves found there (a title, text or blank page?).")
        retry = _stage(source, tmp, work, keep)
        retry.constants = dict(staged.constants)
        return retry
    low = re.search(r"too low interline|interline value of \d+ pixels", log, re.IGNORECASE)
    if low and staged.constants.get(C_MIN_INTERLINE) != "8":
        notes.append("The scan's resolution is low for Audiveris; it was read with relaxed "
                     "settings, so expect more mistakes (300 dpi scans work best).")
        retry = _Staged(staged.path, staged.pages, dict(staged.constants), is_image=staged.is_image)
        retry.constants[C_MIN_INTERLINE] = "8"
        return retry
    if C_PDF_RES in staged.constants:
        # Reading the scan at its own resolution failed: try Audiveris' default.
        return _Staged(staged.path, staged.pages, {})
    return None


_HINTS = [
    (r"Too large image: ([\d,]+) pixels",
     lambda m: f"A page is {int(m.group(1).replace(',', '')) / 1e6:.0f} megapixels; Audiveris "
               "accepts at most 20. Re-export or scan the PDF at 300 dpi."),
    (r"No regularly spaced lines found",
     lambda m: "No staff lines were found: is it a blank or text-only page, or a very noisy scan?"),
    (r"too low interline|interline value of \d+ pixels",
     lambda m: "The staff lines are too close together in pixels: scan at 300 dpi or more."),
    (r"Unsupported sample model",
     lambda m: "Audiveris cannot read this image's colour format; convert it to grayscale."),
    (r"No sheet #\d+",
     lambda m: "The input has fewer pages than --sheets asks for."),
    (r"OutOfMemory",
     lambda m: "Java ran out of memory. Try fewer pages at a time with --sheets."),
    (r"Comparison method violates its general contract",
     lambda m: "Audiveris hit an internal error on this image; a cleaner scan usually helps."),
]

_GENERIC = re.compile(r"Error in export|Exit forced|Could not export since|^\s*at |^\s+\S", re.I)


def _log_lines(log: str, levels=("WARN", "ERROR")) -> list[str]:
    out, seen = [], set()
    for line in log.splitlines():
        if not line.startswith(levels):
            continue
        msg = re.sub(r"^(WARN|ERROR)\s+(\[[^\]]*\]\s+)?\S+\s+\d+\s+\|\s*", "", line).strip()
        if msg and msg not in seen and not _GENERIC.search(msg):
            seen.add(msg)
            out.append(msg)
    return out


def _failure_message(log: str, code: int, log_path: Path, staged: _Staged) -> str:
    hints = []
    for pat, fmt in _HINTS:
        m = re.search(pat, log, re.IGNORECASE)
        if m:
            hints.append(fmt(m))
    invalid = sorted({int(k) for k in _INVALID.findall(log)})
    if invalid and staged.pages:
        pages = [str(staged.pages[k - 1]) for k in invalid if k <= len(staged.pages)]
        hints.append(f"No music was found on page {', '.join(pages)}.")
    details = _log_lines(log)[-8:]
    msg = "Audiveris could not read any music from this file"
    msg += f" (exit code {code})." if code else "."
    if hints:
        msg += "\n" + "\n".join(f"  - {h}" for h in hints)
    if details:
        msg += "\nFrom the Audiveris log:\n" + "\n".join(f"    {d}" for d in details)
    return msg + f"\nFull log: {log_path}"


def _log_notes(log: str) -> list[str]:
    """Audiveris warnings worth showing to the user."""
    notes = []
    if re.search(r"NOT RELIABLE", log):
        notes.append("Audiveris says the staff size is at the limit of what it reads reliably; "
                     "a higher-resolution scan may give better results.")
    if re.search(r"please check time signatures", log, re.IGNORECASE):
        notes.append("Audiveris could not match some bars to a time signature; check the "
                     "time signatures.")
    return notes


def log_warnings(log: Path, limit: int = 20) -> list[str]:
    """The distinct WARN lines Audiveris printed, without stack traces."""
    return _log_lines(log.read_text(errors="replace"), ("WARN",))[:limit]
