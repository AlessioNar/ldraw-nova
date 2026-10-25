import json
import os
import subprocess
import sys

import pytest
from conftest import mpd, ref

from ldraw_tools import buildable
from ldraw_tools.inventory import (check_model, combine, index_owned, make_inventory, match_bom, model_bom,
                                   parse_rebrickable_csv, substituted_pieces)
from test_inventory import FIX, fixture_maps, make_model, model_text  # noqa: F401  (autouse fixture)


def inventory(name, csv):
    return make_inventory(name, parse_rebrickable_csv("Part,Color,Quantity\n" + csv))


def write(folder, name, *lines):
    (folder / name).write_text(mpd("\n".join(lines)))


@pytest.fixture
def models(tmp_path):
    folder = tmp_path / "models"
    folder.mkdir()
    bricks = [ref("3001.dat", 4, f"{i * 100} 0 0") for i in range(4)] + [ref("3003.dat", 1, "0 -24 0")]
    write(folder, "full-1.mpd", *bricks)
    write(folder, "full-1_Half.mpd", *bricks[:2])                       # fits inside its parent set model
    write(folder, "full-1_Other.mpd", *bricks[:2], ref("3022.dat", 14, "0 -48 0"))   # does not
    write(folder, "plates-1.mpd", *[ref("3022.dat", 14, f"{i * 50} 0 0") for i in range(6)])
    write(folder, "tiny-1.mpd", ref("3003.dat", 4))
    (folder / "broken-1.mpd").write_text("0 FILE broken-1.mpd\n1 not a line\n0 NOFILE\n")
    return folder


def scan(parts, models, tmp_path, **kw):
    return buildable.scan(parts, root=models, cache=tmp_path / "cache", workers=1, **kw)


def test_match_bom_core_is_what_check_model_reports(parts):
    model = make_model(model_text(ref("3001.dat", 4), ref("3001.dat", 4, "100 0 0"), ref("3003.dat", 1, "0 -24 0")), parts)
    have = inventory("h", "3001,4,1\n3001,1,5\n3003,1,1\n")
    needed = model_bom(model, parts)
    ok, short = match_bom(needed, combine([have]))
    assert ok == 2 and short == [("3001", 4, 2, 1)]
    report = check_model(model, [have], library=parts)
    assert report == check_model(model, [have], library=parts, needed=needed)
    assert report["ok_pieces"] == ok and [(s["part"], s["colour"], s["needed"], s["owned"]) for s in report["short"]] == short
    assert report["colour_substitutions"][0]["owned_colours"] == [dict(colour=1, qty=5)]


def test_substituted_pieces_pools_colours_per_part():
    needed = {("3001", 4): 3, ("3003", 1): 2, ("3022", 14): 1}
    owned = {("3001", 1): 2, ("3001", 4): 1, ("3003", 4): 9}
    assert substituted_pieces(needed, index_owned(owned)) == 3 + 2 + 0      # 3001: min(3, 3), 3003: min(2, 9), 3022 not owned
    assert match_bom(needed, owned)[0] == 1


def test_scan_builds_then_reuses_the_cache(parts, models, tmp_path):
    first = scan(parts, models, tmp_path)
    assert (first.total, first.built, first.reused, first.pending) == (6, 6, 0, [])
    assert [e.name for e in first.failed] == ["broken-1.mpd"] and "ValueError" in first.failed[0].error
    second = scan(parts, models, tmp_path)
    assert (second.built, second.reused) == (0, 6)
    assert {e.name: e.bom for e in first.entries} == {e.name: e.bom for e in second.entries}
    (models / "plates-1.mpd").write_text(mpd(ref("3022.dat", 14)))
    third = scan(parts, models, tmp_path)
    assert (third.built, third.reused) == (1, 5)
    assert scan(parts, models, tmp_path, build=False).pending == [] and scan(parts, models, tmp_path / "other", build=False).pending


def test_cache_survives_a_re_unpack_that_only_changes_mtimes(parts, models, tmp_path):
    scan(parts, models, tmp_path)
    for f in models.iterdir():
        os.utime(f, ns=(1_000_000_000, 1_000_000_000))
    again = scan(parts, models, tmp_path)
    assert (again.built, again.reused) == (0, 6)
    (models / "plates-1.mpd").write_text(mpd(ref("3022.dat", 14, "0 0 0")))     # different content: rebuilt
    assert scan(parts, models, tmp_path).built == 1


def test_budget_stops_the_build_and_a_rerun_resumes(parts, models, tmp_path):
    seen = []
    part = scan(parts, models, tmp_path, budget=0, progress=lambda done, total: seen.append((done, total)))
    assert part.built == 1 and len(part.pending) == 5 and seen[-1] == (1, 6)
    rest = scan(parts, models, tmp_path)
    assert (rest.built, rest.reused, rest.pending) == (5, 1, [])


def test_rank_complete_partial_and_filters(parts, models, tmp_path):
    entries = scan(parts, models, tmp_path).entries
    have = inventory("h", "3001,4,4\n3003,1,1\n3022,14,3\n")
    results, counts = buildable.rank(entries, [have], library=parts, min_parts=1, keep_subvariants=True)
    by_file = {r["file"]: r for r in results}
    assert counts["models"] == 6 and counts["parsed"] == 5 and counts["skipped"] == 1
    assert by_file["full-1.mpd"]["completeness"] == 1.0 and by_file["full-1.mpd"]["missing"] == 0 and by_file["full-1.mpd"]["top_missing"] == []
    plates = by_file["plates-1.mpd"]
    assert (plates["pieces"], plates["matched"], plates["missing"], plates["missing_lines"], plates["completeness"]) == (6, 3, 3, 1, 0.5)
    assert plates["top_missing"] == [dict(part="3022", colour=14, colour_name="Yellow", description="Plate 2 x 2", needed=6, owned=3, missing=3)]
    assert "tiny-1.mpd" not in by_file                                   # nothing owned in red 3003 (matched 0)
    assert [r["completeness"] for r in results] == sorted((r["completeness"] for r in results), reverse=True)
    # fewer pieces break a completeness tie after fewer missing pieces
    assert [r["file"] for r in results if r["completeness"] == 1.0] == ["full-1_Half.mpd", "full-1_Other.mpd", "full-1.mpd"]
    small, counts = buildable.rank(entries, [have], library=parts)                          # default --min-parts 20
    assert small == [] and counts["too_small"] == 5
    sized, counts = buildable.rank(entries, [have], library=parts, min_parts=5, max_parts=5, keep_subvariants=True)
    assert [r["file"] for r in sized] == ["full-1.mpd"] and counts["too_large"] == 1
    near, _ = buildable.rank(entries, [have], library=parts, min_parts=1, max_missing_share=0.2, keep_subvariants=True)
    assert {r["file"] for r in near} == {"full-1.mpd", "full-1_Half.mpd", "full-1_Other.mpd"}
    assert {r["file"] for r in buildable.rank(entries, [have], library=parts, min_parts=1, max_missing=0, keep_subvariants=True)[0]} \
        == {"full-1.mpd", "full-1_Half.mpd", "full-1_Other.mpd"}
    assert [r["file"] for r in buildable.rank(entries, [have], library=parts, min_parts=1, query="PLATES")[0]] == ["plates-1.mpd"]
    assert len(buildable.rank(entries, [have], library=parts, min_parts=1, limit=1)[0]) == 1


def test_subvariants_dropped_only_when_contained_in_the_parent(parts, models, tmp_path):
    entries = scan(parts, models, tmp_path).entries
    have = inventory("h", "3001,4,4\n3003,1,1\n3022,14,3\n")
    results, counts = buildable.rank(entries, [have], library=parts, min_parts=1)
    files = {r["file"] for r in results}
    assert "full-1_Half.mpd" not in files and "full-1_Other.mpd" in files and counts["subvariants"] == 1
    assert buildable.family("8448-1_Racer.mpd") == "8448-1.mpd" and buildable.family("8448-1.mpd") is None


def test_colour_substitution_is_reported_separately(parts, models, tmp_path):
    entries = scan(parts, models, tmp_path).entries
    have = inventory("h", "3001,4,2\n3001,1,2\n3003,14,1\n3022,14,6\n")      # blue stands in for red 3001, yellow for blue 3003
    exact, _ = buildable.rank(entries, [have], library=parts, min_parts=1, keep_subvariants=True)
    assert all("substituted_completeness" not in r for r in exact)
    both, _ = buildable.rank(entries, [have], library=parts, min_parts=1, substitute_colours=True, keep_subvariants=True)
    assert [(r["file"], r["completeness"]) for r in both] == [(r["file"], r["completeness"]) for r in exact]    # same exact ranking
    plates = next(r for r in both if r["file"] == "plates-1.mpd")
    assert plates["completeness"] == plates["substituted_completeness"] == 1.0
    full = next(r for r in both if r["file"] == "full-1.mpd")
    assert (full["matched"], full["completeness"], full["substituted_matched"], full["substituted_completeness"]) == (2, 0.4, 5, 1.0)


def run_cli(*args):
    env = dict(os.environ, LDRAW_NOVA_RB_MAP_DIR=str(FIX / "maps"))
    return subprocess.run([sys.executable, "-m", "ldraw_tools.cli", "inventory", *args], capture_output=True, text=True, env=env)


def test_cli_buildable(tmp_path, models, official):
    store = tmp_path / "store"
    csv_path = tmp_path / "mine.csv"
    csv_path.write_text("Part,Color,Quantity\n3001,4,4\n3003,1,1\n3022,14,3\n99999zz,4,10\n")
    assert run_cli("import", str(csv_path), "--name", "mine", "--store", str(store)).returncode == 0
    base = ["buildable", "mine", "--store", str(store), "--models-dir", str(models), "--cache-dir", str(tmp_path / "cache"), "--min-parts", "1", "--workers", "1"]
    cold = run_cli(*base, "--json", "--report", str(tmp_path / "r.json"))
    assert cold.returncode == 0, cold.stderr + cold.stdout
    report = json.loads(cold.stdout)
    assert json.loads((tmp_path / "r.json").read_text()) == report
    assert "buildable: 6/6 models" in cold.stderr
    assert report["counts"]["skipped"] == 1 and report["skipped"][0]["file"] == "broken-1.mpd" and report["pending"] == 0 and not report["partial"]
    assert report["unmapped_pieces"] == 10 and report["unmapped_share"] == round(10 / 18, 4)
    assert report["results"][0]["completeness"] == 1.0
    warm = run_cli(*base, "--json")
    assert json.loads(warm.stdout)["built"] == 0
    text = run_cli(*base, "--substitute-colours", "--max-missing-share", "0.6", "--limit", "2")
    assert text.returncode == 0 and "with colour substitution" in text.stdout and "could not be mapped" in text.stdout
    cached = run_cli("buildable", "mine", "--store", str(store), "--models-dir", str(models), "--cache-dir", str(tmp_path / "empty"), "--cache-only", "--json")
    assert json.loads(cached.stdout)["pending"] == 6 and json.loads(cached.stdout)["results"] == []
    assert run_cli("buildable", "--warm", "--models-dir", str(models), "--cache-dir", str(tmp_path / "warm"), "--workers", "1", "--json").returncode == 0
    assert run_cli("buildable", "--store", str(store)).returncode == 2
    assert run_cli("buildable", "mine", "--max-missing", "1", "--max-missing-share", "0.1", "--store", str(store)).returncode == 2
