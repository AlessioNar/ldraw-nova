"""Set catalog: builder on synthetic Rebrickable CSVs, loader, search, ranking, bundled-model flag, committed data."""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

from ldraw_tools import inventory as inv
from ldraw_tools import sets

ROOT = Path(__file__).resolve().parent.parent
FIX = Path(__file__).parent / "fixtures"
spec = importlib.util.spec_from_file_location("build_set_catalog", ROOT / "tools" / "build_set_catalog.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)

FILES = {
    "themes": "id,name,parent_id\n1,Technic,\n2,Racers,1\n3,Gear,\n4,City,\n",
    "sets": ("set_num,name,year,theme_id,num_parts,img_url\n"
             "100-1,Red Racer,1999,2,4,\n"
             "200-1,Big City Set,2005,4,17,\n"
             "300-1,Key Chain,2010,3,1,\n"
             "400-1,Spare Only,2011,4,1,\n"
             "500-1,Odd Parts,2012,4,10,\n"
             "600-1,No Inventory,2013,4,5,\n"),
    "inventories": ("id,version,set_num\n1,1,100-1\n2,2,100-1\n3,1,200-1\n4,1,fig-1\n5,1,300-1\n6,1,400-1\n7,1,500-1\n"),
    "inventory_parts": ("inventory_id,part_num,color_id,quantity,is_spare,img_url\n"
                        "1,3001,4,1,False,\n"                      # superseded version 1: must be ignored
                        "2,3001,4,2,False,\n2,3001a,4,1,False,\n2,3003,1,1,False,\n2,3001,4,5,True,\n"
                        "3,3022,14,4,False,\n"
                        "4,3003,1,1,False,\n"
                        "5,3001,4,1,False,\n"
                        "6,3001,4,1,True,\n"
                        "7,3001,4,3,False,\n7,99999zz,4,2,False,\n7,3003,9999,4,False,\n7,77777,4,1,False,\n"),
    "inventory_sets": "inventory_id,set_num,quantity\n3,100-1,2\n",
    "inventory_minifigs": "inventory_id,fig_num,quantity\n3,fig-1,1\n",
}


@pytest.fixture
def rb(tmp_path, monkeypatch):
    monkeypatch.setenv("LDRAW_NOVA_RB_MAP_DIR", str(FIX / "maps"))
    inv.clear_map_cache()
    d = tmp_path / "rb"
    d.mkdir()
    for name, text in FILES.items():
        (d / f"{name}.csv").write_text(text)
    lib = tmp_path / "ldraw"
    (lib / "parts").mkdir(parents=True)
    for code in ("3001", "3003", "3022"):
        (lib / "parts" / f"{code}.dat").write_text("0 x\n")
    yield d, lib
    inv.clear_map_cache()


@pytest.fixture
def built(rb, tmp_path):
    d, lib = rb
    out = tmp_path / "cat.bin"
    assert builder.main([str(d), str(lib), "--out", str(out), "--source-date", "2026-01-01"]) == 0
    return sets.read_catalog(out), out


def test_builder_sets_and_noise(built):
    cat, _ = built
    assert [s["set_num"] for s in cat.sets] == ["100-1", "200-1", "500-1"]  # Gear, spare-only and inventory-less sets dropped
    assert cat.meta["dropped"] == {"noise theme Gear": 1, "no inventory or only spares": 2}
    assert cat.meta["source_date"] == "2026-01-01" and cat.meta["sets_in_source"] == 6
    assert cat.by_num["100-1"]["theme"] == "Technic > Racers" and cat.by_num["100-1"]["year"] == 1999


def test_inventory_version_alias_and_spares(built):
    cat, _ = built
    assert cat.by_num["100-1"]["inv_version"] == 2
    # version 2 only; spare row dropped; 3001a aliases to 3001 and merges with it
    assert sets.set_bom("100-1", cat) == {("3001", 4): 3, ("3003", 1): 1}


def test_minifig_and_subset_expansion(built):
    cat, _ = built
    # own 3022 x4 + minifig 3003 x1 + 2 x set 100-1 (3001 x3, 3003 x1 each)
    assert sets.set_bom("200-1", cat) == {("3022", 14): 4, ("3003", 1): 3, ("3001", 4): 6}
    assert cat.by_num["200-1"]["mapped_pieces"] == 13


def test_unmapped_counts(built):
    cat, _ = built
    s = cat.by_num["500-1"]
    assert sets.set_bom("500-1", cat) == {("3001", 4): 3}
    assert (s["dropped_part"], s["dropped_colour"], s["mapped_pieces"]) == (3, 4, 3)  # 99999zz + 77777 (no LDraw file), colour 9999


def test_deterministic_and_check(rb, built, tmp_path, capsys):
    d, lib = rb
    cat, out = built
    again = tmp_path / "again.bin"
    builder.main([str(d), str(lib), "--out", str(again), "--source-date", "2026-01-01"])
    assert again.read_bytes() == out.read_bytes()
    capsys.readouterr()
    args = [str(d), str(lib), "--out", str(out), "--source-date", "2026-01-01", "--check"]
    assert builder.main(args) == 0
    (d / "inventory_parts.csv").write_text(FILES["inventory_parts"] + "2,3003,4,1,False,\n")
    assert builder.main(args) == 1
    assert builder.main([str(d), str(lib), "--out", str(tmp_path / "missing.bin"), "--check"]) == 1


def test_builder_never_downloads_and_needs_inputs(tmp_path):
    with pytest.raises(SystemExit, match="never downloads"):
        builder.compute(tmp_path, None)


def test_get_set_and_search(built):
    cat, _ = built
    assert sets.get_set("100", cat)["name"] == "Red Racer"  # bare number means -1
    assert sets.get_set("nope-1", cat) is None
    with pytest.raises(KeyError):
        sets.set_bom("nope-1", cat)
    names = lambda **kw: [s["set_num"] for s in sets.search_sets(catalog=cat, **kw)]  # noqa: E731
    assert names() == ["500-1", "200-1", "100-1"]  # newest first
    assert names(query="racer technic") == ["100-1"]  # all words, name and theme
    assert names(query="racer city") == []
    assert names(theme="technic > racers") == ["100-1"]
    assert names(year=2005) == ["200-1"] and names(year=(2000, 2010)) == ["200-1"]
    assert names(min_parts=5) == ["500-1", "200-1"] and names(max_parts=4) == ["100-1"]
    assert names(limit=1) == ["500-1"]
    assert names(query="200-1") == ["200-1"]


def test_stats(built):
    cat, _ = built
    st = sets.catalog_stats(cat)
    assert (st["sets"], st["distinct_parts"], st["mapped_pieces"], st["dropped_part_pieces"], st["dropped_colour_pieces"]) == (3, 3, 20, 3, 4)
    assert st["mapped_share"] == pytest.approx(20 / 27)


OWNED = {("3001", 4): 2, ("3003", 1): 1, ("3022", 14): 9}


def test_rank_math_and_filters(built):
    cat, _ = built
    ranked = {r["set_num"]: r for r in sets.rank_sets(cat, OWNED, limit=0, bundled=set())}
    # 100-1 needs 3001x3 (own 2), 3003x1 (own 1): 3/4
    assert ranked["100-1"]["owned"] == 3 and ranked["100-1"]["needed"] == 4 and ranked["100-1"]["completeness"] == 0.75
    assert ranked["100-1"]["lines"] == 2 and ranked["100-1"]["lines_complete"] == 1
    # 200-1 needs 3022x4 (4), 3003x3 (1), 3001x6 (2): 7/13; owning more of 3022 than needed does not count extra
    assert ranked["200-1"]["owned"] == 7 and ranked["200-1"]["completeness"] == pytest.approx(7 / 13)
    assert ranked["500-1"]["owned"] == 2 and ranked["500-1"]["dropped_pieces"] == 7
    assert list(ranked) == ["100-1", "500-1", "200-1"]  # best first
    pick = lambda **kw: [r["set_num"] for r in sets.rank_sets(cat, OWNED, limit=0, bundled=set(), **kw)]  # noqa: E731
    assert pick(max_missing_share=0.3) == ["100-1"]
    assert pick(min_parts=11) == ["200-1"] and pick(max_parts=4) == ["100-1"]
    assert pick(theme="technic") == ["100-1"] and pick(query="big city") == ["200-1"]
    assert pick(min_completeness=0.7) == ["100-1"]
    assert sets.rank_sets(cat, OWNED, limit=1, bundled=set())[0]["set_num"] == "100-1"
    assert sets.rank_sets(cat, {("9999", 1): 5}, bundled=set()) == []


def test_rank_generic_path_matches_catalog_path(built):
    cat, _ = built
    pairs = ((s, sets.set_bom(s["set_num"], cat)) for s in cat.sets)
    assert sets.rank_sets(pairs, OWNED, limit=0, bundled=set()) == sets.rank_sets(cat, OWNED, limit=0, bundled=set())


def test_rank_completeness_is_the_check_model_measure(built):
    cat, _ = built
    needed = sets.set_bom("100-1", cat)
    owned = {("3001", 4): 2, ("3003", 1): 1}
    # inventory.check_model: ok = sum(min(need, have)); the report's ok_pieces / needed_total
    ok = sum(min(q, owned.get(k, 0)) for k, q in needed.items())
    assert sets.rank_sets(cat, owned, bundled=set())[0]["completeness"] == ok / sum(needed.values())


def test_bundled_models_flag(built, tmp_path):
    cat, _ = built
    models = tmp_path / "models"
    models.mkdir()
    for name in ("100-1.mpd", "200-1_B-Model-Truck.mpd", "readme.txt"):
        (models / name).write_text("0 x\n")
    sets._bundled.cache_clear()
    found = sets.bundled_set_nums(models)
    assert found == {"100-1", "200-1"}
    ranked = sets.rank_sets(cat, OWNED, limit=0, bundled=found)
    assert {r["set_num"]: r["has_model"] for r in ranked} == {"100-1": True, "200-1": True, "500-1": False}
    assert sets.bundled_set_nums(tmp_path / "absent") == frozenset()  # fails soft


def test_bundled_models_from_archive(tmp_path):
    import zipfile
    with zipfile.ZipFile(tmp_path / "models.zip", "w") as z:
        z.writestr("models/8448-1.mpd", "0 x\n")
    sets._bundled.cache_clear()
    assert sets.bundled_set_nums(tmp_path / "models") == {"8448-1"}


def test_cli_sets(built, tmp_path, monkeypatch):
    import os
    cat, out = built
    store = tmp_path / "store"
    store.mkdir()
    rows = [inv.Row("3001", 4, 2), inv.Row("3003", 1, 1)]
    (store / "mine.json").write_text(json.dumps(inv.Inventory(id="mine", name="mine", created_at=0.0, rows=rows).to_json()))
    env = dict(os.environ, LDRAW_NOVA_SET_CATALOG=str(out), LDRAW_NOVA_INVENTORY_DIR=str(store))

    def run(*args):
        return subprocess.run([sys.executable, "-m", "ldraw_tools.cli", "sets", *args], capture_output=True, text=True, env=env, cwd=ROOT)

    r = run("buildable", "mine", "--json", "--limit", "2")
    assert r.returncode == 0, r.stderr
    report = json.loads(r.stdout)
    assert [s["set_num"] for s in report["sets"]] == ["100-1", "500-1"] and report["sets"][0]["completeness"] == 0.75
    assert "100-1" in run("buildable", "mine", "--max-missing-share", "0.3").stdout
    assert json.loads(run("search", "racer", "--json").stdout)["sets"][0]["set_num"] == "100-1"
    shown = json.loads(run("show", "200-1", "--json").stdout)
    assert shown["distinct_lines"] == 3 and shown["dropped_part"] == 0
    assert "Rebrickable" in run("stats").stdout
    assert run("show", "999-1").returncode == 2


# ---------------------------------------------------------- committed catalog

@pytest.fixture(scope="module")
def real():
    return sets.read_catalog(sets.CATALOG_FILE)


def test_committed_catalog_structure(real):
    assert len(real) > 15000 and len(real.parts) > 4000
    assert real.meta["source"].startswith("Rebrickable") and real.meta["source_date"]
    for s in real.sets[:200] + real.sets[-200:]:
        assert s["set_num"] and s["mapped_pieces"] >= 0 and s["inv_version"] >= 1
        assert all(q > 0 and c >= 0 for _, c, q in real.lines(s["set_num"]))
    assert [s["set_num"] for s in real.sets] == sorted(s["set_num"] for s in real.sets)
    assert not any(s["theme"].split(" > ")[0] in ("Gear", "Books") for s in real.sets)


def test_committed_known_sets(real):
    s = sets.get_set("8448-1", real)
    assert s["name"] == "Super Street Sensation" and s["theme"] == "Technic" and s["year"] == 1999
    assert 1300 <= s["num_parts"] <= 1500 and s["mapped_pieces"] / s["num_parts"] > 0.95
    bom = sets.set_bom("8448-1", real)
    assert sum(bom.values()) == s["mapped_pieces"] and len(bom) > 100
    assert sets.get_set("10189-1", real)["name"] == "Taj Mahal"
    assert "10189-1" in [x["set_num"] for x in sets.search_sets("taj mahal", catalog=real)]
    assert sets.catalog_stats(real)["mapped_share"] > 0.9
