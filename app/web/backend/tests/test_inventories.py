"""Parts inventories: storage, REST API, toolkit import and the per-chat parts options.

Imports run the real toolkit CLI as a subprocess. The interpreter comes from
LDRAW_NOVA_TOOLKIT_PYTHON (default: the toolkit venv); those tests skip when it cannot import
ldraw_tools. Tests that need the LDraw library skip without LDRAW_DIR.
"""
import asyncio
import json
import os
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import agent
import inventories
import llm_config
import model_catalog
import run_state
import sandbox
import settings
import toolkit
from main import app

REPO = Path(__file__).resolve().parents[4]
FIXTURES = REPO / "tests" / "fixtures"
DEV_PYTHON = "/tmp/claude-0/-home-user/c396e752-1ea1-5b53-b373-11c34417d8ad/scratchpad/cli-venv/bin/python"


def fixture_csv(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def inventory_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "INVENTORIES_DIR", tmp_path / "inventories")
    inventories.clear_caches()
    yield settings.INVENTORIES_DIR
    inventories.clear_caches()


@pytest.fixture
def toolkit_cli(tmp_path, monkeypatch):
    """Point the import subprocess at an interpreter that can run ldraw_tools from this checkout."""
    python = os.environ.get("LDRAW_NOVA_TOOLKIT_PYTHON") or (DEV_PYTHON if Path(DEV_PYTHON).exists() else "")
    if not python:
        pytest.skip("no toolkit interpreter (set LDRAW_NOVA_TOOLKIT_PYTHON)")
    probe = subprocess.run([python, "-c", "import ldraw_tools.inventory"], cwd=tmp_path, env={"PYTHONPATH": str(REPO)},
                           capture_output=True)
    if probe.returncode:
        pytest.skip("the toolkit interpreter cannot import ldraw_tools")
    fake_root = tmp_path / "toolkit"
    (fake_root / ".venv/bin").mkdir(parents=True)
    (fake_root / ".venv/bin/python").touch()
    (fake_root / "instructions.md").touch()
    monkeypatch.setattr(settings, "TOOLKIT_DIR", fake_root)
    monkeypatch.setattr(settings, "TOOLKIT_PYTHON", Path(python))
    real = toolkit.environment

    def environment():
        env = real()
        env["PYTHONPATH"] = f"{REPO}:{env['PYTHONPATH']}"
        return env
    monkeypatch.setattr(toolkit, "environment", environment)
    monkeypatch.setattr(sandbox, "SCRIPTS_DIR", "/nonexistent")


@pytest.fixture
def library(monkeypatch):
    path = os.environ.get("LDRAW_DIR") or os.environ.get("LEOCAD_LIB") or ""
    if not path or not (Path(path) / "parts").is_dir():
        pytest.skip("needs the LDraw library (set LDRAW_DIR)")
    import ldraw
    monkeypatch.setattr(settings, "LDRAW_DIR", Path(path))
    monkeypatch.setattr(settings, "INDEX_DIR", Path(path) / "nonexistent-index")
    ldraw.parts_index.cache_clear()
    ldraw.colours.cache_clear()
    inventories.clear_caches()
    yield
    ldraw.parts_index.cache_clear()
    ldraw.colours.cache_clear()
    inventories.clear_caches()


def cached(value):
    """A stand-in for one of the module's lru_cache'd lookups."""
    lookup = lambda: value  # noqa: E731
    lookup.cache_clear = lambda: None
    return lookup


def put(inventory_id: str, rows: list[tuple], name: str = "", spare: int = 0, **extra) -> None:
    """Store an inventory file directly: (part, colour, qty) rows."""
    data = {"id": inventory_id, "name": name or inventory_id, "created_at": 1000.0, "source_filename": f"{inventory_id}.csv",
            "rows": [{"part": p, "colour": c, "qty": q, "rb_part": p, "rb_colour": c} for p, c, q in rows],
            "unmapped_parts": [], "unmapped_colours": [], "warnings": [], "import": {"spare_pieces": spare}, **extra}
    inventories.atomic_write(settings.INVENTORIES_DIR / f"{inventory_id}.json", json.dumps(data).encode())
    (settings.INVENTORIES_DIR / f"{inventory_id}.csv").write_text("Part,Color,Quantity\n")


# --- import through the toolkit ---------------------------------------------------

def test_upload_database_dialect(client, toolkit_cli):
    r = client.post("/api/inventories", json={"name": "My Box", "csv": fixture_csv("rb_database.csv")})
    assert r.status_code == 200, r.text
    body = r.json()
    inv = body["inventory"]
    assert inv["id"] == "my-box" and inv["name"] == "My Box"
    assert inv["source_filename"] == "my-box.csv"
    assert (inv["distinct_colours"], inv["spare_pieces"], inv["unmapped_colours"]) == (2, 1, 1)
    report = body["report"]
    # With the library the made-up part 99999zz is unmapped too; without it nothing is checked.
    assert inv["total_pieces"] + sum(u["qty"] for u in report["unmapped_parts"]) + 2 == 14
    assert inv["distinct_parts"] == 3 - inv["unmapped_parts"]
    assert report["dialect"] == "rebrickable-database" and report["total_rows"] == 5
    assert report["total_pieces"] == 14 and report["spare_pieces"] == 1
    assert report["unmapped_colours"] == [{"rb_colour": 9999, "part": "3001", "qty": 2}]
    assert isinstance(report["warnings"], list)
    stored = settings.INVENTORIES_DIR
    assert json.loads((stored / "my-box.json").read_text())["rows"][0]["part"] == "3001"
    assert (stored / "my-box.csv").read_text() == fixture_csv("rb_database.csv")
    assert not [p for p in stored.iterdir() if p.name.startswith(".")]            # no import scratch left behind
    assert client.get("/api/inventories").json()["inventories"] == [inv]


def test_upload_parts_list_dialect_and_spares(client, toolkit_cli):
    csv = fixture_csv("rb_parts_list_spare_bom.csv")
    plain = client.post("/api/inventories", json={"name": "plain", "csv": csv}).json()
    spares = client.post("/api/inventories", json={"name": "with spares", "csv": csv, "include_spares": True}).json()
    assert plain["report"]["dialect"] == "rebrickable-parts-list"
    assert plain["report"]["spare_pieces"] > 0
    assert spares["inventory"]["total_pieces"] == plain["inventory"]["total_pieces"] + plain["report"]["spare_pieces"]


def test_upload_semicolon_dialect(client, toolkit_cli):
    r = client.post("/api/inventories", json={"name": "semi", "csv": fixture_csv("rb_semicolon.csv")})
    assert r.status_code == 200 and r.json()["inventory"]["total_pieces"] > 0


@pytest.mark.parametrize("fixture", ["malformed_header.csv", "malformed_negative.csv", "malformed_quantity.csv"])
def test_upload_bad_csv_is_400(client, toolkit_cli, fixture):
    r = client.post("/api/inventories", json={"name": "bad", "csv": fixture_csv(fixture)})
    assert r.status_code == 400 and r.json()["detail"]
    assert not list(settings.INVENTORIES_DIR.glob("*.json"))


def test_upload_empty_csv_is_400(client, toolkit_cli):
    r = client.post("/api/inventories", json={"name": "empty", "csv": "   \n"})
    assert r.status_code == 400 and "empty" in r.json()["detail"].lower()


def test_upload_duplicate_is_409_by_slug(client, toolkit_cli):
    csv = fixture_csv("rb_parts_list.csv")
    assert client.post("/api/inventories", json={"name": "Red Bricks", "csv": csv}).status_code == 200
    for name in ("Red Bricks", "red  bricks!", "RED-BRICKS"):
        r = client.post("/api/inventories", json={"name": name, "csv": csv})
        assert r.status_code == 409 and "already exists" in r.json()["detail"]
    assert len(client.get("/api/inventories").json()["inventories"]) == 1


@pytest.mark.parametrize("name", ["", "   ", "x" * 81, "bad\nname"])
def test_upload_invalid_name_is_400(client, name):
    r = client.post("/api/inventories", json={"name": name, "csv": "Part,Color,Quantity\n3001,4,1\n"})
    assert r.status_code == 400 and r.json()["detail"]


def test_upload_name_of_exactly_80_chars_is_accepted(client, toolkit_cli):
    r = client.post("/api/inventories", json={"name": "n" * 80, "csv": fixture_csv("rb_parts_list.csv")})
    assert r.status_code == 200 and len(r.json()["inventory"]["id"]) <= 60


def test_upload_over_25_mb_is_413_before_parsing(client, monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError("an oversize upload must not reach the parser")
    monkeypatch.setattr(sandbox, "run", forbidden)
    csv = "Part,Color,Quantity\n" + "3001,4,1\n" * (inventories.MAX_CSV_BYTES // 9 + 10)
    r = client.post("/api/inventories", json={"name": "huge", "csv": csv})
    assert r.status_code == 413
    # Over the request limit of the middleware: refused before the route runs.
    r = client.post("/api/inventories", content=b"x" * (28 * 1024 * 1024), headers={"content-type": "application/json"})
    assert r.status_code == 413 and "detail" in r.json()


def test_upload_requires_same_origin(client):
    r = client.post("/api/inventories", json={"name": "x", "csv": "y"}, headers={"origin": "https://evil.example"})
    assert r.status_code == 403


def test_upload_missing_fields_is_422(client):
    assert client.post("/api/inventories", json={"name": "x"}).status_code == 422


def test_import_failure_modes(client, monkeypatch):
    """Subprocess problems are 5xx with a plain message, never a crash."""
    def runner(report, exit_code=0, timed_out=False):
        async def run(argv, cwd, timeout, **kwargs):
            if report is not None:
                (cwd / "report.json").write_text(json.dumps(report))
            return sandbox.RunResult(exit_code, "", "boom", timed_out, 0.1)
        return run
    body = {"name": "fails", "csv": "Part,Color,Quantity\n3001,4,1\n"}
    monkeypatch.setattr(sandbox, "run", runner(None))
    assert client.post("/api/inventories", json=body).status_code == 500
    monkeypatch.setattr(sandbox, "run", runner({"error": "disk", "error_type": "OSError"}, exit_code=2))
    r = client.post("/api/inventories", json=body)
    assert r.status_code == 500 and "disk" not in r.text
    monkeypatch.setattr(sandbox, "run", runner({"error": "slow"}, timed_out=True))
    assert client.post("/api/inventories", json=body).status_code == 504
    monkeypatch.setattr(sandbox, "run", runner({"inventory": {"id": "other"}}))
    assert client.post("/api/inventories", json=body).status_code == 500
    assert not list(settings.INVENTORIES_DIR.glob("*.json"))
    assert not [p for p in settings.INVENTORIES_DIR.iterdir() if p.name.startswith(".import-")]


def test_import_command_is_a_bounded_argv_list(client, monkeypatch):
    seen = {}

    async def run(argv, cwd, timeout, **kwargs):
        seen.update(argv=argv, timeout=timeout, env=kwargs.get("env_extra"))
        (cwd / "report.json").write_text(json.dumps({"error": "x", "error_type": "ParseError"}))
        return sandbox.RunResult(2, "", "", False, 0.1)
    monkeypatch.setattr(sandbox, "run", run)
    name = "--evil; rm -rf /"
    r = client.post("/api/inventories", json={"name": name, "csv": "Part,Color,Quantity\n3001,4,1\n"})
    assert r.status_code == 400
    assert f"--name={name}" in seen["argv"] and seen["timeout"] == inventories.IMPORT_TIMEOUT
    assert seen["env"]["LDRAW_NOVA_INVENTORY_DIR"] == str(settings.INVENTORIES_DIR)


@pytest.mark.usefixtures("library")
def test_upload_with_library_checks_parts_and_detail_has_names(client, toolkit_cli):
    csv = "Part,Color,Quantity\n3001,4,3\n3001,1,2\nnot-a-real-part,4,9\n"
    r = client.post("/api/inventories", json={"name": "lib", "csv": csv}).json()
    assert r["inventory"]["unmapped_parts"] == 1
    assert r["report"]["unmapped_parts"] == [{"rb_part": "not-a-real-part", "colour": 4, "qty": 9}]
    detail = client.get("/api/inventories/lib", params={"query": "brick 2 x 4"}).json()
    assert detail["rows"][0] == {"part": "3001", "colour": 4, "colour_name": "Red", "qty": 3, "description": "Brick 2 x 4"}
    assert {c["colour_name"] for c in detail["top_colours"]} == {"Red", "Blue"}


# --- listing, detail, rename, delete ---------------------------------------------------

@pytest.fixture
def stocked(monkeypatch):
    names = {4: "Red", 1: "Blue", 14: "Yellow"}
    monkeypatch.setattr(inventories, "_descriptions", cached({"3001": "Brick 2 x 4", "3003": "Brick 2 x 2", "3022": "Plate 2 x 2"}))
    monkeypatch.setattr(inventories, "_colour_names", cached(names))
    rows = [("3001", 4, 10), ("3001", 1, 3), ("3003", 4, 5), ("3022", 14, 5), ("3022", 4, 5), ("9999", 4, 1)]
    put("box", rows, name="Box", spare=2, unmapped_parts=[{"rb_part": "x", "colour": 4, "qty": 1}])
    return rows


def test_list_summary_counts(client, stocked):
    [inv] = client.get("/api/inventories").json()["inventories"]
    assert inv == {"id": "box", "name": "Box", "created_at": 1000.0, "source_filename": "box.csv", "distinct_parts": 4,
                   "distinct_colours": 3, "total_pieces": 29, "spare_pieces": 2, "unmapped_parts": 1, "unmapped_colours": 0, "kind": "import", "schema": 1}


def test_list_empty_without_directory(client):
    assert client.get("/api/inventories").json() == {"inventories": []}


def test_list_skips_unreadable_files(client, stocked):
    (settings.INVENTORIES_DIR / "broken.json").write_text("{nope")
    (settings.INVENTORIES_DIR / "Not A Slug.json").write_text("{}")
    assert [i["id"] for i in client.get("/api/inventories").json()["inventories"]] == ["box"]


def test_detail_sorted_and_paged(client, stocked):
    body = client.get("/api/inventories/box").json()
    assert body["total"] == 6 and body["inventory"]["id"] == "box"
    assert [(r["part"], r["colour"], r["qty"]) for r in body["rows"]] == [
        ("3001", 4, 10), ("3003", 4, 5), ("3022", 4, 5), ("3022", 14, 5), ("3001", 1, 3), ("9999", 4, 1)]
    assert body["rows"][0] == {"part": "3001", "colour": 4, "colour_name": "Red", "qty": 10, "description": "Brick 2 x 4"}
    assert body["rows"][-1]["description"] == "" and body["rows"][-1]["colour_name"] == "Red"
    page = client.get("/api/inventories/box", params={"limit": 2, "offset": 2}).json()
    assert page["total"] == 6 and [r["part"] for r in page["rows"]] == ["3022", "3022"]
    assert client.get("/api/inventories/box", params={"offset": 99}).json()["rows"] == []
    assert len(client.get("/api/inventories/box", params={"limit": 100000}).json()["rows"]) == 6
    assert len(client.get("/api/inventories/box", params={"limit": 0}).json()["rows"]) == 1      # clamped to 1


def test_detail_limit_is_capped_at_200(client):
    put("many", [(f"p{i}", 4, 1) for i in range(300)])
    assert len(client.get("/api/inventories/many", params={"limit": 500}).json()["rows"]) == 200
    assert len(client.get("/api/inventories/many").json()["rows"]) == 50


def test_detail_search_words_and_colour(client, stocked):
    def parts(**params):
        body = client.get("/api/inventories/box", params=params).json()
        return body["total"], [(r["part"], r["colour"]) for r in body["rows"]]
    assert parts(query="brick") == (3, [("3001", 4), ("3003", 4), ("3001", 1)])
    assert parts(query="BRICK 3003") == (1, [("3003", 4)])               # every word, any case, part and description
    assert parts(query="3022") == (2, [("3022", 4), ("3022", 14)])        # part number
    assert parts(query="plate", colour=14) == (1, [("3022", 14)])
    assert parts(colour=1) == (1, [("3001", 1)])
    assert parts(query="nothing here") == (0, [])
    assert client.get("/api/inventories/box", params={"colour": "red"}).status_code == 422


def test_detail_top_colours_ignore_filters_and_cap_at_12(client, stocked):
    body = client.get("/api/inventories/box", params={"query": "plate", "colour": 14}).json()
    assert body["top_colours"] == [{"colour": 4, "colour_name": "Red", "qty": 21},
                                   {"colour": 14, "colour_name": "Yellow", "qty": 5},
                                   {"colour": 1, "colour_name": "Blue", "qty": 3}]
    put("rainbow", [("3001", c, 20 - c) for c in range(15)])
    top = client.get("/api/inventories/rainbow").json()["top_colours"]
    assert len(top) == 12 and top[0]["colour"] == 0 and top[0]["colour_name"] == ""


def test_unknown_inventory_is_404(client):
    assert client.get("/api/inventories/missing").status_code == 404
    assert client.patch("/api/inventories/missing", json={"name": "x"}).status_code == 404
    assert client.delete("/api/inventories/missing").status_code == 404
    assert client.get("/api/inventories/missing").json() == {"detail": "no such inventory"}


@pytest.mark.parametrize("bad", ["..", "%2e%2e", "..%2f..%2fetc%2fpasswd", "a%2fb", "a%00b", "UPPER", "-lead", "x" * 61, ".hidden", "a.json"])
def test_path_traversal_ids_are_refused(client, stocked, bad):
    # Some of these never reach the route (the URL normalises to another path); none may touch a file.
    assert '"rows"' not in client.get(f"/api/inventories/{bad}").text
    assert client.delete(f"/api/inventories/{bad}").status_code in (404, 405, 422)
    assert client.patch(f"/api/inventories/{bad}", json={"name": "x"}).status_code in (404, 405, 422)
    assert (settings.INVENTORIES_DIR / "box.json").is_file()


def test_id_validation_helpers(tmp_path):
    assert inventories.valid_id("box-2") and not inventories.valid_id("../x") and not inventories.valid_id("")
    assert not inventories.valid_id(None) and not inventories.valid_id("a/b") and not inventories.valid_id("A")
    with pytest.raises(inventories.InventoryError):
        inventories._path("../../etc/passwd")
    assert inventories.slugify("My  Box!") == "my-box"
    assert inventories.valid_id(inventories.slugify("x" * 200)) and inventories.slugify("***") == "inventory"


def test_rename_keeps_id_and_files(client, stocked):
    r = client.patch("/api/inventories/box", json={"name": "  Big Box "})
    assert r.status_code == 200 and r.json()["inventory"]["id"] == "box" and r.json()["inventory"]["name"] == "Big Box"
    assert client.get("/api/inventories/box").json()["inventory"]["name"] == "Big Box"
    assert json.loads((settings.INVENTORIES_DIR / "box.json").read_text())["rows"]          # data intact
    assert (settings.INVENTORIES_DIR / "box.csv").is_file()
    assert client.patch("/api/inventories/box", json={"name": "BOX"}).status_code == 200      # own slug is not a clash


def test_rename_conflicts_and_bad_names(client, stocked):
    put("other", [("3001", 4, 1)], name="Other")
    assert client.patch("/api/inventories/box", json={"name": "other"}).status_code == 409
    assert client.patch("/api/inventories/box", json={"name": "Other!"}).status_code == 409
    for name in ("", "  ", "x" * 81):
        assert client.patch("/api/inventories/box", json={"name": name}).status_code == 400
    assert client.patch("/api/inventories/box", json={}).status_code == 422
    assert client.get("/api/inventories/box").json()["inventory"]["name"] == "Box"


def test_delete_removes_both_files(client, stocked):
    assert client.delete("/api/inventories/box").json() == {"ok": True}
    assert not list(settings.INVENTORIES_DIR.iterdir())
    assert client.get("/api/inventories/box").status_code == 404
    assert client.delete("/api/inventories/box").status_code == 404


def test_atomic_write_leaves_old_file_on_failure(tmp_path, monkeypatch):
    target = tmp_path / "x.json"
    inventories.atomic_write(target, b"old")
    assert target.read_bytes() == b"old" and (target.stat().st_mode & 0o777) == 0o644

    def broken(self, other):
        raise OSError("disk full")
    monkeypatch.setattr(Path, "replace", broken)
    with pytest.raises(OSError):
        inventories.atomic_write(target, b"new")
    monkeypatch.undo()
    assert target.read_bytes() == b"old"
    assert [p.name for p in tmp_path.iterdir()] == ["x.json"]                  # no temp file left


def test_atomic_write_creates_the_directory(tmp_path):
    inventories.atomic_write(tmp_path / "deep" / "er" / "x.json", b"{}")
    assert (tmp_path / "deep" / "er" / "x.json").read_bytes() == b"{}"


def test_toolkit_environment_points_at_the_inventory_folder(monkeypatch, tmp_path):
    (tmp_path / ".venv/bin").mkdir(parents=True)
    (tmp_path / ".venv/bin/python").touch()
    (tmp_path / "instructions.md").touch()
    monkeypatch.setattr(settings, "TOOLKIT_DIR", tmp_path)
    assert toolkit.environment()["LDRAW_NOVA_INVENTORY_DIR"] == str(settings.INVENTORIES_DIR)


def test_inventory_folder_is_never_served(client, stocked):
    assert "inventories" not in settings.WEB_DIRS
    assert client.get("/files/inventories/box.json").status_code == 404


# --- chat options -----------------------------------------------------------------------

ENTRY = {"litellm_params": {"model": "openai/gpt-6-luna", "api_key": "sk-x"}}


def options(**given):
    return model_catalog.validate_options(ENTRY, given)


def test_parts_options_default_off():
    value = options()
    assert value["parts_mode"] == "off" and value["inventory_ids"] == []
    assert inventories.parts_options(value) == ("off", [])
    assert inventories.parts_options(None) == ("off", [])
    assert inventories.parts_options({"parts_mode": "bogus", "inventory_ids": ["a"]}) == ("off", [])


def test_parts_options_accepted(stocked):
    put("two", [("3001", 4, 1)])
    value = options(parts_mode="only", inventory_ids=["box", "two", "box"])
    assert (value["parts_mode"], value["inventory_ids"]) == ("only", ["box", "two"])         # de-duplicated, ordered
    assert inventories.parts_options(value) == ("only", ["box", "two"])
    assert options(parts_mode="prefer", inventory_ids=["box"])["parts_mode"] == "prefer"
    assert options(parts_mode=None, inventory_ids=None)["parts_mode"] == "off"


@pytest.mark.parametrize("given, message", [
    ({"parts_mode": "always"}, "Unknown parts mode"),
    ({"parts_mode": 3}, "Unknown parts mode"),
    ({"parts_mode": "prefer"}, "at least one inventory"),
    ({"parts_mode": "only", "inventory_ids": []}, "at least one inventory"),
    ({"parts_mode": "prefer", "inventory_ids": "box"}, "list of inventory ids"),
    ({"parts_mode": "prefer", "inventory_ids": [1]}, "list of inventory ids"),
    ({"inventory_ids": {"box": 1}}, "list of inventory ids"),
    ({"parts_mode": "prefer", "inventory_ids": ["box", "ghost"]}, "'ghost' no longer exists"),
    ({"parts_mode": "prefer", "inventory_ids": ["../etc"]}, "Invalid inventory id"),
    ({"inventory_ids": ["Bad Id"]}, "Invalid inventory id"),
    ({"parts_mode": "prefer", "inventory_ids": [f"inv-{i}" for i in range(21)]}, "at most 20"),
])
def test_parts_options_errors(stocked, given, message):
    with pytest.raises(ValueError, match=message):
        options(**given)


def test_twenty_inventories_are_allowed():
    ids = [f"inv-{i}" for i in range(20)]
    for i in ids:
        put(i, [("3001", 4, 1)])
    assert options(parts_mode="prefer", inventory_ids=ids)["inventory_ids"] == ids


def test_unknown_ids_are_dropped_when_off(stocked):
    assert options(parts_mode="off", inventory_ids=["box", "ghost"])["inventory_ids"] == ["box"]


def post_message(client, chat_id, **opts):
    return client.post(f"/api/chats/{chat_id}/messages", json={"text": "build", "llm_model_id": opts.pop("model"), "options": opts})


@pytest.fixture
def model_id():
    return llm_config.create({"model_name": "fake", "litellm_params": {"model": "openai/gpt-6-luna", "api_key": "sk-x"},
                              "capabilities": {"tools": True, "vision": True}})["id"]


def test_message_with_bad_parts_options_is_400(client, stocked, model_id, store):
    chat = store.create_chat()
    for opts, message in [({"parts_mode": "sometimes"}, "Unknown parts mode"),
                          ({"parts_mode": "only"}, "at least one inventory"),
                          ({"parts_mode": "prefer", "inventory_ids": ["ghost"]}, "'ghost' no longer exists")]:
        r = post_message(client, chat["id"], model=model_id, **opts)
        assert r.status_code == 400 and message in r.json()["detail"]
    assert store.messages(chat["id"]) == []                                  # nothing started or recorded


def test_turn_persists_and_delivers_parts_options(stocked, model_id, store, monkeypatch):
    seen = {}

    async def noop(self_store, run, entry):
        seen["options"] = run.options
    monkeypatch.setattr(agent, "_run_turn", noop)
    chat = store.create_chat()

    async def go():
        await agent.start_turn(store, chat["id"], "hi", model_id, {"parts_mode": "only", "inventory_ids": ["box"]})
        await run_state._runs[chat["id"]].task
    asyncio.run(go())
    assert inventories.parts_options(seen["options"]) == ("only", ["box"])
    saved = store.get_chat(chat["id"])["options"]
    assert (saved["parts_mode"], saved["inventory_ids"]) == ("only", ["box"])
    # The chat as the UI reloads it carries the same options.
    with TestClient(app) as c:
        assert c.get(f"/api/chats/{chat['id']}").json()["chat"]["options"]["inventory_ids"] == ["box"]


def test_turn_after_inventory_deleted_is_rejected_cleanly(stocked, model_id, store, monkeypatch):
    chat = store.create_chat()
    store.update_chat(chat["id"], options={"parts_mode": "prefer", "inventory_ids": ["box"]})
    inventories.delete("box")

    async def go():
        await agent.start_turn(store, chat["id"], "hi", model_id, store.get_chat(chat["id"])["options"])
    with pytest.raises(ValueError, match="'box' no longer exists"):
        asyncio.run(go())
    assert store.messages(chat["id"]) == []


# --- Brickit Pileometer exports ---------------------------------------------------

PILE = 'Scan,Part,PartName,Color,Quantity\n1,3001,"Brick 2 x 4",Grays,2\n2,3001,"Brick 2 x 4",Grays,1\n1,3003,"Brick 2 x 2",Reds,4\n1,Unknown,"",Reds,2\n'


def stored(inventory_id="pile"):
    return json.loads((settings.INVENTORIES_DIR / f"{inventory_id}.json").read_text())


def test_pileometer_upload_keeps_families_for_review_and_lists_unmatched_parts(client, toolkit_cli):
    r = client.post("/api/inventories", json={"name": "Pile", "csv": PILE})
    assert r.status_code == 200, r.text
    body = r.json()
    report = body["report"]
    assert report["dialect"] == "pileometer"
    assert (report["pileometer"]["scans"], report["pileometer"]["rows"], report["pileometer"]["pieces"]) == (2, 4, 9)
    assert {f["family"]: f["colour"] for f in report["pileometer"]["families"]} == {"Grays": None, "Reds": None}
    assert [(u["rb_part"], u["qty"]) for u in report["unmapped_parts"]] == [("Unknown", 2)]
    data = stored()
    assert data["rows"] == [] and {(i["part"], i["family"], i["qty"]) for i in data["review"]} == {("3001", "Grays", 3), ("3003", "Reds", 4)}
    assert (body["inventory"]["total_pieces"], body["inventory"]["review_pieces"], body["inventory"]["review_items"]) == (0, 7, 2)


def test_pileometer_assign_on_upload_and_again_later(client, toolkit_cli):
    r = client.post("/api/inventories", json={"name": "Pile", "csv": PILE, "family_colours": {"Grays": 72}}).json()
    assert {f["family"]: f["colour"] for f in r["report"]["pileometer"]["families"]} == {"Grays": 72, "Reds": None}
    row = stored()["rows"][0]
    assert (row["part"], row["colour"], row["qty"], row["colour_approximate"]) == ("3001", 72, 3, True)
    again = client.put("/api/inventories/pile/family-colours", json={"family_colours": {"Grays": 0, "Reds": 14}})
    assert again.status_code == 200, again.text
    data = stored()
    assert {(r["part"], r["colour"]) for r in data["rows"]} == {("3001", 0), ("3003", 14)} and not data.get("review")
    assert data["name"] == "Pile" and len(client.get("/api/inventories").json()["inventories"]) == 1
    assert client.put("/api/inventories/pile/family-colours", json={"family_colours": {"Purples": 4}}).status_code == 400
    assert client.put("/api/inventories/pile/family-colours", json={"family_colours": {"Reds": "x"}}).status_code == 400


def test_family_assignment_refused_for_other_formats(client, toolkit_cli):
    client.post("/api/inventories", json={"name": "plain", "csv": "Part,Color,Quantity\n3001,4,1\n"})
    assert client.put("/api/inventories/plain/family-colours", json={"family_colours": {"Reds": 4}}).status_code == 400
    assert client.put("/api/inventories/missing/family-colours", json={"family_colours": {}}).status_code == 404
