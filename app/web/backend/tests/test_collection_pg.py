"""The parts collection on PostgreSQL (collection_pg.py): parity with the JSON files, the derived export the toolkit CLI
reads, undo and history, reservations, the importer (migrate_collection.py) and the storage switch.

Parity: one scenario of real collection operations runs once on the files and once on the database and must produce
the same API results. Skipped unless LDRAW_NOVA_TEST_DATABASE_URL points at a throwaway PostgreSQL (see conftest.py)."""
import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import func, select

import collection
import collection_pg
import collection_store
import inventories
import migrate_collection
import part_info
import quick_add
import reservations
import settings
from db.models import AppSetting, PartLog, PartNote, PartNoteLog, PartReservation, PartReview, PartRow, PartSource
from inventories import InventoryError
from test_collection import COLOURS, META

RAW = "Part,Color,Quantity\n3001,Red,2\n"
VOLATILE = {"ts", "created_at"}


@pytest.fixture(autouse=True)
def folder(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "INVENTORIES_DIR", tmp_path / "inventories")
    monkeypatch.setattr(settings, "COLLECTION_EXPORT_DIR", tmp_path / "export")
    monkeypatch.setattr(part_info, "meta", lambda: META)
    monkeypatch.setattr(part_info, "colour_names", lambda: COLOURS)
    monkeypatch.setattr(quick_add, "_popular", {})
    inventories.clear_caches()
    yield tmp_path
    collection_store.refresh()
    inventories.clear_caches()


@pytest.fixture
def pg(database):
    collection_store.refresh(force=True)
    inventories.clear_caches()
    return database


def rows(*items):
    return [dict(part=p, colour=c, qty=q, rb_part=p, rb_colour=0) for p, c, q in items]


def source(sid, items, **extra):
    return {"id": sid, "name": extra.pop("name", sid), "created_at": extra.pop("created_at", 1.5), "source_filename": None, "rows": rows(*items),
            "unmapped_parts": [], "unmapped_colours": [], "warnings": [], **extra}


def add(part, colour, qty):
    return {"op": "add", "part": part, "colour": colour, "qty": qty}


def seed():
    """Three sources written the way the app writes them: an import with its upload and log, one with "maybe" pieces and
    unmapped lines, a built set."""
    box = source("box", [("3001", 4, 5), ("3003", 1, 2), ("3022", 14, 4)], source_filename="box.csv",
                 **{"import": {"dialect": "rebrickable-csv", "total_rows": 3, "total_pieces": 11, "spare_pieces": 0}},
                 origin={"format": "rebrickable-csv"}, kind="import")
    box["rows"][1]["note"] = "a row note"
    pile = source("pile", [("3020", 4, 3)], unmapped_parts=[{"rb_part": "x", "colour": 4, "qty": 2}], review=[
        {"id": "r0", "part": "3001", "family": "Red", "candidates": [4, 320], "qty": 3, "brickit_part": "3001", "note": "scan 1"},
        {"id": "r1", "part": "3003", "family": "Blue", "candidates": [1], "qty": 2, "note": "scan 2"}], created_at=2.5)
    owned_set = source("set-1-1", [("3001", 4, 2), ("3710", 0, 6)], kind="set", created_at=3.5,
                       set={"set_num": "1-1", "brand": "LEGO", "name": "Tiny", "year": 2020, "theme": "T", "image": None, "num_parts": 8,
                            "copies": 1, "state": "built", "dropped_pieces": 0},
                       origin={"format": "set-catalog", "catalog": {"source": "test"}})
    collection_store.write_csv("box", RAW.encode())              # before the source exists, like collection._store_new
    for data in (box, pile, owned_set):
        with collection_store.lock:
            collection._save(data)
            collection._append_log(data["id"], collection._stamp([{"op": "create", "what": data.get("kind", "import")}], 1, "Created"))


def scrub(value):
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items() if k not in VOLATILE and not (k == "id" and str(v).startswith("res-"))}
    if isinstance(value, (list, tuple)):
        return [scrub(v) for v in value]
    if isinstance(value, float):
        return round(value, 4)
    return value


def scenario() -> dict:
    out = {}
    seed()
    out["sources"] = collection.sources()
    out["detail"] = inventories.detail("box", "", None, 50, 0)
    out["ops"] = collection.apply_ops("box", [add("3001", 4, 2), {"op": "set", "part": "3003", "colour": 1, "qty": 9},
                                             {"op": "remove", "part": "3022", "colour": 14}, add("3004", 7, 1)], note="edit")
    out["ops_noop"] = collection.apply_ops("box", [add("3001", 4, 0 + 1), {"op": "set", "part": "3001", "colour": 4, "qty": 8}])
    out["resolve"] = collection.apply_ops("pile", [{"op": "review_resolve", "id": "r0", "colour": 4, "qty": 2},
                                                   {"op": "review_discard", "id": "r1"}])
    out["history"] = collection.history("box")
    out["undo"] = collection.undo("box")
    out["undo_pile"] = collection.undo("pile")
    out["history_after"] = [collection.history("box"), collection.history("pile")]
    out["box_after"] = collection.get_source("box") if hasattr(collection, "get_source") else inventories.detail("box")
    out["pile_data"] = {k: v for k, v in copy.deepcopy(collection_store.load("pile")).items() if (k, v) != ("kind", "import")}
    out["notes"] = [collection.set_note("3001", "blue tub"), collection.set_note("3003", "drawer 2"), collection.set_note("3003", ""),
                    collection.get_notes()]
    out["reserve"] = reservations.reserve("truck-v1.mpd", {("3001", 4): 3, ("3022", 14): 9}, build="truck")
    out["reserve_again"] = reservations.reserve("truck-v1.mpd", {("3001", 4): 4}, build="truck")
    out["reserve_other"] = reservations.reserve("car-v1.mpd", {("3003", 1): 1}, build="car")
    out["listing"] = reservations.listing()
    out["available"] = sorted(reservations.available_totals()[0].items())
    out["available_ex"] = sorted(reservations.available_totals("truck")[0].items())
    out["shortage"] = reservations.shortage({("3001", 4): 20, ("3003", 1): 1})
    out["owned_sets"] = collection.owned_sets()
    out["set_state"] = reservations.set_state("set-1-1", "parts")
    out["available_unbuilt"] = sorted(reservations.available_totals()[0].items())
    out["released_model"] = reservations.release_model("car-v1.mpd")
    out["owned"] = sorted(collection_store.owned_totals()[0].items())
    out["exact"] = [collection_store.exact_rows(s) for s in ("box", "pile", "set-1-1", "nope")]
    out["csv"] = [collection_store.read_csv("box"), collection_store.read_csv("pile"), collection_store.read_csv("box", 3)]
    out["review_queue"] = collection.review_queue()
    out["part_detail"] = collection.part_detail("3001")
    out["quick"] = collection.quick_add([{"part": "3001", "colour": 4, "qty": 3}])
    out["rename"] = inventories.rename("box", "Box two")
    try:
        inventories.rename("pile", "box two")
        out["rename_clash"] = "allowed"
    except InventoryError as exc:
        out["rename_clash"] = exc.status
    inventories.delete("pile")
    for call in (lambda: inventories.delete("pile"), lambda: collection_store.load("pile")):
        try:
            call()
            out["gone"] = "no error"
        except InventoryError as exc:
            out["gone"] = exc.status
    out["ids_after"] = collection_store.source_ids()
    out["mappings"] = [collection_store.write_import_mapping("sig", {"part": "A", "colour": "B", "qty": "C", "system": "name"}),
                       collection_store.read_import_mappings()]
    out["final_sources"] = collection.sources()
    return scrub(out)


def test_parity_files_and_postgres_give_the_same_results(folder, database):
    collection_store.refresh(force=False)
    on_files = scenario()
    inventories.clear_caches()
    collection_store.refresh(force=True)
    on_pg = scenario()
    assert on_pg.keys() == on_files.keys()
    for key in on_files:
        assert on_pg[key] == on_files[key], key
    assert on_files["undo"]["undone"] == 3 and on_files["gone"] == 404 and on_files["rename_clash"] == 409


def test_a_source_round_trips_through_the_tables(pg):
    data = source("rt", [("3001", 4, 5), ("3003", 1, 2)], kind="import", schema=2, totals={"pieces": 7}, future_key={"a": [1, 2]},
                  review=[{"id": "r0", "part": "3001", "family": "Red", "candidates": [4], "qty": 1, "extra_field": "x"}])
    data["rows"][0]["note"] = "n"
    data["rows"].reverse()                                       # the order of the rows is kept
    collection_store.save(data)
    got = collection_store.load("rt")
    assert got["rows"] == data["rows"] and got["review"] == data["review"] and got["future_key"] == {"a": [1, 2]}
    assert got["totals"] == {"pieces": 7} and got["schema"] == 2 and got["created_at"] == data["created_at"]
    assert collection_store.load_copy("rt") == got and collection_store.load_copy("rt") is not collection_store.load("rt")
    with pytest.raises(InventoryError):
        collection_store.load("missing")


def test_edit_replaces_rows_and_drops_removed_review_items(pg):
    seed()
    collection.apply_ops("pile", [{"op": "review_discard", "id": "r0"}, add("3020", 4, 1)])
    with pg.session_scope() as session:
        assert session.scalar(select(func.count()).select_from(PartReview)) == 1
        assert session.scalar(select(PartRow.qty).where(PartRow.source_id == "pile", PartRow.part == "3020")) == 4


def test_name_must_stay_unique_and_delete_cascades(pg):
    seed()
    clash = source("other", [("3001", 1, 1)], name="BOX")
    with pytest.raises(InventoryError) as exc:
        collection_store.save(clash)
    assert exc.value.status == 409
    inventories.delete("box")
    with pg.session_scope() as session:
        assert session.scalar(select(func.count()).select_from(PartLog).where(PartLog.source_id == "box")) == 0
        assert session.scalar(select(func.count()).select_from(PartRow).where(PartRow.source_id == "box")) == 0


def test_log_keeps_the_last_500_batches(pg):
    seed()
    for batch in range(2, 520):
        collection_store.append_log("box", [{"ts": 1.0, "batch": batch, "op": "add"}])
    batches = sorted({e["batch"] for e in collection_store.read_log("box")})
    assert len(batches) == collection_store.MAX_BATCHES and batches[-1] == 519 and batches[0] == 20


def test_undo_and_history_after_a_replace_restore_rows_and_the_upload(pg):
    seed()
    old = copy.deepcopy(collection_store.load("box"))
    new = copy.deepcopy(old)
    new["rows"] = rows(("3001", 4, 1))
    collection.replace_source("box", new, b"Part,Color,Quantity\n3001,Red,1\n", name=None)
    assert collection_store.read_csv("box").endswith("1\n") and len(collection_store.load("box")["rows"]) == 1
    h = collection.history("box")
    assert h["batches"][0]["kind"] == "replace" and h["undo_batch"] == h["batches"][0]["batch"]
    collection.undo("box")
    assert collection_store.read_csv("box") == RAW
    assert [r["part"] for r in collection_store.load("box")["rows"]] == [r["part"] for r in old["rows"]]
    assert collection_store.load("box")["rows"][1]["note"] == "a row note"
    with pytest.raises(InventoryError) as exc:
        collection.undo("pile")
    assert exc.value.status == 409


def test_reservations_survive_in_order_and_signature_changes(pg):
    seed()
    assert collection_store.reservations_signature() == ""
    a = reservations.reserve("a-v1.mpd", {("3001", 4): 1})
    first = collection_store.reservations_signature()
    b = reservations.reserve("b-v1.mpd", {("3001", 4): 2})
    assert [r["id"] for r in collection_store.read_reservations()] == [a["id"], b["id"]]
    assert collection_store.reservations_signature() != first
    again = reservations.reserve("a-v1.mpd", {("3001", 4): 5})          # one reservation per model: replaced, moved last
    assert [r["id"] for r in collection_store.read_reservations()] == [b["id"], again["id"]]
    reservations.release(b["id"])
    assert [r["model"] for r in collection_store.read_reservations()] == ["a-v1.mpd"]
    with pytest.raises(InventoryError):
        reservations.release(b["id"])
    free, signature = reservations.available_totals()
    assert free[("3001", 4)] == 0 and "res:" in signature            # 5 owned + 2 in the built set - 2 held (built set) - 5


def test_owned_totals_signature_follows_edits(pg):
    seed()
    totals, first = collection_store.owned_totals()
    assert totals[("3001", 4)] == 7 and totals[("3710", 0)] == 6
    collection.apply_ops("box", [add("3001", 4, 1)])
    totals2, second = collection_store.owned_totals()
    assert totals2[("3001", 4)] == 8 and second != first


def test_the_notes_log_and_import_mappings_are_stored(pg):
    collection.set_note("3001", "blue tub")
    collection.set_note("3001", "red tub")
    with pg.session_scope() as session:
        assert session.scalar(select(func.count()).select_from(PartNoteLog)) == 2
        assert dict(session.execute(select(PartNote.part, PartNote.note)).all()) == {"3001": "red tub"}
    collection_store.write_import_mapping("sig", {"part": "A", "colour": "B", "qty": "C", "system": "name"})
    assert collection_store.read_import_mappings()["sig"]["qty"] == "C"


# ---------------------------------------------------------------- the derived export for the toolkit CLI

def exported(name):
    return json.loads((settings.COLLECTION_EXPORT_DIR / name).read_text(encoding="utf-8"))


def test_every_commit_updates_the_derived_json_copy(pg):
    export = settings.COLLECTION_EXPORT_DIR
    seed()
    assert sorted(p.name for p in export.glob("*.json")) == [".collection-reservations.json", "box.json", "pile.json", "set-1-1.json"] or \
        sorted(p.name for p in export.glob("*.json")) == ["box.json", "pile.json", "set-1-1.json"]
    for sid in collection_store.source_ids():
        assert exported(f"{sid}.json") == json.loads(json.dumps(collection_store.load(sid)))
    collection.apply_ops("box", [add("3001", 4, 5)])
    assert {(r["part"], r["colour"]): r["qty"] for r in exported("box.json")["rows"]}[("3001", 4)] == 10
    collection.set_note("3001", "tub")
    assert exported(".collection-notes.json")["notes"] == {"3001": "tub"}
    res = reservations.reserve("m-v1.mpd", {("3001", 4): 2})
    assert exported(".collection-reservations.json")["reservations"][0]["id"] == res["id"]
    reservations.release(res["id"])
    assert exported(".collection-reservations.json")["reservations"] == []
    inventories.rename("box", "Renamed box")
    assert exported("box.json")["name"] == "Renamed box"
    inventories.delete("pile")
    assert not (export / "pile.json").exists()
    assert not list(settings.INVENTORIES_DIR.glob("*")) if settings.INVENTORIES_DIR.exists() else True      # originals untouched


def test_sync_export_regenerates_everything_and_drops_stale_files(pg):
    seed()
    export = settings.COLLECTION_EXPORT_DIR
    (export / "box.json").write_text("{}", encoding="utf-8")
    (export / "stale.json").write_text("{}", encoding="utf-8")
    (export / "notes.txt").write_text("keep", encoding="utf-8")
    assert collection_pg.sync_export() == 3
    assert exported("box.json")["name"] == "box" and not (export / "stale.json").exists() and (export / "notes.txt").exists()
    before = (export / "box.json").stat().st_mtime_ns
    collection_pg.sync_export()
    assert (export / "box.json").stat().st_mtime_ns == before          # unchanged files are not rewritten


def test_cli_bridge_points_at_the_export_and_scratch_lives_there(pg):
    export = settings.COLLECTION_EXPORT_DIR
    assert collection_store.cli_store_args() == ["--store", str(export)]
    assert collection_store.cli_environment() == {"LDRAW_NOVA_INVENTORY_DIR": str(export)}
    scratch = collection_store.scratch_dir(".buildable-")
    assert scratch.parent == export and scratch.name.startswith(".buildable-")
    collection_store.refresh(force=False)
    assert collection_store.cli_store_args() == ["--store", str(settings.INVENTORIES_DIR)]


def _toolkit_python():
    for root in (Path("/opt/ldraw-nova"), Path(__file__).resolve().parents[4]):
        python = Path(os.environ.get("LDRAW_NOVA_TOOLKIT_PYTHON") or settings.TOOLKIT_PYTHON)
        if (root / "ldraw_tools").is_dir() and python.exists():
            probe = subprocess.run([str(python), "-c", "import ldraw_tools.inventory"], cwd=root, env={"PYTHONPATH": str(root)}, capture_output=True)
            if probe.returncode == 0:
                return python, root
    return None, None


def test_the_toolkit_cli_reads_the_export_without_database_access(pg, tmp_path):
    python, root = _toolkit_python()
    if python is None:
        pytest.skip("no toolkit interpreter that can import ldraw_tools")
    seed()
    reservations.reserve("m-v1.mpd", {("3001", 4): 3})
    env = {"PYTHONPATH": str(root), "PATH": os.environ.get("PATH", ""), "LDRAW_NOVA_INVENTORY_DIR": str(settings.COLLECTION_EXPORT_DIR)}
    listed = subprocess.run([str(python), "-m", "ldraw_tools.cli", "inventory", "list", *collection_store.cli_store_args(), "--json"],
                            cwd=tmp_path, env=env, capture_output=True, text=True)
    assert listed.returncode == 0, listed.stderr
    ids = {i["id"]: i["pieces"] for i in json.loads(listed.stdout)["inventories"]}
    assert ids == {"box": 11, "pile": 3, "set-1-1": 8}
    code = ("import json,sys; from pathlib import Path; from ldraw_tools.commands.inventory import load_inventory; "
            "from ldraw_tools.inventory import available_inventory; store = Path(sys.argv[1]); "
            "inv = available_inventory([load_inventory(store, 'box')], store / '.collection-reservations.json'); "
            "print(json.dumps({f'{r.part}/{r.colour}': r.qty for r in inv.rows}))")
    held = subprocess.run([str(python), "-c", code, str(settings.COLLECTION_EXPORT_DIR)], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert held.returncode == 0, held.stderr[-600:]
    assert json.loads(held.stdout)["3001/4"] == 2          # 5 owned, 3 held by the reservation


# ---------------------------------------------------------------- the importer

def write_files(folder: Path):
    folder.mkdir(parents=True, exist_ok=True)
    for sid, data in (("box", source("box", [("3001", 4, 5), ("3003", 1, 2)], source_filename="box.csv", kind="import", schema=2)),
                      ("pile", source("pile", [("3020", 4, 3)], created_at=2.5, review=[
                          {"id": "r0", "part": "3001", "family": "Red", "candidates": [4, 320], "qty": 3, "note": "scan 1"}])),
                      ("old", source("old", [("3001", 4, 1), ("3001", 4, 2)], created_at=0.5))):          # a repeated pair
        (folder / f"{sid}.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    (folder / "box.csv").write_text(RAW, encoding="utf-8")
    (folder / "box.log.jsonl").write_text(json.dumps({"ts": 1.0, "batch": 1, "op": "create", "what": "import"}) + "\n"
                                          + json.dumps({"ts": 2.0, "batch": 2, "op": "add", "part": "3001", "colour": 4, "before": 3, "after": 5,
                                                        "rows_before": []}) + "\n" + '{"torn', encoding="utf-8")
    (folder / ".collection-notes.json").write_text(json.dumps({"schema": 1, "notes": {"3001": "blue tub"}}), encoding="utf-8")
    (folder / ".collection-notes.log.jsonl").write_text(json.dumps({"ts": 1.0, "part": "3001", "before": "", "after": "blue tub"}) + "\n", encoding="utf-8")
    (folder / ".collection-reservations.json").write_text(json.dumps({"schema": 1, "reservations": [
        {"id": "res-aaaa", "model": "m-v1.mpd", "build": "m", "chat": None, "state": "built", "created_at": 5.0, "source": "bom",
         "rows": [["3001", 4, 2]]}]}), encoding="utf-8")
    (folder / ".collection-import-mappings.json").write_text(json.dumps({"schema": 1, "mappings": {"sig": {"part": "A", "colour": "B", "qty": "C", "system": "name"}}}), encoding="utf-8")


def snapshot(folder: Path) -> dict:
    return {p.name: p.read_bytes() for p in sorted(folder.iterdir()) if p.is_file()}


def test_dry_run_writes_nothing_commit_imports_verify_passes_and_it_is_idempotent(pg, folder):
    inv = settings.INVENTORIES_DIR
    write_files(inv)
    frozen = snapshot(inv)
    dry = migrate_collection.run_import(inv, dry_run=True)
    assert dry.ok and dry.counts["sources_imported"] == 3
    with pg.session_scope() as session:
        assert session.scalar(select(func.count()).select_from(PartSource)) == 0
    ok, reports = migrate_collection.import_and_verify(inv)
    commit, verified = reports[1], reports[2]
    assert ok and verified.ok, verified.mismatches
    assert commit.counts["sources_imported"] == 3 and commit.counts["rows_imported"] == 4 and commit.counts["pieces_imported"] == 13
    assert commit.counts["review_items_imported"] == 1 and commit.counts["log_entries_imported"] == 2
    assert commit.counts["notes_imported"] == 1 and commit.counts["reservations_imported"] == 1 and commit.counts["mappings_imported"] == 1
    assert any("repeated part+colour" in a for a in commit.anomalies) and any("box.log.jsonl" in a for a in commit.anomalies)
    assert verified.extra["matched"] == {"sources": 3, "rows": 4, "pieces": 13, "review_items": 1, "log_entries": 2, "notes": 1, "reservations": 1}
    assert collection_store.load("old")["rows"] == [dict(part="3001", colour=4, qty=3, rb_part="3001", rb_colour=0)]
    assert collection_store.read_csv("box") == RAW and collection_store.read_notes() == {"3001": "blue tub"}
    assert collection_store.read_reservations()[0]["id"] == "res-aaaa" and collection_store.read_import_mappings()["sig"]["qty"] == "C"
    with pg.session_scope() as session:
        assert session.scalar(select(AppSetting.value).where(AppSetting.key == "collection_import"))["done"] is True
    assert exported("box.json")["rows"][0]["qty"] == 5                       # the derived copy was written
    again = migrate_collection.run_import(inv, dry_run=False)
    assert again.counts.get("sources_imported", 0) == 0 and again.counts["sources_already_imported"] == 3
    assert not again.counts.get("notes_imported") and not again.counts.get("reservations_imported") and not again.counts.get("log_entries_imported")
    with pg.session_scope() as session:
        assert session.scalar(select(func.count()).select_from(PartLog)) == 2
        assert session.scalar(select(func.count()).select_from(PartReservation)) == 1
    assert snapshot(inv) == frozen                                          # the files were only read


def test_verify_finds_a_difference_and_tolerates_edits_made_in_the_app(pg):
    inv = settings.INVENTORIES_DIR
    write_files(inv)
    assert migrate_collection.import_and_verify(inv)[0]
    collection.apply_ops("box", [add("3001", 4, 1)])                        # edited after the import: its log is longer
    after_edit = migrate_collection.run_verify(inv)
    assert after_edit.ok and after_edit.counts["edited_since_import"] == 1
    with pg.session_scope() as session:
        session.query(PartRow).filter(PartRow.source_id == "pile").update({"qty": 99})
    broken = migrate_collection.run_verify(inv)
    assert not broken.ok and any("pile" in m for m in broken.mismatches)


def test_name_clash_is_reported_and_the_source_skipped(pg):
    inv = settings.INVENTORIES_DIR
    write_files(inv)
    collection_store.save(source("taken", [("3001", 1, 1)], name="BOX"))
    report = migrate_collection.run_import(inv, dry_run=False)
    assert report.counts["sources_imported"] == 2 and any("already has the name" in a for a in report.anomalies)
    assert not migrate_collection.run_verify(inv).ok


def test_export_writes_the_file_layout_back(pg, tmp_path):
    inv = settings.INVENTORIES_DIR
    write_files(inv)
    migrate_collection.import_and_verify(inv)
    target = tmp_path / "back"
    report = migrate_collection.Report("export")
    migrate_collection.export(target, False, report)
    assert json.loads((target / "box.json").read_text(encoding="utf-8"))["rows"] == collection_store.load("box")["rows"]
    assert (target / "box.csv").read_text(encoding="utf-8") == RAW and len((target / "box.log.jsonl").read_text().splitlines()) == 2
    assert json.loads((target / ".collection-notes.json").read_text())["notes"] == {"3001": "blue tub"}
    assert json.loads((target / ".collection-reservations.json").read_text())["reservations"][0]["id"] == "res-aaaa"
    again = migrate_collection.Report("export")
    migrate_collection.export(target, False, again)
    assert again.counts["files_written"] == 0 and again.anomalies                 # never overwrites without --force
    # the exported folder imports back to the same collection
    back = migrate_collection.Files(target, migrate_collection.Report("x"))
    assert set(back.sources) == {"box", "pile", "old"} and back.notes == {"3001": "blue tub"}


# ---------------------------------------------------------------- the storage switch

def test_auto_keeps_the_files_until_the_import_is_done(pg, monkeypatch):
    import storage
    monkeypatch.setattr(storage, "use_database", lambda: True)
    monkeypatch.setattr(storage, "mode", lambda: "auto")
    write_files(settings.INVENTORIES_DIR)
    assert collection_store.refresh() is False                               # files present, import not done
    migrate_collection.mark_done(migrate_collection.Report("x"))
    assert collection_store.refresh() is True
    monkeypatch.setattr(storage, "mode", lambda: "postgres")
    with pg.session_scope() as session:
        session.query(AppSetting).filter(AppSetting.key == "collection_import").delete()
    assert collection_store.refresh() is True                                # postgres means postgres
    monkeypatch.setattr(storage, "mode", lambda: "auto")
    for p in settings.INVENTORIES_DIR.glob("*.json"):
        p.unlink()
    assert collection_store.refresh() is True                                # nothing to import: a fresh install
    monkeypatch.setattr(storage, "use_database", lambda: False)
    assert collection_store.refresh() is False


# ---------------------------------------------------------------- no import cycle

@pytest.mark.parametrize("module", ["collection_store", "inventories", "toolkit", "understand_eval", "generator_bridge", "collection_pg"])
def test_modules_import_standalone(module):
    """Each module imports first in a fresh interpreter: no circular import between inventories, toolkit and the store."""
    backend = Path(__file__).resolve().parents[1]
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(backend), str(backend.parents[1])])}
    done = subprocess.run([sys.executable, "-I", "-c", f"import sys; sys.path[:0] = {[str(backend), str(backend.parents[1])]!r}; import {module}"],
                          cwd=backend.parent, env=env, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr[-800:]
