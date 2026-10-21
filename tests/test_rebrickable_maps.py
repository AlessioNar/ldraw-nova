"""Structure and spot checks for the committed Rebrickable -> LDraw mapping data."""
import json
import re
from pathlib import Path

import pytest

DATA = Path(__file__).resolve().parent.parent / "ldraw_tools" / "data"
COLOURS = json.loads((DATA / "rebrickable-colours.json").read_text(encoding="utf8"))
PARTS = json.loads((DATA / "rebrickable-parts.json").read_text(encoding="utf8"))


def test_colour_file_structure():
    assert COLOURS["version"] == 1 and COLOURS["source"]
    cols = COLOURS["colours"]
    assert len(cols) > 200
    for rid, v in cols.items():
        assert re.fullmatch(r"-?\d+", rid)
        assert isinstance(v["ldraw"], int) and not isinstance(v["ldraw"], bool)
        assert v["name"] and v["method"] in ("override", "rgb", "name")
        assert isinstance(v["exact"], bool)
    assert all(re.fullmatch(r"-?\d+", u) for u in COLOURS["unmapped"])
    assert not set(COLOURS["unmapped"]) & set(cols)


def test_well_known_colours():
    cols = COLOURS["colours"]
    expected = {"0": 0, "1": 1, "2": 2, "4": 4, "14": 14, "15": 15, "71": 71, "72": 72, "70": 70, "47": 47}
    for rid, code in expected.items():
        assert cols[rid]["ldraw"] == code and cols[rid]["exact"], rid
    assert cols["85"]["ldraw"] == 85 and cols["85"]["method"] == "override"
    assert cols["1013"]["ldraw"] == 30001  # Modulex White must not collapse onto plain White
    assert cols["1062"]["exact"] is False
    assert "-1" in COLOURS["unmapped"]


def test_colour_codes_exist_in_ldconfig(official):
    from ldraw_tools.common import library_path
    text = (library_path() / "LDConfig.ldr").read_text(encoding="utf8", errors="replace")
    codes = {int(m) for m in re.findall(r"!COLOUR \S+\s+CODE\s+(\d+)", text)}
    missing = {rid: v["ldraw"] for rid, v in COLOURS["colours"].items() if v["ldraw"] not in codes}
    assert not missing


def test_part_file_structure():
    assert PARTS["version"] == 1 and PARTS["source"]
    aliases = PARTS["aliases"]
    assert len(aliases) > 1000
    for key, target in aliases.items():
        assert key and target
        assert target == target.lower() and not target.endswith(".dat")
        assert target != key and target != key.lower()
    assert isinstance(PARTS["no_ldraw"], list) and len(PARTS["no_ldraw"]) <= 500
    assert not set(PARTS["no_ldraw"]) & set(aliases)


def test_well_known_parts():
    aliases = PARTS["aliases"]
    assert aliases["3626cpr3662"] == "3626c"   # printed head -> plain head
    assert aliases["3002a"] == "3002"          # mold variant
    assert "3001" not in aliases               # identity is never listed


def test_alias_targets_exist_in_library(official):
    from ldraw_tools.common import library_path
    parts_dir = library_path() / "parts"
    missing = [t for t in set(PARTS["aliases"].values()) if not (parts_dir / f"{t}.dat").is_file()]
    assert not missing[:10]


@pytest.mark.parametrize("name", ["rebrickable-colours.json", "rebrickable-parts.json"])
def test_files_are_small_and_newline_terminated(name):
    raw = (DATA / name).read_bytes()
    assert len(raw) < 1_000_000 and raw.endswith(b"\n")
