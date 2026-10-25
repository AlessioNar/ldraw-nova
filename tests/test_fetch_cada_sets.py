"""tools/fetch_cada_sets.py: parsing only. The tool's network path is never exercised by tests."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import fetch_cada_sets as f  # noqa: E402

ROWS = {"data": [
    [1, "C55029W", "", "Mazda 787B", "", "", "", "NORMAL", 242, 2024, 1],
    ["2-480-319", "CDPB0004W", "", "Display Box for C64008W", "", "", "", "NORMAL", "", 2026, 1],
    [1, "", "", "no number", "", "", "", "NORMAL", 5, 2020, 1],
]}


def test_parse_sets_keys_and_nulls():
    sets = f.parse_sets(ROWS)
    assert [s["set_num"] for s in sets] == ["C55029W", "CDPB0004W"]
    assert sets[0] == {"key": "cada:C55029W", "brand": "cada", "set_num": "C55029W", "name": "Mazda 787B",
                       "year": 2024, "pieces": 242}
    assert sets[1]["pieces"] is None


def test_data_file_url():
    html = '<table id=t data-type=brand data-file=/setdb/brand/cada/data.en.abc.json><thead>'
    assert f.data_file_url(html) == "https://www.merlinsbricks.com/setdb/brand/cada/data.en.abc.json"


def test_default_output_is_gitignored():
    ignore = (f.REPO / ".gitignore").read_text()
    assert "data/local/" in ignore
