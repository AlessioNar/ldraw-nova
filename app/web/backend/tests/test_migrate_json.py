"""The JSON importer (migrate_json.py): dry run, commit, verify, idempotence, the frozen files, the export, the first start.

The corpus is a small data folder with the awkward cases the owner's real volumes have: a message cut short by a crash, a
build with no build.json (derived from its model files), a build and a chat that record the same version twice, a chat whose
build does not exist, a worker transcript, an old `_subagent` message, a draft with extra keys, a proposal.

Skipped unless LDRAW_NOVA_TEST_DATABASE_URL points at a throwaway PostgreSQL (see conftest.py)."""
import hashlib
import json
import shutil
from pathlib import Path

import pytest
from sqlalchemy import func, select, text

import builds
import migrate_json
import settings
import storage
from db.models import AppSetting, Build, BuildDraft, Chat, ImportRun, Message, ModelRecord, Proposal, UsageEvent, WorkerStep

TINY = ("0 FILE tiny.ldr\n0 Two bricks\n0 Name: tiny.ldr\n0 Author: Tests\n0 !LDRAW_ORG Model\n"
        "1 4 0 0 0 1 0 0 0 1 0 0 0 1 3001.dat\n0 STEP\n1 1 0 -24 0 1 0 0 0 1 0 0 0 1 3003.dat\n0 NOFILE\n")
C1, C2, C3 = "20261001-100000-aaaaaa", "20261002-100000-bbbbbb", "20261003-100000-cccccc"
PID = "0123456789ab"


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")


def write_jsonl(path: Path, records: list, tail: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records) + tail, encoding="utf-8")


def make_corpus(root: Path) -> None:
    gen = root / "generated"
    gen.mkdir(parents=True, exist_ok=True)
    for name in ("house-v1.mpd", "house-v2.mpd", "old-barn-v1.mpd", "dupes-v1.mpd", "chatcar-v1.mpd"):
        (gen / name).write_text(TINY.replace("Two bricks", name), encoding="utf-8")
    # a build with everything
    house = root / "builds" / "house"
    write_json(house / "build.json", {"id": "b-house", "title": "House", "created_at": 1790000000.5, "updated_at": 1790000500.25,
                                      "chats": [C1], "source": {"kind": "generator", "generator": "house", "params": {"width": 6}}})
    write_jsonl(house / "models.jsonl", [
        {"id": "a1a1a1a1a1a1", "name": "House", "model": "../../generated/house-v1.mpd", "warnings": ["w"], "created_at": 1790000010.5, "version": 1, "origin": "generator"},
        {"id": "a2a2a2a2a2a2", "name": "House", "model": "../../generated/house-v2.mpd", "warnings": [], "created_at": 1790000020.5, "version": 2, "origin": "editor"}])
    write_json(house / "draft.json", {"base_version": 2, "base_file": "house-v2.mpd", "base_revision": 7, "saved_revision": 7,
                                      "base_undo_depth": 4, "forked": True, "drift": 2, "updated_at": 1790000400.5,
                                      "sources": {"3": {"id": "house", "params": {"width": 8}}}})
    (house / "proposals").mkdir(parents=True)
    (house / "proposals" / f"{PID}.mpd").write_text(TINY, encoding="utf-8")
    write_json(house / "proposals" / f"{PID}.json", {"id": PID, "created_at": 1790000300.5, "summary": {"added": 1, "removed": 0, "moved": 0},
                                                      "cost_eur": 0.0123, "chat_id": C1, "warnings": ["careful"]})
    (house / "output").mkdir()
    (house / "edit.json").write_text("{}", encoding="utf-8")                  # editor state: a file, not imported
    # a build with the same version recorded twice
    dupes = root / "builds" / "dupes"
    write_json(dupes / "build.json", {"id": "b-dupes", "title": "Dupes", "created_at": 1790000001.0, "updated_at": 1790000002.0, "chats": [],
                                      "source": {"kind": "empty"}})
    write_jsonl(dupes / "models.jsonl", [
        {"id": "b1b1b1b1b1b1", "name": "Dupes", "model": "../../generated/dupes-v1.mpd", "warnings": [], "created_at": 1790000011.0, "version": 1},
        {"id": "b2b2b2b2b2b2", "name": "Dupes again", "model": "../../generated/dupes-v1.mpd", "warnings": [], "created_at": 1790000012.0, "version": 1}])
    # (old-barn has no build folder: a build derived from its file)
    # chats
    c1 = root / "chats" / C1
    write_json(c1 / "chat.json", {"id": C1, "title": "Build a house", "llm_model_id": "anthropic/claude-x", "created_at": 1790000005.5,
                                  "updated_at": 1790000450.25, "build_id": "house", "options": {"permissions": "full"}})
    write_jsonl(c1 / "messages.jsonl", [
        {"id": 1, "created_at": 1790000006.5, "role": "user", "content": "A house with café windows"},
        {"id": 2, "created_at": 1790000007.5, "role": "assistant", "content": "ok", "_llm_model": "anthropic/claude-x",
         "_usage": {"prompt": 100, "completion": 20, "cache_read": 10, "cache_write": 5, "cost_usd": 0.01},
         "tool_calls": [{"id": "t1", "type": "function", "function": {"name": "run_toolkit", "arguments": "{\"a\": 1}"}}]},
        {"id": 3, "created_at": 1790000008.5, "role": "assistant", "content": "", "_ui_only": True, "_hidden": True,
         "_usage": {"prompt": 50, "completion": 5, "cost_usd": 0.02}, "_worker": {"id": "abcd1234", "model": "cheap/w", "steps": 2, "status": "done"}},
        {"id": 4, "created_at": 1790000009.5, "role": "tool", "tool_call_id": "t1", "content": "result"}],
        tail='{"id": 5, "created_at": 1790000010.5, "role": "assist')              # cut short by a crash
    write_jsonl(c1 / "workers" / "abcd1234.jsonl", [{"id": 1, "role": "user", "content": "task"},
                                                     {"id": 2, "role": "assistant", "content": "done", "_usage": {"prompt": 50, "completion": 5, "cost_usd": 0.02}}])
    write_jsonl(c1 / "models.jsonl", [{"id": "c1c1c1c1c1c1", "name": "House", "model": "../../generated/house-v1.mpd", "warnings": [],
                                       "created_at": 1790000030.5, "version": 1, "origin": "editor"}])
    (c1 / "renders").mkdir(parents=True)
    c2 = root / "chats" / C2
    write_json(c2 / "chat.json", {"id": C2, "title": "A car", "llm_model_id": None, "created_at": 1790000100.5, "updated_at": 1790000200.5})
    write_jsonl(c2 / "messages.jsonl", [
        {"id": 1, "created_at": 1790000101.5, "role": "user", "content": "car"},
        {"id": 2, "created_at": 1790000102.5, "role": "assistant", "content": "", "_subagent": "w1", "_usage": {"prompt": 7, "completion": 1}},
        {"id": 3, "created_at": 1790000103.5, "role": "assistant", "content": "unpriced", "_usage": {"prompt": 9, "completion": 2}}])
    write_jsonl(c2 / "models.jsonl", [
        {"id": "d1d1d1d1d1d1", "name": "Car", "model": "../../generated/chatcar-v1.mpd", "warnings": [], "created_at": 1790000110.5, "version": 1},
        {"id": "d2d2d2d2d2d2", "name": "Car 2", "model": "../../generated/chatcar-v1.mpd", "warnings": [], "created_at": 1790000120.5, "version": 1},
        {"id": "d3d3d3d3d3d3", "name": "Gone", "model": "../../generated/deleted-v1.mpd", "warnings": [], "created_at": 1790000130.5, "version": 2}])
    c3 = root / "chats" / C3
    write_json(c3 / "chat.json", {"id": C3, "title": "Orphan", "llm_model_id": None, "created_at": 1790000300.5, "updated_at": 1790000300.5, "build_id": "ghost"})


def tree_hashes(root: Path) -> dict:
    return {p.relative_to(root).as_posix(): (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns) for p in sorted(root.rglob("*")) if p.is_file()}


@pytest.fixture
def corpus(database):
    root = settings.DATA_DIR
    for folder in ("chats", "builds", "generated", "output", "_migration"):
        shutil.rmtree(root / folder, ignore_errors=True)
    for folder in ("chats", "output", "generated"):
        (root / folder).mkdir(parents=True, exist_ok=True)
    builds._groups_cache = (None, {})
    make_corpus(root)
    yield root
    for folder in ("chats", "builds", "generated", "output", "_migration"):
        shutil.rmtree(root / folder, ignore_errors=True)
    for folder in ("chats", "output", "generated"):
        (root / folder).mkdir(parents=True, exist_ok=True)
    builds._groups_cache = (None, {})


def table_counts(database) -> dict:
    with database.session_scope() as session:
        return {t.__tablename__: session.scalar(select(func.count()).select_from(t))
                for t in (Build, Chat, Message, WorkerStep, ModelRecord, BuildDraft, Proposal, UsageEvent)}


def test_dry_run_reports_and_writes_nothing(database, corpus):
    before = tree_hashes(corpus)
    report = migrate_json.run_import(corpus, dry_run=True)
    assert report.mode == "dry-run" and report.ok
    c = report.counts
    assert c["chats_added"] == 3 and c["messages_added"] == 7 and c["builds_added"] == 4 and c["worker_steps_added"] == 2
    assert c["messages_unreadable"] == 1 and c["usage_events_added"] == 4
    assert any("unreadable" in a for a in report.anomalies) and any("ghost" in a for a in report.anomalies)
    assert any("recorded twice" in a for a in report.anomalies)
    assert set(table_counts(database).values()) == {0}                         # rolled back
    with database.session_scope() as session:
        run = session.scalars(select(ImportRun)).one()
    assert run.mode == "dry-run" and run.ok and run.report["counts"]["chats_added"] == 3
    assert tree_hashes(corpus) == before


def test_commit_verify_and_the_files_stay_untouched(database, corpus):
    before = tree_hashes(corpus)
    ok, reports = migrate_json.import_and_verify(corpus)
    assert ok, [r.mismatches for r in reports]
    verified = reports[-1]
    assert verified.mode == "verify" and verified.mismatches == []
    matched = verified.extra["matched"]
    assert matched["chats"] == 3 and matched["messages"] == 7 and matched["builds"] == 4 and matched["worker_steps"] == 2
    assert matched["build_versions"] == 3 and matched["build_drafts"] == 1 and matched["proposals"] == 1
    # builds: house, dupes, and the two derived from model files (old-barn, chatcar); versions: house 2 + dupes 1 + chats 1 + 2
    # + the two derived builds' files adopted by the reconcile step
    assert table_counts(database) == {"build": 4, "chat": 3, "message": 7, "worker_step": 2, "model_record": 8,
                                      "build_draft": 1, "proposal": 1, "usage_event": 4}
    # the files: only the report folder was added
    after = tree_hashes(corpus)
    assert {k: v for k, v in after.items() if not k.startswith("_migration/")} == before
    assert list((corpus / "_migration").glob("report-*.json"))
    with database.session_scope() as session:
        done = session.get(AppSetting, "json_import").value
        modes = [r.mode for r in session.scalars(select(ImportRun).order_by(ImportRun.id))]
    assert done["done"] is True and done["counts"]["chats_in_files"] == 3
    assert modes == ["dry-run", "commit", "verify"]


def test_what_was_imported_reads_like_the_files(database, corpus):
    migrate_json.run_import(corpus, dry_run=False)
    from pg_store import PgChatStore
    store = PgChatStore(settings.CHATS_DIR, settings.OUTPUT_DIR)
    chat = store.get_chat(C1)
    assert chat == {"id": C1, "title": "Build a house", "llm_model_id": "anthropic/claude-x", "created_at": 1790000005.5,
                    "updated_at": 1790000450.25, "build_id": "house", "options": {"permissions": "full"}}
    assert [m["id"] for m in store.messages(C1)] == [1, 2, 3, 4] and store.messages(C1)[0]["content"] == "A house with café windows"
    assert store.add_message(C1, {"role": "user", "content": "next"}) == 5                   # the counter continues
    assert [w["id"] for w in store.worker_messages(C1, "abcd1234")] == [1, 2]
    [ref] = store.models(C1)
    assert ref["model"] == "../../generated/house-v1.mpd" and store.resolve(C1, ref["model"]) == settings.GENERATED_DIR / "house-v1.mpd"
    assert builds.read(store, "house") == {"id": "b-house", "title": "House", "created_at": 1790000000.5, "updated_at": 1790000500.25,
                                           "chats": [C1], "source": {"kind": "generator", "generator": "house", "params": {"width": 6}}}
    assert builds.read(store, "old-barn")["source"] == {"kind": "import"}             # derived from its file, as builds._legacy does
    assert [v["version"] for v in builds.versions("old-barn")] == [1]
    import workspace
    assert workspace.read_draft(store, "b-house") == {"base_version": 2, "base_file": "house-v2.mpd", "base_revision": 7, "saved_revision": 7,
                                                      "base_undo_depth": 4, "forked": True, "drift": 2, "updated_at": 1790000400.5,
                                                      "sources": {"3": {"id": "house", "params": {"width": 8}}}}
    assert workspace.list_proposals(store, "house") == [{"id": PID, "created_at": 1790000300.5, "summary": {"added": 1, "removed": 0, "moved": 0},
                                                         "cost_eur": 0.0123, "chat_id": C1, "warnings": ["careful"]}]
    assert store.get_chat(C3).get("build_id") is None                                  # the build "ghost" never existed
    assert [m["version"] for m in store.models(C2)] == [1, 2] and store.models(C2)[0]["id"] == "d2d2d2d2d2d2"   # duplicate: the later record wins
    assert [m["id"] for m in store.models(store.build_key("dupes"))] == ["b2b2b2b2b2b2"]


def test_the_second_run_adds_nothing(database, corpus):
    migrate_json.run_import(corpus, dry_run=False)
    first = table_counts(database)
    with database.session_scope() as session:
        snapshot = [session.execute(text(f"select md5(string_agg(t::text, '|' order by t::text)) from {name} t where true")).scalar()
                    for name in ("build", "chat", "message", "model_record", "worker_step", "build_draft", "proposal")]
    again = migrate_json.run_import(corpus, dry_run=False)
    assert table_counts(database) == first
    assert [again.counts.get(k, 0) for k in ("chats_added", "messages_added", "builds_added", "usage_events_added", "worker_steps_added")] == [0] * 5
    with database.session_scope() as session:
        assert snapshot == [session.execute(text(f"select md5(string_agg(t::text, '|' order by t::text)) from {name} t where true")).scalar()
                            for name in ("build", "chat", "message", "model_record", "worker_step", "build_draft", "proposal")]
    assert migrate_json.run_verify(corpus).ok


def test_each_message_with_a_usage_gets_one_cost_event_and_live_ones_are_not_doubled(database, corpus):
    with database.session_scope() as session:               # the live recorder already wrote message 2 of the first chat
        session.add(UsageEvent(kind="agent", chat_id=C1, message_id=2, model="anthropic/claude-x", prompt=100, completion=20, cost_usd=0.01))
    report = migrate_json.run_import(corpus, dry_run=False)
    with database.session_scope() as session:
        rows = session.execute(select(UsageEvent.chat_id, UsageEvent.kind, UsageEvent.message_id, UsageEvent.worker_id, UsageEvent.cost_usd)
                               .order_by(UsageEvent.chat_id, UsageEvent.message_id)).all()
    assert [(r[0][-6:], r[1], r[2], r[3], float(r[4]) if r[4] is not None else None) for r in rows] == [
        ("aaaaaa", "agent", 2, None, 0.01), ("aaaaaa", "worker", 3, "abcd1234", 0.02),
        ("bbbbbb", "worker", 2, "w1", None), ("bbbbbb", "agent", 3, None, None)]
    assert report.counts["usage_events_added"] == 3
    assert migrate_json.run_verify(corpus).ok


def test_verify_catches_a_changed_message(database, corpus):
    migrate_json.run_import(corpus, dry_run=False)
    with database.session_scope() as session:
        session.execute(text("update message set body = jsonb_set(body, '{content}', '\"tampered\"') where chat_id = :c and id = 1"), {"c": C1})
    report = migrate_json.run_verify(corpus)
    assert not report.ok and any(C1 in m and "hash" in m for m in report.mismatches)
    with database.session_scope() as session:
        assert session.scalars(select(ImportRun.ok).order_by(ImportRun.id.desc())).first() is False


def test_verify_catches_a_missing_message_a_changed_model_file_and_a_cost_drift(database, corpus):
    migrate_json.run_import(corpus, dry_run=False)
    with database.session_scope() as session:
        session.execute(text("delete from message where chat_id = :c and id = 4"), {"c": C1})
        session.execute(text("update usage_event set cost_usd = cost_usd + 1 where chat_id = :c and message_id = 3"), {"c": C1})
    (corpus / "generated" / "house-v2.mpd").write_text("0 changed\n", encoding="utf-8")
    report = migrate_json.run_verify(corpus)
    text_ = "\n".join(report.mismatches)
    assert not report.ok and "4 messages in the files, 3 of them" in text_ and "sha256" in text_ and "cost in the files" in text_


def test_verify_notices_a_file_that_is_missing_from_the_database(database, corpus):
    migrate_json.run_import(corpus, dry_run=False)
    with database.session_scope() as session:
        session.execute(text("delete from chat where id = :c"), {"c": C3})
        session.execute(text("delete from build where id = 'dupes'"))
    report = migrate_json.run_verify(corpus)
    assert not report.ok and any(C3 in m and "missing" in m for m in report.mismatches) and any("dupes" in m for m in report.mismatches)


def test_a_file_changed_after_the_import_is_reported_not_copied_over(database, corpus):
    migrate_json.run_import(corpus, dry_run=False)
    chat_json = corpus / "chats" / C2 / "chat.json"
    changed = json.loads(chat_json.read_text())
    chat_json.write_text(json.dumps({**changed, "title": "Edited in the files"}), encoding="utf-8")
    report = migrate_json.run_import(corpus, dry_run=True)
    assert any(a.startswith("changed:") and C2 in a for a in report.anomalies)
    with database.session_scope() as session:
        assert session.get(Chat, C2).title == "A car"


def test_the_export_writes_the_json_layout_back(database, corpus, tmp_path):
    migrate_json.run_import(corpus, dry_run=False)
    out = tmp_path / "exported"
    report = migrate_json.Report("export")
    migrate_json.export(corpus, out, False, report)
    assert report.counts["chats_exported"] == 3 and report.counts["builds_exported"] == 4
    for chat_id in (C1, C2, C3):
        original = json.loads((corpus / "chats" / chat_id / "chat.json").read_text())
        assert json.loads((out / "chats" / chat_id / "chat.json").read_text()) == {k: v for k, v in original.items() if not (k == "build_id" and v == "ghost")}
    mine, theirs = migrate_json.read_jsonl(out / "chats" / C1 / "messages.jsonl")[0], migrate_json.read_jsonl(corpus / "chats" / C1 / "messages.jsonl")[0]
    assert mine == theirs and len(mine) == 4                                          # the cut-short line was never imported
    assert migrate_json.read_jsonl(out / "chats" / C1 / "workers" / "abcd1234.jsonl")[0] == migrate_json.read_jsonl(corpus / "chats" / C1 / "workers" / "abcd1234.jsonl")[0]
    exported = migrate_json.read_jsonl(out / "chats" / C1 / "models.jsonl")[0]
    original = migrate_json.read_jsonl(corpus / "chats" / C1 / "models.jsonl")[0]
    resolve = lambda base, rec: (Path(base) / rec["model"]).resolve()
    assert [{k: v for k, v in r.items() if k != "model"} for r in exported] == [{k: v for k, v in r.items() if k != "model"} for r in original]
    assert resolve(out / "chats" / C1, exported[0]) == resolve(corpus / "chats" / C1, original[0])
    build = json.loads((out / "builds" / "house" / "build.json").read_text())
    assert build == json.loads((corpus / "builds" / "house" / "build.json").read_text())
    assert json.loads((out / "builds" / "house" / "draft.json").read_text()) == json.loads((corpus / "builds" / "house" / "draft.json").read_text())
    assert json.loads((out / "builds" / "house" / "proposals" / f"{PID}.json").read_text()) == json.loads((corpus / "builds" / "house" / "proposals" / f"{PID}.json").read_text())
    assert (out / "builds" / "house" / "proposals" / f"{PID}.mpd").read_text() == TINY
    # a second export leaves every existing file alone, --force rewrites them
    again = migrate_json.Report("export")
    migrate_json.export(corpus, out, False, again)
    assert again.counts["files_written"] == 0 and again.counts["files_skipped_existing"] > 10
    forced = migrate_json.Report("export")
    migrate_json.export(corpus, out, True, forced)
    assert forced.counts["files_written"] > 10


def test_the_first_start_imports_only_when_the_owner_opted_in(database, corpus, monkeypatch):
    monkeypatch.delenv("LDRAW_NOVA_DB_IMPORT", raising=False)
    assert migrate_json.first_start(corpus) is None and table_counts(database)["chat"] == 0          # default: off
    monkeypatch.setenv("LDRAW_NOVA_DB_IMPORT", "auto")
    assert migrate_json.first_start(corpus) is True
    assert table_counts(database)["chat"] == 3 and storage.import_done()
    assert list((corpus / "_migration").glob("report-*.json"))
    assert migrate_json.first_start(corpus) is None                                                # done: never again
    monkeypatch.setenv("LDRAW_NOVA_STORAGE", "auto")
    assert storage.refresh() is True                                                               # and the app now reads the database
    monkeypatch.undo()
    storage.refresh()


def test_the_first_start_does_nothing_on_an_empty_volume(database, monkeypatch):
    for folder in ("chats", "builds"):
        shutil.rmtree(settings.DATA_DIR / folder, ignore_errors=True)
    settings.CHATS_DIR.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("LDRAW_NOVA_DB_IMPORT", "auto")
    assert migrate_json.first_start(settings.DATA_DIR) is None


def test_command_line(database, corpus, capsys):
    assert migrate_json.main([]) == 0
    assert "dry-run: OK" in capsys.readouterr().out and table_counts(database)["chat"] == 0
    assert migrate_json.main(["--commit"]) == 0
    out = capsys.readouterr().out
    assert "commit: OK" in out and "verify: OK" in out and table_counts(database)["chat"] == 3
    assert migrate_json.main(["--verify"]) == 0
    with database.session_scope() as session:
        session.execute(text("delete from message where chat_id = :c"), {"c": C1})
    assert migrate_json.main(["--verify"]) == 1 and "MISMATCH" in capsys.readouterr().out
