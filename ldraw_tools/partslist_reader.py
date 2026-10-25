"""Read a parts list from a PDF page or a photo of one, so it can go through the normal import route.

PDF text is tried first (``pypdf``, then the ``pdftotext`` command); when a PDF has no text layer, or the upload is an
image, the page is read by OCR with the ``tesseract`` command (PDF pages are rendered by ``pypdfium2`` or ``pdftoppm``).
Every extraction tool is optional: without one the reader says what to install and the rest of the app is unaffected.
This module stays standard-library only.

The result is a list of ``PartLine`` (part number, colour name, quantity, a 0-1 ``confidence`` and the ``flags`` that
lowered it) plus ``to_csv()``, a BrickLink CSV (Item No, Color, Qty) that ``importers.parse`` reads like any other file.
Lines below ``REVIEW_BELOW`` are meant to be checked by a person before import. The patterns were written against
generated pages (``Nx`` quantity, BrickLink part number, colour name, in any order or as table columns); real
instruction pages may need them tuned.
"""
from __future__ import annotations

import csv
import io
import re
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .bricklink import load_bricklink_colours, norm_colour_name

REVIEW_BELOW = 0.8
MAX_BYTES = 25 * 1024 * 1024
MAX_PAGES = 12
OCR_DPI = 300
MIN_TEXT_CHARS = 20                 # a PDF page with less text than this is treated as a picture
OCR_TIMEOUT = 120


class ReaderError(ValueError):
    """The file cannot be read; the message says why and what to do about it."""


@dataclass
class PartLine:
    part: str
    colour: str
    qty: int
    confidence: float
    flags: list[str] = field(default_factory=list)
    text: str = ""                  # the source line, for the review screen
    page: int = 1

    @property
    def needs_review(self):
        return self.confidence < REVIEW_BELOW


@dataclass
class PageRead:
    method: str                     # "pdf-text" or "ocr"
    lines: list[PartLine]
    warnings: list[str] = field(default_factory=list)

    def to_csv(self, lines=None):
        out = io.StringIO()
        writer = csv.writer(out, lineterminator="\n")
        writer.writerow(["Item No", "Color", "Qty"])
        for line in self.lines if lines is None else lines:
            writer.writerow([line.part, line.colour, line.qty])
        return out.getvalue()

    def to_json(self):
        return dict(method=self.method, warnings=self.warnings, review_below=REVIEW_BELOW, csv=self.to_csv(),
                    lines=[dict(asdict(l), needs_review=l.needs_review) for l in self.lines])


# ------------------------------------------------------------------------------------------- text -> lines

_DIMENSIONS = re.compile(r"\b\d+\s*[x×]\s*\d+(?:\s*[x×]\s*\d+)?\b", re.IGNORECASE)
_LEADING_QTY = re.compile(r"^\W{0,3}(?:(\d{1,4})\s*[x×]|[x×]\s*(\d{1,4}))\s+", re.IGNORECASE)
_LEADING_BARE = re.compile(r"^\W{0,3}(\d{1,3})\s+(?=\d{3})")
_TRAILING_QTY = re.compile(r"\s[x×]\s*(\d{1,4})\W{0,3}$", re.IGNORECASE)
_BARE_QTY = re.compile(r"\s(\d{1,3})\W{0,3}$")
_PART = re.compile(r"(?<![\w.])(\d{3,6}[a-z]?(?:(?:pr|pb|pat|px|c|cpb|d|e)\d+[a-z]*)?)(?![\w.])", re.IGNORECASE)
_OCR_PART = re.compile(r"(?<![\w.])([0-9OolISB]{3,6}[a-z]?)(?![\w.])")
_LOOKALIKE = str.maketrans("OolISB", "001558")
_TOKEN = re.compile(r"[A-Za-z]+")


def _colour_in(text):
    """The longest run of 1-4 words in the text that is a BrickLink colour name, or None."""
    known = load_bricklink_colours()["by_name"]
    words = _TOKEN.findall(text)
    for size in (4, 3, 2, 1):
        for i in range(len(words) - size + 1):
            run = " ".join(words[i:i + size])
            if norm_colour_name(run) in known:
                return run
    return None


def _title(name):
    return " ".join(w.capitalize() for w in name.split())


def parse_line(raw, *, ocr=False, ceiling=1.0):
    """One text line as a PartLine, or None when it holds no part number and quantity."""
    text = " ".join(raw.split())
    if len(text) < 4:
        return None
    flags, score = [], ceiling
    body = _DIMENSIONS.sub(" ", text)                       # "Brick 2 x 4" is a name, not 2 pieces
    qty, explicit = None, False
    for pattern in (_LEADING_QTY, _TRAILING_QTY):
        found = pattern.search(body if pattern is not _LEADING_QTY else text)
        if found:
            qty, explicit = int(found.group(1) or found.group(2)), True
            body = (body[:found.start()] + " " + body[found.end():]) if pattern is _TRAILING_QTY else \
                _DIMENSIONS.sub(" ", text[found.end():])
            break
    if qty is None:
        found = _LEADING_BARE.search(text)
        if found:
            qty, body = int(found.group(1)), _DIMENSIONS.sub(" ", text[found.end():])
    part, fixed = None, False
    for found in _PART.finditer(body):
        if sum(c.isdigit() for c in found.group(1)) >= 3 and (qty is None or found.group(1) != str(qty)):
            part = found.group(1).lower()
            break
    if part is None and ocr:
        for found in _OCR_PART.finditer(body):
            candidate = found.group(1)
            if sum(c.isdigit() for c in candidate) >= 2 and candidate != candidate.translate(_LOOKALIKE):
                part, fixed = candidate.translate(_LOOKALIKE).lower(), True
                break
    if part is None:
        return None
    rest = body.replace(part, " ", 1) if not fixed else body
    if qty is None:
        found = _BARE_QTY.search(rest)
        if found:
            qty = int(found.group(1))
            rest = rest[:found.start()]
    if qty is None or qty < 1:
        return None
    colour = _colour_in(rest)
    if not explicit:
        flags.append("quantity has no x marker")
        score *= 0.75
    if colour is None:
        flags.append("no colour recognised")
        score *= 0.6
    if fixed:
        flags.append("part number repaired from look-alike characters")
        score *= 0.6
    if qty > 200:
        flags.append("unusually large quantity")
        score *= 0.5
    return PartLine(part=part, colour=_title(colour) if colour else "", qty=qty, confidence=round(score, 2), flags=flags, text=text)


def parse_text(text, *, ocr=False, page=1, confidences=None):
    """Part lines of a block of text. ``confidences`` optionally maps a line's text to its OCR confidence (0-1)."""
    lines = []
    for raw in text.splitlines():
        line = parse_line(raw, ocr=ocr, ceiling=(confidences or {}).get(" ".join(raw.split()), 1.0))
        if line is None:
            continue
        line.page = page
        if confidences and confidences.get(line.text, 1.0) < REVIEW_BELOW:
            line.flags.append("low OCR confidence")
        lines.append(line)
    return lines


# --------------------------------------------------------------------------------------------- extraction

def _kind(data):
    if data[:5] == b"%PDF-":
        return "pdf"
    if data[:8] == b"\x89PNG\r\n\x1a\n" or data[:3] == b"\xff\xd8\xff" or data[:4] in (b"II*\x00", b"MM\x00*") \
            or data[:2] == b"BM" or (data[:4] == b"RIFF" and data[8:12] == b"WEBP"):
        return "image"
    raise ReaderError("This is not a PDF, PNG, JPEG, TIFF, BMP or WebP file")


def _pdf_text_pages(path):
    """Text of each PDF page, with pypdf and then pdftotext; [] when neither is available."""
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(path))
        if len(reader.pages) > MAX_PAGES:
            raise ReaderError(f"The PDF has {len(reader.pages)} pages; cut it down to the parts pages (at most {MAX_PAGES})")
        return [(page.extract_text() or "") for page in reader.pages]
    except ImportError:
        pass
    except ReaderError:
        raise
    except Exception:                                       # damaged or encrypted: let pdftotext try
        pass
    if shutil.which("pdftotext"):
        done = subprocess.run(["pdftotext", "-layout", "-l", str(MAX_PAGES), str(path), "-"], capture_output=True, timeout=60)
        if done.returncode == 0:
            return done.stdout.decode("utf-8", "replace").split("\f")[:MAX_PAGES]
    return []


def ocr_available():
    return shutil.which("tesseract") is not None


def _need_ocr(why):
    return ReaderError(f"{why} Reading it needs OCR: install the tesseract command (for example `apt install tesseract-ocr`), "
                       "or export the parts list from the manufacturer's app as text.")


def _ocr_image(path):
    """(text, confidences by line text) from tesseract's TSV output."""
    done = subprocess.run(["tesseract", str(path), "stdout", "--psm", "6", "tsv"], capture_output=True, timeout=OCR_TIMEOUT)
    if done.returncode != 0:
        raise ReaderError("OCR failed: " + done.stderr.decode("utf-8", "replace").strip()[-300:])
    rows, order = {}, []
    for row in csv.DictReader(io.StringIO(done.stdout.decode("utf-8", "replace")), delimiter="\t", quoting=csv.QUOTE_NONE):
        word = (row.get("text") or "").strip()
        try:
            conf = float(row.get("conf", "-1"))
        except ValueError:
            continue
        if not word or conf < 0:
            continue
        key = (row["block_num"], row["par_num"], row["line_num"])
        if key not in rows:
            rows[key] = []
            order.append(key)
        rows[key].append((word, conf))
    lines, conf = [], {}
    for key in order:
        text = " ".join(w for w, _ in rows[key])
        lines.append(text)
        conf[text] = round(sum(c for _, c in rows[key]) / len(rows[key]) / 100, 2)
    return "\n".join(lines), conf


def _render_pdf(path, folder):
    """PNG paths of the PDF's pages: pypdfium2 if installed, else pdftoppm."""
    try:
        import pypdfium2
        pdf = pypdfium2.PdfDocument(str(path))
        out = []
        for i in range(min(len(pdf), MAX_PAGES)):
            target = Path(folder) / f"page-{i + 1}.png"
            pdf[i].render(scale=OCR_DPI / 72).to_pil().save(target)
            out.append(target)
        return out
    except ImportError:
        pass
    if shutil.which("pdftoppm"):
        subprocess.run(["pdftoppm", "-r", str(OCR_DPI), "-l", str(MAX_PAGES), "-png", str(path), str(Path(folder) / "page")],
                       capture_output=True, timeout=OCR_TIMEOUT, check=True)
        return sorted(Path(folder).glob("page-*.png"))
    raise _need_ocr("This PDF has no text layer, and no tool is installed to turn its pages into pictures (pypdfium2 or pdftoppm).")


def read_bytes(data, filename=""):
    """Read a PDF or image upload into a PageRead; ReaderError says what is wrong or missing."""
    if len(data) > MAX_BYTES:
        raise ReaderError(f"The file is larger than {MAX_BYTES // (1024 * 1024)} MB")
    kind = _kind(data)
    with tempfile.TemporaryDirectory(prefix="parts-page-") as folder:
        source = Path(folder) / ("page.pdf" if kind == "pdf" else "page.img")
        source.write_bytes(data)
        warnings = []
        if kind == "pdf":
            pages = _pdf_text_pages(source)
            lines = [l for n, text in enumerate(pages, 1) for l in parse_text(text, page=n)]
            if lines:
                return PageRead("pdf-text", lines, warnings)
            if any(len(t.strip()) >= MIN_TEXT_CHARS for t in pages):
                warnings.append("The PDF has text, but no line looks like 'quantity, part number, colour'; trying OCR as well.")
            if not ocr_available():
                raise _need_ocr("This PDF has no readable parts lines in its text layer." if pages else
                                "No PDF text reader (pypdf or pdftotext) is installed, and the text layer could not be checked.")
            images = _render_pdf(source, folder)
        else:
            if not ocr_available():
                raise _need_ocr("A photo has to be read as a picture.")
            images = [source]
        lines = []
        for n, image in enumerate(images, 1):
            text, conf = _ocr_image(image)
            lines += parse_text(text, ocr=True, page=n, confidences=conf)
        if not lines:
            warnings.append("No part lines were found. Try a sharper, straight-on picture of the parts list.")
        return PageRead("ocr", lines, warnings)


def read_file(path):
    path = Path(path)
    return read_bytes(path.read_bytes(), path.name)
