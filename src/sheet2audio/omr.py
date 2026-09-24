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
from fractions import Fraction
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
# Audiveris gives up on a page when one step takes over 120 s (its default),
# which a busy machine can hit; a slow page is better than a failed one.
C_STEP_TIMEOUT = "org.audiveris.omr.Main.sheetStepTimeOut"
STEP_TIMEOUT_S = 600


class OMRError(RuntimeError):
    pass


@dataclass
class OMRResult:
    movements: list[Path]  # one MusicXML file per movement, in order
    book: Path | None  # Audiveris .omr book (open in Audiveris to correct errors)
    log: Path
    pages: int  # pages transcribed
    notes: list[str] = field(default_factory=list)
    meter_fixes: list = field(default_factory=list)  # MeterFix: time signatures put back
    unplaced: list = field(default_factory=list)  # BarCheck: bars still missing chords


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
    from pypdf.errors import DependencyError, PdfReadError

    try:
        reader = PdfReader(str(source))
        if reader.is_encrypted and not reader.decrypt(""):
            raise OMRError(f"{source.name} is password-protected; save an unprotected copy first.")
        n = len(reader.pages)
    except (PdfReadError, OSError, ValueError, KeyError) as e:
        raise OMRError(f"{source.name} is not a readable PDF ({e}).") from None
    except DependencyError:
        # An encryption method pypdf cannot handle here; Audiveris (PDFBox)
        # can still read the file as it is.
        if pages:
            raise OMRError(f"{source.name} is encrypted in a way that prevents choosing pages; "
                           "run it without --sheets, or save an unprotected copy.") from None
        dest = dest_dir / "score.pdf"
        shutil.copyfile(source, dest)
        return _Staged(dest, None, notes=["The PDF is encrypted, so the scan-resolution check "
                                          "was skipped."])
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
    staged.constants.update(_pdf_constants(reader, selected, staged.notes))
    return staged


def _pdf_constants(reader, pages: list[int], notes: list[str]) -> dict[str, str]:
    """Audiveris draws every PDF page at 300 dpi (one setting for the whole
    file). Adjust that for scans stored at another resolution, and so that no
    page exceeds Audiveris' 20-megapixel limit."""
    inches = []
    for p in pages:
        box = reader.pages[p - 1].mediabox
        inches.append((float(box.width) / 72.0, float(box.height) / 72.0))
    largest = max(w * h for w, h in inches)
    cap = math.sqrt(MAX_PIXELS * 0.95 / largest)  # highest dpi that fits 20 MP
    dpi = _scanned_pdf_dpi(reader, pages)
    if dpi and 130 <= dpi < 290:
        # A scan at 130-290 dpi loses staves when resampled to 300; read it
        # at its own resolution (below ~130 dpi the enlargement helps).
        return {C_PDF_RES: str(int(min(dpi, cap))), C_MIN_INTERLINE: "8"}
    if dpi and dpi < 130 and cap < 300:
        # A scan stored at a low nominal resolution on a huge page (e.g. a
        # 300-dpi scan saved as a 72-dpi PDF): draw it pixel for pixel.
        return {C_PDF_RES: str(int(min(dpi, cap)))}
    if cap < 300:
        notes.append(f"The pages are unusually large ({inches[0][0]:.0f}x{inches[0][1]:.0f} in); "
                     f"they were read at {cap:.0f} dpi to stay within Audiveris' limit.")
        return {C_PDF_RES: str(int(cap))}
    return {}


def _visible_text(page) -> bool:
    """True if the page shows text. Text in render mode 3 (invisible, as
    scanner apps write for searchable PDFs) does not count."""
    from pypdf.generic import ContentStream

    try:
        contents = page.get_contents()
        if contents is None:
            return False
        ops = ContentStream(contents, page.pdf).operations
    except Exception:  # noqa: BLE001
        return bool((page.extract_text() or "").strip())
    mode, stack = 0, []
    for operands, op in ops:
        if op == b"q":
            stack.append(mode)
        elif op == b"Q":
            mode = stack.pop() if stack else 0
        elif op == b"Tr" and operands:
            mode = int(operands[0])
        elif op in (b"Tj", b"TJ", b"'", b'"') and mode != 3:
            text = operands[-1] if operands else ""
            if isinstance(text, list):
                text = "".join(x for x in text if isinstance(x, str))
            if str(text).strip():
                return True
    return False


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
            if len(images) != 1 or _visible_text(page):
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
        # A camera JPEG can carry a second picture (gain map, preview); it is
        # still one page.
        n = 1 if img.format in ("MPO", "JPEG") else getattr(img, "n_frames", 1)
    except (UnidentifiedImageError, OSError) as e:
        raise OMRError(f"{source.name} is not a readable image ({e}).") from None
    if pages and max(pages) > n:
        raise OMRError(f"{source.name} has {n} page{'s' if n > 1 else ''}; --sheets asked for "
                       f"page {max(pages)}.")
    selected = pages or list(range(1, n + 1))
    notes = []
    frames, dpis = [], []
    for p in selected:
        img.seek(p - 1)
        frame = ImageOps.exif_transpose(img.copy())
        dpi = _image_dpi(img.info.get("dpi"), frame.size)
        if frame.mode in ("RGBA", "LA", "PA") or (frame.mode == "P" and "transparency" in frame.info):
            frame = frame.convert("RGBA")
            white = Image.new("RGBA", frame.size, (255, 255, 255, 255))
            frame = Image.alpha_composite(white, frame)
        if frame.mode.startswith("I") or frame.mode == "F":  # 16/32-bit gray: scale, don't clip
            frame = frame.convert("I") if frame.mode != "F" else frame
            hi = frame.getextrema()[1] or 1
            frame = frame.point(lambda v: v * (255.0 / hi) if hi > 255 else v).convert("L")
        if frame.mode not in ("1", "L"):
            frame = frame.convert("L")  # also handles CMYK, which Audiveris rejects
        w, h = frame.size
        if w * h > MAX_PIXELS * 0.97:
            k = math.sqrt(MAX_PIXELS * 0.95 / (w * h))
            frame = frame.convert("L").resize((int(w * k), int(h * k)), Image.Resampling.LANCZOS)
            dpi *= k
            notes.append(f"Page {p} was {w * h / 1e6:.0f} megapixels; it was shrunk to "
                         f"{frame.size[0]}x{frame.size[1]} (Audiveris' limit is 20).")
        frames.append(frame)
        dpis.append(dpi)
    low = [p for p, d in zip(selected, dpis) if d < 130]
    if low:
        # Too coarse for Audiveris to find the staff lines. As PDF pages of
        # the right size, the images are drawn at 300 dpi, i.e. enlarged with
        # smoothing, which Audiveris can read; sharp pages are drawn 1:1.
        dest = dest_dir / "score.pdf"
        _images_to_pdf(frames, dpis, dest)
        notes.append(f"Page{'s' if len(low) > 1 else ''} {', '.join(map(str, low))}: only about "
                     f"{min(dpis):.0f} dpi; enlarged for recognition, so expect mistakes "
                     "(300 dpi scans work best).")
    elif len(frames) == 1:
        dest = dest_dir / "score.png"
        frames[0].save(dest)
    else:
        dest = dest_dir / "score.tif"
        frames[0].save(dest, save_all=True, append_images=frames[1:], compression="tiff_lzw")
    return _Staged(dest, selected, notes=notes, is_image=True)


def _images_to_pdf(frames, dpis: list[float], dest: Path) -> None:
    """One PDF page per image, each sized for its own resolution, so that at
    300 dpi a sharp page is drawn 1:1 and a coarse one is enlarged."""
    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter()
    for frame, dpi in zip(frames, dpis):
        # Never let the 300-dpi drawing exceed 20 megapixels.
        w, h = frame.size
        dpi = max(dpi, math.sqrt(w * h * 300 * 300 / (MAX_PIXELS * 0.95)))
        one = dest.with_suffix(".one.pdf")
        frame.save(one, resolution=float(dpi))
        writer.add_page(PdfReader(str(one)).pages[0])
    with open(dest, "wb") as f:
        writer.write(f)
    dest.with_suffix(".one.pdf").unlink(missing_ok=True)


def _image_dpi(tag, size: tuple[int, int]) -> float:
    """An image's resolution. The tag is trusted only when it describes a
    plausible page (at most ~14 inches wide); a tag that makes a normal
    page's worth of pixels look coarse (96 dpi from a screenshot tool) is
    ignored in favour of assuming a letter/A4-width page."""
    guess = size[0] / 8.5
    try:
        d = float(tag[0]) if tag else 0.0
    except (TypeError, ValueError, IndexError):
        d = 0.0
    if not 50 <= d <= 1200 or size[0] / d > 14 or (d < 130 <= guess):
        return guess
    return d


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
    cmd = [str(audiveris), "-batch", "-export", "-output", str(work),
           "-constant", f"{C_STEP_TIMEOUT}={STEP_TIMEOUT_S}"]
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
        meter_fixes, unplaced = [], []
        books = sorted(work.rglob("*.omr"))
        if books:
            scratch = tmp / "meters"
            scratch.mkdir()
            meter_fixes, exports = repair_meters(audiveris, books[0], log_path, scratch, timeout)
            if exports:
                mxls = exports
            unplaced = [b for b in check_bars(books[0]) if b.dropped]
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
                     pages=len(staged.pages or []) or 1, notes=notes,
                     meter_fixes=meter_fixes, unplaced=unplaced)


def _keep_book(work: Path, omr_dir: Path, source: Path) -> Path | None:
    if source.suffix.lower() == BOOK_SUFFIX:
        return None  # the user's own book; nothing new to keep
    books = sorted(work.rglob("*.omr"))
    if not books:
        return None
    dest = omr_dir / "score.omr"
    shutil.copyfile(books[0], dest)
    return dest


_SHEET_TAG = re.compile(r"\[\S*#(\d+)\]")
_LOW_RES = re.compile(r"too low interline|interline value of \d+ pixels", re.IGNORECASE)


def _sheet_reasons(log: str) -> dict[int, str]:
    """Why each invalid page (numbered in the staged file) failed:
    'empty' (no staff lines), 'lowres', or 'other'."""
    text: dict[int, list[str]] = {}
    current = None
    for line in log.splitlines():
        m = _SHEET_TAG.search(line[:60])
        if m:
            current = int(m.group(1))
        if current is not None:
            text.setdefault(current, []).append(line)
    reasons = {}
    for k in {int(x) for x in _INVALID.findall(log)}:
        body = "\n".join(text.get(k, []))
        if _LOW_RES.search(body):
            reasons[k] = "lowres"
        elif re.search(r"No regularly spaced lines|No staff", body, re.IGNORECASE):
            reasons[k] = "empty"
        else:
            reasons[k] = "other"
    return reasons


def _plan_retry(source: Path, staged: _Staged, log: str, notes: list[str], tmp: Path,
                work: Path) -> _Staged | None:
    if staged.pages is None:
        return None
    reasons = _sheet_reasons(log)
    low = _LOW_RES.search(log) is not None
    if low and staged.constants.get(C_MIN_INTERLINE) != "8":
        notes.append("The scan's resolution is low for Audiveris; it was read with relaxed "
                     "settings, so expect more mistakes (300 dpi scans work best).")
        retry = _Staged(staged.path, staged.pages, dict(staged.constants), is_image=staged.is_image)
        retry.constants[C_MIN_INTERLINE] = "8"
        return retry
    empty = sorted(k for k, r in reasons.items() if r in ("empty", "other"))
    if empty and len(empty) < len(staged.pages):
        bad = [staged.pages[k - 1] for k in empty if k <= len(staged.pages)]
        keep = [p for k, p in enumerate(staged.pages, 1) if k not in empty]
        notes.append(f"Page{'s' if len(bad) > 1 else ''} {', '.join(map(str, bad))} skipped: "
                     "no music found there (a title, text or blank page?).")
        retry = _stage(source, tmp, work, keep)  # its own resolution settings for these pages
        if staged.constants.get(C_MIN_INTERLINE):
            retry.constants.setdefault(C_MIN_INTERLINE, staged.constants[C_MIN_INTERLINE])
        return retry
    if C_PDF_RES in staged.constants:
        # Reading the scan at its own resolution failed: try Audiveris' default.
        return _Staged(staged.path, staged.pages, {})
    return None


_HINTS = [
    (r"Too large image: ([\d,]+) pixels",
     lambda m: f"A page came out at {int(m.group(1).replace(',', '')) / 1e6:.0f} megapixels; "
               "Audiveris accepts at most 20."),
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


# ---------------------------------------------------------------- keys from the book


@dataclass
class BookKey:
    """A key signature Audiveris recognised: in the `page`-th page of music
    (0-based, counting every page of every sheet in order) at the start of
    that page's `measure`-th bar (0-based)."""

    page: int
    measure: int
    fifths: int


def book_keys(book: Path) -> list[BookKey]:
    """Every key signature in an Audiveris book, as Audiveris recognised it.

    Audiveris' MusicXML export sometimes drops a key change: a key made of
    natural signs (a cancellation, e.g. B-flat major -> C major) is not
    exported. The book still holds it. A cancellation counts as C major
    unless a new key follows it on the same staff."""
    import xml.etree.ElementTree as ET
    import zipfile

    out: list[BookKey] = []
    try:
        z = zipfile.ZipFile(book)
    except (OSError, zipfile.BadZipFile):
        return out
    page_index = 0
    with z:
        sheets = sorted((n for n in z.namelist() if re.fullmatch(r"sheet#(\d+)/sheet#\1\.xml", n)),
                        key=lambda n: int(re.search(r"#(\d+)", n).group(1)))
        for name in sheets:
            try:
                root = ET.fromstring(z.read(name))
            except ET.ParseError:
                continue
            il = root.find("scale/interline")
            reach = 12 * float(il.get("main", 20)) if il is not None else 240  # a key's width
            for page in root.iter("page"):
                for system in page.iter("system"):
                    # Bars are numbered from 1 on each page; a cautionary
                    # stack (courtesy signs at the end of a line) repeats the
                    # number of the bar before it and is skipped.
                    stacks = [(float(st.get("left", 0)), float(st.get("right", 0)), st.get("id", ""))
                              for st in system.iter("stack") if st.get("special") != "CAUTIONARY"]
                    keys = []
                    for k in system.iter("key"):
                        b = k.find("bounds")
                        if b is None:
                            continue
                        x = float(b.get("x", 0))
                        if k.get("shape") == "KEY_CANCEL":
                            keys.append((x, k.get("staff"), None))
                        elif re.fullmatch(r"-?\d", k.get("fifths") or ""):
                            keys.append((x, k.get("staff"), int(k.get("fifths"))))
                    by_bar: dict[int, list[int]] = {}
                    for x, staff, fifths in keys:
                        if fifths is None:
                            after = [f for x2, s2, f in keys
                                     if s2 == staff and f is not None and x < x2 <= x + reach]
                            fifths = after[0] if after else 0
                        bar = next((sid for left, right, sid in stacks if left - 5 <= x < right), None)
                        if bar and bar.isdigit():
                            by_bar.setdefault(int(bar) - 1, []).append(fifths)
                    for bar, values in sorted(by_bar.items()):
                        out.append(BookKey(page_index, bar, max(set(values), key=values.count)))
                page_index += 1
    return out


# ---------------------------------------------------------------- bars Audiveris could not time


@dataclass
class BarCheck:
    """One bar (a 'stack' across all staves) as Audiveris' rhythm step saw it."""

    sheet: int  # sheet (page image) number, 1-based
    page: int  # page of music, 0-based over the whole book (as in BookKey)
    system: int  # line of music within the sheet, 0-based
    bar: int  # bar within the page, 1-based (Audiveris' stack id)
    expected: Fraction  # bar length from the time signature Audiveris assumed (whole notes)
    duration: Fraction  # length of the longest voice Audiveris built (whole notes)
    abnormal: bool  # Audiveris flagged its rhythm
    dropped: int  # chords recognised but left out: no place in time, so not exported


def _fraction(text: str | None) -> Fraction:
    try:
        return Fraction(text or "0")
    except (ValueError, ZeroDivisionError):
        return Fraction(0)


def _book_sheets(z) -> list[str]:
    return sorted((n for n in z.namelist() if re.fullmatch(r"sheet#(\d+)/sheet#\1\.xml", n)),
                  key=lambda n: int(re.search(r"#(\d+)", n).group(1)))


def check_bars(book: Path) -> list[BarCheck]:
    """Every bar of an Audiveris book with its rhythm verdict, in score order.

    Audiveris fits each bar's chords into voices against the length the
    time signature gives. Chords that do not fit get no time offset, and its
    MusicXML export silently leaves them out; this finds them."""
    import xml.etree.ElementTree as ET
    import zipfile

    out: list[BarCheck] = []
    try:
        z = zipfile.ZipFile(book)
    except (OSError, zipfile.BadZipFile):
        return out
    page_index = 0
    with z:
        for name in _book_sheets(z):
            sheet = int(re.search(r"#(\d+)", name).group(1))
            try:
                root = ET.fromstring(z.read(name))
            except ET.ParseError:
                continue
            system_index = 0
            for page in root.iter("page"):
                for system in page.iter("system"):
                    dropped: dict[str, int] = {}
                    abnormal: set[str] = set()
                    for part in system.findall("part"):
                        for m in part.findall("measure"):
                            mid = m.get("id", "")
                            chords = set()
                            for tag in ("head-chords", "rest-chords"):
                                for e in m.findall(tag):
                                    chords |= set((e.text or "").split())
                            timed = {v.get("chord") for v in m.iter("value")}
                            for v in m.findall("voice"):
                                timed |= set(v.attrib.values())  # e.g. measure-rest-chord
                            dropped[mid] = dropped.get(mid, 0) + len(chords - timed)
                            if m.get("abnormal") == "true":
                                abnormal.add(mid)
                    for st in system.iter("stack"):
                        sid = st.get("id", "")
                        if st.get("special") == "CAUTIONARY" or not sid.isdigit():
                            continue
                        out.append(BarCheck(sheet, page_index, system_index, int(sid),
                                            _fraction(st.get("expected")),
                                            _fraction(st.get("duration")),
                                            sid in abnormal, dropped.get(sid, 0)))
                    system_index += 1
                page_index += 1
    return out


@dataclass
class MeterFix:
    """A time signature Audiveris missed, put back into its book."""

    page: int  # as in BarCheck / BookKey
    bar: int  # 1-based within the page
    beats: int
    beat_type: int
    recovered: int  # chords that now have a place in time (and get exported)


_WHOLE_TIMES = {"COMMON_TIME": (4, 4), "CUT_TIME": (2, 2)}
_STEPS_AFTER_LINKS = ("RHYTHMS", "PAGE")


def _meter_before(root_sheets, target: BarCheck) -> tuple[int, int] | None:
    """The last time signature Audiveris recognised at or before `target`."""
    found = None
    for sheet, root in root_sheets:
        page_systems = [s for p in root.iter("page") for s in p.iter("system")]
        for si, system in enumerate(page_systems):
            if (sheet, si) > (target.sheet, target.system):
                return found
            stacks = {st.get("id"): (float(st.get("left", 0)), float(st.get("right", 0)))
                      for st in system.iter("stack")}
            for t in system.iter():
                if t.tag not in ("time-pair", "time-whole") or t.find("bounds") is None:
                    continue
                x = float(t.find("bounds").get("x", 0))
                bar = next((int(k) for k, (l, r) in stacks.items() if k.isdigit() and l - 5 <= x < r),
                           None)
                if (sheet, si) == (target.sheet, target.system) and (bar is None or bar > target.bar):
                    continue
                rational = t.get("time-rational") or ""
                m = re.fullmatch(r"(\d+)/(\d+)", rational)
                if m:
                    found = (int(m.group(1)), int(m.group(2)))
                elif t.get("shape") in _WHOLE_TIMES:
                    found = _WHOLE_TIMES[t.get("shape")]
                else:
                    m = re.fullmatch(r"TIME_(\w+)_(\w+)", t.get("shape") or "")
                    words = {"TWO": 2, "THREE": 3, "FOUR": 4, "FIVE": 5, "SIX": 6, "SEVEN": 7,
                             "EIGHT": 8, "NINE": 9, "TWELVE": 12, "SIXTEEN": 16}
                    if m and m.group(1) in words and m.group(2) in words:
                        found = (words[m.group(1)], words[m.group(2)])
    return found


def _meter_for(length: Fraction, beat_type: int) -> tuple[int, int] | None:
    """A time signature whose bar lasts `length` whole notes, preferring `beat_type`."""
    for bt in (beat_type, 4, 8, 2, 16):
        beats = length * bt
        if beats.denominator == 1 and 1 <= beats <= 16:
            return int(beats), bt
    return None


def _inject_time(src: Path, dest: Path, target: BarCheck, beats: int, beat_type: int) -> bool:
    """Copy the book `src` to `dest` with a time signature `beats/beat_type`
    at the start of `target`'s bar on every staff of its line, and with every
    page from that sheet on set back to just before the rhythm step (so
    Audiveris re-times them, and nothing else, when asked for the PAGE step)."""
    import xml.etree.ElementTree as ET
    import zipfile

    with zipfile.ZipFile(src) as z:
        name = f"sheet#{target.sheet}/sheet#{target.sheet}.xml"
        try:
            root = ET.fromstring(z.read(name))
        except (KeyError, ET.ParseError):
            return False
        systems = [s for p in root.iter("page") for s in p.iter("system")]
        if target.system >= len(systems):
            return False
        system = systems[target.system]
        stack = next((st for st in system.iter("stack")
                      if st.get("id") == str(target.bar) and st.get("special") != "CAUTIONARY"), None)
        inters = system.find("sig/inters")
        if stack is None or inters is None:
            return False
        measures = [(part, m) for part in system.findall("part") for m in part.findall("measure")
                    if m.get("id") == str(target.bar)]
        if not measures or any(m.find("times") is not None for _, m in measures):
            return False  # a time signature is already there
        last = int(root.get("last-persistent-id", "0") or 0)
        il = root.find("scale/interline")
        interline = float(il.get("main", 20)) if il is not None else 20.0
        left = float(stack.get("left", 0))
        ids: dict[str, int] = {}
        for staff in system.iter("staff"):
            ys = [float(p.get("y")) for line in staff.iter("line") for p in line.iter("point")]
            if not staff.get("id") or not ys:
                continue
            last += 1
            ids[staff.get("id")] = last
            t = ET.Element("time-pair", {"time-rational": f"{beats}/{beat_type}", "grade": "0.9",
                                         "ctx-grade": "0.9", "staff": staff.get("id"), "id": str(last)})
            ET.SubElement(t, "bounds", {"x": str(int(left + interline / 2)), "y": str(int(min(ys))),
                                        "w": str(int(interline * 1.7)),
                                        "h": str(int(max(ys) - min(ys)))})
            inters.insert(0, t)
        for part, m in measures:
            staff_ids = [ids[s.get("id")] for s in part.findall("staff") if s.get("id") in ids]
            if not staff_ids:
                return False
            times = ET.Element("times")
            times.text = " ".join(map(str, staff_ids))
            barline = m.find("right-barline")
            m.insert(list(m).index(barline) + 1 if barline is not None else 0, times)
        root.set("last-persistent-id", str(last))
        book_xml = z.read("book.xml").decode("utf-8")

        def reset(match):
            number = int(match.group(1))
            body = match.group(2)
            if number >= target.sheet:
                body = re.sub(r"<steps>([^<]*)</steps>", lambda s: "<steps>" + " ".join(
                    w for w in s.group(1).split() if w not in _STEPS_AFTER_LINKS) + "</steps>", body)
            return f'<sheet number="{match.group(1)}"' + body

        book_xml = re.sub(r'<sheet number="(\d+)"(.*?)(?=<sheet number=|</book>)', reset, book_xml,
                          flags=re.S)
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as out:
            for item in z.infolist():
                data = z.read(item.filename)
                if item.filename == name:
                    data = b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n' \
                        + ET.tostring(root, encoding="utf-8")
                elif item.filename == "book.xml":
                    data = book_xml.encode("utf-8")
                out.writestr(item, data)
    return True


def _drop_score(bars: list[BarCheck]) -> tuple[int, int]:
    return sum(b.dropped for b in bars), sum(b.abnormal for b in bars)


def _long_runs(bars: list[BarCheck]) -> list[list[int]]:
    """Runs of 2+ consecutive bars where chords were dropped or a voice ran
    long: what a missed time signature (to a longer bar) looks like. A lone
    bar is more likely a misread rhythm."""
    runs, run = [], []
    for i, b in enumerate(bars):
        if b.dropped or (b.abnormal and b.duration > b.expected):
            run.append(i)
        else:
            if len(run) >= 2:
                runs.append(run)
            run = []
    if len(run) >= 2:
        runs.append(run)
    return runs


def repair_meters(audiveris: Path, book: Path, log_path: Path, scratch: Path,
                  timeout: float | None = None,
                  max_trials: int = 8) -> tuple[list[MeterFix], list[Path]]:
    """Find time signatures Audiveris missed (its rhythm step then drops the
    chords that do not fit the old bar length) and put them back: add the
    time signature to the book, let Audiveris re-time the pages from there,
    and keep the change only if fewer chords are dropped. Returns the fixes
    and the new MusicXML exports (empty if nothing changed; they live in
    `scratch`); `book` is updated in place."""
    import xml.etree.ElementTree as ET
    import zipfile

    fixes: list[MeterFix] = []
    exports: list[Path] = []
    bars = check_bars(book)
    tried: set[tuple[int, int]] = set()
    trials = 0
    while trials < max_trials:
        runs = [r for r in _long_runs(bars) if (bars[r[0]].page, bars[r[0]].bar) not in tried]
        if not runs:
            break
        run = runs[0]
        start = bars[run[0]]
        tried.add((start.page, start.bar))
        with zipfile.ZipFile(book) as z:
            sheets = []
            for n in _book_sheets(z):
                try:
                    sheets.append((int(re.search(r"#(\d+)", n).group(1)), ET.fromstring(z.read(n))))
                except ET.ParseError:
                    pass
        old = _meter_before(sheets, start)
        beat_type = old[1] if old else 4
        longer = [bars[i].duration for i in run if bars[i].duration > bars[i].expected]
        lengths = sorted(set(longer), key=lambda d: (-longer.count(d), d))
        lengths += [x for x in (start.expected * 2, start.expected * 3 / 2) if x not in lengths]
        best = None
        for length in lengths[:3]:
            meter = _meter_for(length, beat_type)
            if meter is None or trials >= max_trials:
                continue
            trials += 1
            with tempfile.TemporaryDirectory(prefix="meter-", dir=scratch) as tmp_name:
                trial = Path(tmp_name) / "score.omr"
                if not _inject_time(book, trial, start, *meter):
                    continue
                cmd = [str(audiveris), "-batch", "-step", "PAGE", "-export", "-save",
                       "-constant", f"{C_STEP_TIMEOUT}={STEP_TIMEOUT_S}",
                       "-output", tmp_name, "--", str(trial)]
                with open(log_path, "a") as log:
                    log.write(f"\n==== sheet2audio: trying {meter[0]}/{meter[1]} at sheet "
                              f"{start.sheet} bar {start.bar}\n")
                    log.flush()
                    try:
                        subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, timeout=timeout)
                    except subprocess.TimeoutExpired:
                        continue
                after = check_bars(trial)
                mxls = sorted(Path(tmp_name).rglob("*.mxl"), key=_movement_key)
                if not mxls or len(after) != len(bars):
                    continue
                # A real bar length makes whole bars of the run come out clean;
                # a wrong one that merely fits more (e.g. misread specks) does not.
                clean = sum(1 for i in run if not after[i].dropped and not after[i].abnormal)
                if clean >= 2 and _drop_score(after)[0] < _drop_score(bars)[0] and \
                        (best is None or _drop_score(after) < _drop_score(best[1])):
                    keep = scratch / f"best{trials}"
                    keep.mkdir()
                    shutil.copyfile(trial, keep / "score.omr")
                    for m in mxls:
                        shutil.copyfile(m, keep / m.name)
                    best = (meter, after, keep)
                    if not any(after[i].dropped for i in run):
                        break  # this bar length fits the whole run
        if best is None:
            continue
        meter, after, keep = best
        recovered = _drop_score(bars)[0] - _drop_score(after)[0]
        shutil.copyfile(keep / "score.omr", book)
        exports = sorted(keep.glob("*.mxl"), key=_movement_key)
        fixes.append(MeterFix(start.page, start.bar, meter[0], meter[1], recovered))
        bars = after
    return fixes, exports
