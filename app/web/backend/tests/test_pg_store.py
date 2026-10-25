"""The PostgreSQL store against the file store: the same operations give the same results (pg_store.py, pg_builds.py),
the storage switch (storage.py), and what only a database has to prove (numbering under concurrency, cascades).

Skipped unless LDRAW_NOVA_TEST_DATABASE_URL points at a throwaway PostgreSQL (see conftest.py)."""
import shutil
import threading

import pytest

import builds
import settings
import storage
import workspace
from pg_store import PgChatStore
from store import ChatStore

TINY = ("0 FILE tiny.ldr\n0 Two bricks\n0 Name: tiny.ldr\n0 Author: Tests\n0 !LDRAW_ORG Model\n"
        "1 4 0 0 0 1 0 0 0 1 0 0 0 1 3001.dat\n0 STEP\n1 1 0 -24 0 1 0 0 0 1 0 0 0 1 3003.dat\n0 NOFILE\n")
TINY2 = TINY.replace("3003.dat", "3004.dat")


@pytest.fixture
def clean_data():
    """An empty data folder (chats, builds, generated models), before and after."""
    def wipe():
        for folder in (settings.CHATS_DIR, settings.DATA_DIR / "builds", settings.GENERATED_DIR, settings.OUTPUT_DIR):
            shutil.rmtree(folder, ignore_errors=True)
            folder.mkdir(parents=True, exist_ok=True)
        builds._groups_cache = (None, {})
    wipe()
    yield
    wipe()


def file_store() -> ChatStore:
    return ChatStore(settings.CHATS_DIR, settings.OUTPUT_DIR)


def pg_store(database) -> PgChatStore:
    return PgChatStore(settings.CHATS_DIR, settings.OUTPUT_DIR)


def shape(value):
    """A result with the parts that legitimately differ between two runs (ids made from the clock or at random, float
    times) replaced by their type, so two backends can be compared."""
    if isinstance(value, dict):
        return {k: ("<time>" if k in ("created_at", "updated_at") else "<id>" if k == "id" and not str(v).startswith("b-")
                    else ["<id>"] * len(v) if k == "chats" else "<id>" if k == "chat_id" and v else shape(v))
                for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [shape(v) for v in value]
    return value


def scenario(store, tag: str) -> dict:
    """One session of everything the routers do with chats, models, builds, drafts and proposals."""
    out = {}
    settings.GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    chat = store.create_chat("First", llm_model_id="m1")
    other = store.create_chat()
    store.update_chat(chat["id"], title="Renamed", options={"permissions": "full"})
    store.update_chat(chat["id"], bogus="ignored")
    out["chat"] = shape(store.get_chat(chat["id"]))
    out["missing"] = store.get_chat("20200101-000000-aaaaaa")
    ids = [store.add_message(chat["id"], {"role": "user", "content": "build a car"}),
           store.add_message(chat["id"], {"role": "assistant", "content": "ok", "_usage": {"prompt": 10, "completion": 2, "cost_usd": 0.01},
                                          "tool_calls": [{"id": "t1", "type": "function", "function": {"name": "x", "arguments": "{}"}}]}),
           store.add_message(chat["id"], {"role": "assistant", "_fast_path": {"by": "person"}, "content": "café ☃"})]
    out["ids"] = ids
    out["messages"] = shape(store.messages(chat["id"]))
    out["no_messages"] = store.messages(other["id"])
    car = settings.GENERATED_DIR / f"{tag}-car-v1.mpd"
    car.write_text(TINY, encoding="utf-8")
    car2 = settings.GENERATED_DIR / f"{tag}-car-v2.mpd"
    car2.write_text(TINY2, encoding="utf-8")
    first = store.add_model(chat["id"], "Car", car, ["w1"], source="car.ldr", origin="editor")
    second = store.add_model(chat["id"], "Car", car2, [])
    out["models"] = shape(store.models(chat["id"]))
    out["model_ref"] = first["model"]
    out["model_resolves"] = store.resolve(chat["id"], first["model"]) == car and second["version"] == 2
    out["chat_order"] = [c["title"] for c in store.list_chats()]
    # a build
    slug, record = builds.create(store, f"{tag} House", {"kind": "generator", "generator": "house", "params": {"w": 6}})
    out["build"] = shape(builds.read(store, slug))
    chat_b = store.create_chat(build_id=slug)
    builds.attach_chat(store, slug, chat_b["id"])
    out["build_chats"] = [c == chat_b["id"] for c in builds.read(store, slug)["chats"]]
    out["attached_chat"] = shape(store.get_chat(chat_b["id"]))
    v1 = settings.GENERATED_DIR / f"{slug}-v1.mpd"
    v1.write_text(TINY, encoding="utf-8")
    builds._groups_cache = (None, {})
    builds.record_version(store, slug, "House", v1, "generator", ["careful"], source={"kind": "generator", "generator": "house", "params": {"w": 8}})
    v2 = builds.copy_into_generated(store, slug, v1)
    builds.record_version(store, slug, "House", v2, "editor")
    builds._groups_cache = (None, {})
    out["versions"] = [(v["version"], v["path"].name.replace(slug, "<slug>")) for v in builds.versions(slug)]
    out["version_models"] = shape(store.models(store.build_key(slug)))
    out["origin"] = builds.origin_of(store, slug, v1)
    out["build_after"] = shape(builds.read(store, slug))
    builds.update(store, slug, title="Renamed house")
    out["build_title"] = builds.read(store, slug)["title"]
    out["slugs"] = [s.replace(slug, "<slug>") for s in builds.all_slugs(store)]
    out["unique_slug"] = builds.unique_slug(store, f"{tag} House").replace(slug, "<slug>")
    # the draft
    key = store.build_key(slug)
    out["no_draft"] = workspace.read_draft(store, key)
    saved = workspace.write_draft(store, key, {"base_version": 2, "base_file": f"{slug}-v2.mpd", "base_revision": 5, "saved_revision": 5,
                                               "base_undo_depth": 3, "forked": False, "drift": 0})
    workspace.touch_draft(store, key, "edit", {"undo_depth": 1})
    workspace.write_draft(store, key, {**workspace.read_draft(store, key), "sources": {"2": {"id": "house"}}})
    out["draft"] = shape(workspace.read_draft(store, key))
    out["draft_keys"] = sorted(workspace.read_draft(store, key))
    # proposals
    made = [workspace.create_proposal(store, slug, TINY2, chat["id"], TINY, 0.0123, ["warned"]) for _ in range(3)]
    listed = workspace.list_proposals(store, slug)
    out["proposals"] = shape(listed)
    out["proposal_ids_in_order"] = [p["id"] for p in listed] == [m["id"] for m in made]
    out["proposal_file"] = workspace.proposal_file(store, slug, made[0]["id"]).read_text(encoding="utf-8") == TINY2
    workspace.delete_proposal(store, slug, made[0]["id"])
    out["proposals_after_delete"] = len(workspace.list_proposals(store, slug))
    out["proposal_gone"] = workspace.proposal_file(store, slug, made[0]["id"])
    # workers
    sink = store.worker_sink(chat["id"], "abcd1234")
    sink.add({"role": "user", "content": "task"})
    sink.add({"role": "assistant", "content": "done", "_usage": {"prompt": 1, "completion": 1}})
    out["worker"] = shape(store.worker_messages(chat["id"], "abcd1234"))
    out["worker_none"] = store.worker_messages(chat["id"], "00000000")
    out["sink_messages"] = shape(sink.messages())
    # deleting
    store.delete_chat(chat["id"])
    out["deleted"] = [store.get_chat(chat["id"]), store.messages(chat["id"]), store.models(chat["id"])]
    out["files_kept"] = car.exists() and not store.chat_dir(chat["id"]).exists()
    builds.forget(store, slug)
    out["build_forgotten_chat_kept"] = shape(store.get_chat(chat_b["id"]).get("build_id")) if store.pg else None
    return out


def test_the_same_operations_give_the_same_results(database, clean_data):
    expected = scenario(file_store(), "a")
    for folder in (settings.CHATS_DIR, settings.DATA_DIR / "builds", settings.GENERATED_DIR, settings.OUTPUT_DIR):
        shutil.rmtree(folder, ignore_errors=True)
        folder.mkdir(parents=True, exist_ok=True)
    builds._groups_cache = (None, {})
    got = scenario(pg_store(database), "a")
    assert got.pop("build_forgotten_chat_kept") is None and expected.pop("build_forgotten_chat_kept") is None   # detached: no build_id
    for name in expected:
        assert got[name] == expected[name], name


def test_messages_are_stored_verbatim_in_jsonb(database):
    store = pg_store(database)
    chat = store.create_chat()
    message = {"role": "assistant", "content": "", "_usage": {"prompt": 3, "cost_usd": 1e-05}, "_worker": {"id": "ab12cd34", "steps": 4},
               "nested": {"b": [1, 2.5, None, True], "a": "ü"}, "provider_specific_fields": {"thinking_blocks": [{"signature": "x" * 400}]}}
    msg_id = store.add_message(chat["id"], message)
    [stored] = store.messages(chat["id"])
    assert stored["id"] == msg_id == 1 and isinstance(stored["created_at"], float)
    assert {k: v for k, v in stored.items() if k not in ("id", "created_at")} == message


def test_a_nul_character_does_not_break_a_message(database):
    store = pg_store(database)
    chat = store.create_chat()
    store.add_message(chat["id"], {"role": "tool", "content": "binary \x00 output"})
    assert store.messages(chat["id"])[0]["content"] == "binary � output"


def test_message_numbers_are_unique_under_concurrency(database):
    store = pg_store(database)
    chat = store.create_chat()
    taken, errors = [], []

    def write():
        try:
            for _ in range(15):
                taken.append(store.add_message(chat["id"], {"role": "user", "content": "x"}))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
    threads = [threading.Thread(target=write) for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors and sorted(taken) == list(range(1, 61))
    assert [m["id"] for m in store.messages(chat["id"])] == list(range(1, 61))
    # a second store object continues the numbering (it lives in the row, not in memory)
    assert pg_store(database).add_message(chat["id"], {"role": "user", "content": "y"}) == 61


def test_two_saves_at_once_get_two_versions(database, clean_data):
    store = pg_store(database)
    chat = store.create_chat()
    files = []
    for n in (1, 2, 3, 4):
        path = settings.GENERATED_DIR / f"race-{n}.mpd"
        path.write_text(TINY, encoding="utf-8")
        files.append(path)
    versions = []
    threads = [threading.Thread(target=lambda p=p: versions.append(store.add_model(chat["id"], "Race", p, [])["version"])) for p in files]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(versions) == [1, 2, 3, 4]


def test_a_build_version_is_the_file_number_and_a_resave_replaces_the_record(database, clean_data):
    store = pg_store(database)
    slug, _ = builds.create(store, "Tower")
    path = settings.GENERATED_DIR / "tower-v3.mpd"
    path.write_text(TINY, encoding="utf-8")
    first = store.add_model(store.build_key(slug), "Tower", path, [], origin="generator")
    again = store.add_model(store.build_key(slug), "Tower again", path, [], origin="editor")
    assert first["version"] == again["version"] == 3
    [only] = store.models(store.build_key(slug))
    assert only["id"] == again["id"] and only["name"] == "Tower again" and only["origin"] == "editor"


def test_model_records_carry_sha256_and_part_count(database, clean_data):
    import hashlib
    from sqlalchemy import select
    from db.models import ModelRecord
    store = pg_store(database)
    chat = store.create_chat()
    path = settings.GENERATED_DIR / "counted-v1.mpd"
    path.write_text(TINY, encoding="utf-8")
    store.add_model(chat["id"], "Counted", path, [])
    with database.session_scope() as session:
        row = session.scalars(select(ModelRecord)).one()
    assert row.sha256 == hashlib.sha256(path.read_bytes()).hexdigest() and row.parts == 2
    assert row.file == "generated/counted-v1.mpd"


def test_deleting_a_chat_cascades_but_keeps_the_spend_and_the_files(database, clean_data):
    import usage_log
    from sqlalchemy import func, select
    from db.models import Message, ModelRecord, UsageEvent, WorkerStep
    store = pg_store(database)
    chat = store.create_chat()
    store.add_message(chat["id"], {"role": "user", "content": "x"})
    store.worker_sink(chat["id"], "11112222").add({"role": "user", "content": "t"})
    path = settings.GENERATED_DIR / "kept.mpd"
    path.write_text(TINY, encoding="utf-8")
    store.add_model(chat["id"], "Kept", path, [])
    usage_log.record("agent", {"prompt": 5, "completion": 1, "cost_usd": 0.5}, "m", chat_id=chat["id"], message_id=1)
    store.delete_chat(chat["id"])
    with database.session_scope() as session:
        counts = [session.scalar(select(func.count()).select_from(t)) for t in (Message, WorkerStep, ModelRecord, UsageEvent)]
    assert counts == [0, 0, 0, 1] and path.exists()


def test_deleting_a_build_detaches_its_chats(database, clean_data):
    store = pg_store(database)
    slug, _ = builds.create(store, "Doomed")
    chat = store.create_chat(build_id=slug)
    builds.forget(store, slug)
    assert builds.read(store, slug) is None and "build_id" not in store.get_chat(chat["id"])


def test_a_build_workspace_key_is_not_a_chat(database):
    store = pg_store(database)
    assert store.get_chat("b-house") is None and store.models("b-house") == []
    store.update_chat("b-house", title="x")          # silently nothing, as the file store did for a missing chat.json


def test_reconcile_adopts_files_nobody_recorded(database, clean_data):
    from sqlalchemy import select
    from db.models import ModelRecord
    store = pg_store(database)
    slug, _ = builds.create(store, "Barn")
    for n in (1, 2):
        (settings.GENERATED_DIR / f"barn-v{n}.mpd").write_text(TINY, encoding="utf-8")
    builds._groups_cache = (None, {})
    assert builds.reconcile(store) == 2 and builds.reconcile(store) == 0
    with database.session_scope() as session:
        rows = session.scalars(select(ModelRecord).where(ModelRecord.build_id == slug).order_by(ModelRecord.version)).all()
    assert [(r.version, r.origin, r.parts) for r in rows] == [(1, None, 2), (2, None, 2)] and all(r.sha256 for r in rows)


def test_a_proposal_outlives_its_chat(database, clean_data):
    store = pg_store(database)
    slug, _ = builds.create(store, "Keep proposal")
    chat = store.create_chat(build_id=slug)
    made = workspace.create_proposal(store, slug, TINY2, chat["id"], TINY)
    store.delete_chat(chat["id"])
    assert workspace.list_proposals(store, slug)[0]["chat_id"] == chat["id"] and made["id"]


def test_old_proposals_are_pruned_to_the_newest_twenty(database, clean_data):
    store = pg_store(database)
    slug, _ = builds.create(store, "Many")
    made = [workspace.create_proposal(store, slug, TINY2, None, TINY)["id"] for _ in range(workspace.MAX_PROPOSALS + 3)]
    kept = [p["id"] for p in workspace.list_proposals(store, slug)]
    assert kept == made[-workspace.MAX_PROPOSALS:]


# ---------------------------------------------------------------- the switch

@pytest.fixture
def switch(monkeypatch, clean_data):
    """storage.use_database() decided afresh, with the data folder and the database under the test's control."""
    def decide(mode: str | None) -> bool:
        if mode is None:
            monkeypatch.delenv("LDRAW_NOVA_STORAGE", raising=False)
        else:
            monkeypatch.setenv("LDRAW_NOVA_STORAGE", mode)
        return storage.refresh()
    yield decide
    monkeypatch.undo()
    storage.refresh()
    import store as store_module
    store_module.reset_store()


def test_files_are_forced_by_the_setting(database, switch):
    assert switch("files") is False


def test_auto_uses_the_database_when_it_is_migrated_and_the_files_hold_nothing(database, switch):
    assert switch(None) is True and switch("auto") is True


def test_auto_keeps_the_files_until_they_are_imported(database, switch):
    chat = file_store().create_chat()                                   # a chat.json on disk
    (settings.CHATS_DIR / chat["id"] / "chat.json").write_text('{"id": "%s", "title": "t", "created_at": 1, "updated_at": 1}' % chat["id"])
    assert storage.json_state_present() and switch(None) is False
    assert switch("postgres") is True                                    # the owner can insist
    with database.session_scope() as session:
        from db.models import AppSetting
        session.add(AppSetting(key="json_import", value={"done": True}))
    assert switch(None) is True


def test_without_a_database_the_files_are_used(switch, monkeypatch):
    import db
    db.reset()
    monkeypatch.setenv("LDRAW_NOVA_DB_PASSWORD_FILE", "/nonexistent")
    monkeypatch.delenv("LDRAW_NOVA_DATABASE_URL", raising=False)
    assert switch("auto") is False and switch("postgres") is False


def test_an_unmigrated_database_means_files(fresh_database, switch):
    import db
    db.configure(fresh_database)
    try:
        assert switch("auto") is False
    finally:
        db.reset()


def test_get_store_follows_the_decision(database, switch):
    import store as store_module
    switch("files")
    store_module.reset_store()
    assert store_module.get_store().pg is False
    switch("postgres")
    store_module.reset_store()
    assert store_module.get_store().pg is True
    store_module.reset_store()


def test_environment_reports_the_storage(database, switch):
    from fastapi.testclient import TestClient
    from main import app
    switch("postgres")
    with TestClient(app) as client:
        body = client.get("/api/environment").json()
    assert body["storage"]["backend"] == "postgres" and body["storage"]["mode"] == "postgres"
