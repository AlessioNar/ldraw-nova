"""Parts list pages (PDF text, scanned PDF, photo) read into BrickLink CSV lines for the normal import route.

The pages are generated here: no real CaDA or LEGO page is available yet, so the patterns may need tuning once one is."""
import shutil

import pytest

from ldraw_tools import importers, partslist_reader as reader
from tests.test_bricklink_import import FIX, fixture_maps  # noqa: F401  (autouse: small colour and part tables)

PAGE = ["Parts list C590I3W", "3x  3001  Brick 2 x 4  Red", "12x 3023 Plate 1 x 2 Light Bluish Gray", "4x 99999 Mystery thing Blue",
        "2 3020 Plate 2 x 4 Black", "x 5 3069b Tile 1 x 2 White"]


def by_part(lines):
    return {l.part: l for l in lines}


def test_parse_text_reads_quantity_part_and_colour():
    got = by_part(reader.parse_text("\n".join(PAGE)))
    assert (got["3001"].qty, got["3001"].colour, got["3001"].confidence) == (3, "Red", 1.0)
    assert (got["3023"].qty, got["3023"].colour) == (12, "Light Bluish Gray")
    assert got["3069b"].qty == 5                         # trailing "x 5"


def test_dimensions_are_not_quantities_and_titles_are_skipped():
    lines = reader.parse_text("\n".join(PAGE))
    assert "C590I3W" not in {l.part for l in lines} and len(lines) == 5
    assert by_part(lines)["3001"].qty == 3               # not 2 from "2 x 4"


def test_uncertain_lines_are_flagged_for_review():
    got = by_part(reader.parse_text("\n".join(PAGE)))
    assert not got["3001"].needs_review
    assert got["3020"].needs_review and "quantity has no x marker" in got["3020"].flags
    assert got["99999"].colour == "Blue" and not got["99999"].needs_review
    odd = reader.parse_text("5x 3001 Brick 2 x 4 Zorblax")[0]
    assert odd.needs_review and "no colour recognised" in odd.flags


def test_ocr_lookalike_characters_are_repaired_and_flagged():
    line = reader.parse_text("4x 3O01 Brick Red", ocr=True)[0]
    assert line.part == "3001" and line.needs_review and any("look-alike" in f for f in line.flags)
    assert reader.parse_text("4x 3O01 Brick Red", ocr=False) == []


def test_low_ocr_confidence_flags_the_line():
    text = "3x 3001 Brick Red"
    line = reader.parse_text(text, ocr=True, confidences={text: 0.55})[0]
    assert line.needs_review and "low OCR confidence" in line.flags


def test_csv_goes_through_the_normal_import_route():
    page = reader.PageRead("pdf-text", reader.parse_text("\n".join(PAGE)))
    csv_text = page.to_csv()
    assert importers.detect(csv_text) == "bricklink-csv"
    result = importers.parse(csv_text)
    assert sum(r.qty for r in result.rows) >= 3


def test_not_a_pdf_or_image():
    with pytest.raises(reader.ReaderError, match="not a PDF"):
        reader.read_bytes(b"part,colour,qty\n")


def make_pdf(lines):
    canvas = pytest.importorskip("reportlab.pdfgen.canvas")
    import io
    out = io.BytesIO()
    c = canvas.Canvas(out)
    for i, text in enumerate(lines):
        c.drawString(72, 780 - i * 20, text)
    c.save()
    return out.getvalue()


def make_picture(lines, fmt="PNG"):
    image = pytest.importorskip("PIL.Image")
    draw, fonts = pytest.importorskip("PIL.ImageDraw"), pytest.importorskip("PIL.ImageFont")
    import io
    picture = image.new("RGB", (900, 60 + 60 * len(lines)), "white")
    pen = draw.Draw(picture)
    font = fonts.load_default(size=34)
    for i, text in enumerate(lines):
        pen.text((30, 30 + i * 60), text, fill="black", font=font)
    out = io.BytesIO()
    picture.save(out, fmt)
    return out.getvalue()


def test_pdf_text_layer_is_read_without_ocr(monkeypatch):
    pytest.importorskip("pypdf")
    monkeypatch.setattr(reader, "ocr_available", lambda: False)
    page = reader.read_bytes(make_pdf(PAGE))
    assert page.method == "pdf-text" and by_part(page.lines)["3023"].qty == 12


def test_pdf_without_text_asks_for_ocr_when_it_is_missing(monkeypatch):
    monkeypatch.setattr(reader, "_pdf_text_pages", lambda path: ["", ""])
    monkeypatch.setattr(reader, "ocr_available", lambda: False)
    with pytest.raises(reader.ReaderError, match="tesseract"):
        reader.read_bytes(make_pdf(PAGE))


def test_photo_without_ocr_says_what_to_install(monkeypatch):
    monkeypatch.setattr(reader, "ocr_available", lambda: False)
    with pytest.raises(reader.ReaderError, match="install the tesseract"):
        reader.read_bytes(make_picture(PAGE))


needs_tesseract = pytest.mark.skipif(shutil.which("tesseract") is None, reason="tesseract is not installed")


@needs_tesseract
def test_photo_is_read_by_ocr():
    page = reader.read_bytes(make_picture(PAGE[1:4]))
    got = by_part(page.lines)
    assert page.method == "ocr"
    assert got["3001"].qty == 3 and got["3023"].qty == 12 and "99999" in got
    assert reader.parse_text  # parsing stays shared with the PDF route


@needs_tesseract
def test_pdf_picture_is_read_by_ocr():
    pytest.importorskip("pypdfium2")
    image = pytest.importorskip("PIL.Image")
    import io
    out = io.BytesIO()
    image.open(io.BytesIO(make_picture(PAGE[1:4]))).save(out, "PDF")
    page = reader.read_bytes(out.getvalue())
    assert page.method == "ocr" and by_part(page.lines)["3001"].qty == 3
