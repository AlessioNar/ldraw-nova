"""The application database: Alembic, the request log, the cost history and the costs API, on a real PostgreSQL.

They skip when LDRAW_NOVA_TEST_DATABASE_URL is unset (see conftest.py). No live provider calls: acompletion is scripted.
"""
import asyncio
import datetime as dt
import hashlib
import json
import threading
from decimal import Decimal
from pathlib import Path

import litellm
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, select, text

import agent
import agent_loop
import fast_path
import generator_bridge
import llm_config
import request_log
import run_state
import understand
import usage_log
from db import migrate
from db.models import AppSetting, RequestLog, UsageEvent, metadata
from main import app
from test_agent import chunk, scripted
from test_fast_path import HAIKU, HOUSE, REGISTRY, reply

REFERENCE_SQL = next((p for p in (Path(__file__).resolve().parents[i] / "docs/plans/postgres/sql/0001_init.sql" for i in (3, 4))
                      if p.exists()), Path("/opt/ldraw-nova/docs/plans/postgres/sql/0001_init.sql"))


# ---------------------------------------------------------------- Alembic

def test_upgrade_from_empty_creates_every_table_the_view_and_downgrades(fresh_database):
    engine = create_engine(fresh_database)
    migrate.upgrade(engine)
    names = set(inspect(engine).get_table_names())
    assert {t.name for t in metadata.sorted_tables} <= names and "alembic_version" in names
    assert len(metadata.sorted_tables) == 16
    assert "cost_daily" in inspect(engine).get_view_names()
    from alembic.script import ScriptDirectory
    head = ScriptDirectory.from_config(migrate.alembic_config()).get_current_head()
    with engine.connect() as connection:
        assert connection.execute(text("select version_num from alembic_version")).scalar_one() == head
    migrate.upgrade(engine)                                    # a second start is a no-op
    from alembic import command
    with engine.connect() as connection:
        command.downgrade(migrate.alembic_config(connection), "base")
        connection.commit()
    assert set(inspect(engine).get_table_names()) == {"alembic_version"} and not inspect(engine).get_view_names()
    engine.dispose()


def test_models_and_migrations_agree(fresh_database):
    """What `alembic revision --autogenerate` would add on top of the migrated database is nothing."""
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    engine = create_engine(fresh_database)
    migrate.upgrade(engine)
    with engine.connect() as connection:
        context = MigrationContext.configure(connection, opts={"compare_type": True, "compare_server_default": True})
        diff = compare_metadata(context, metadata)
    engine.dispose()
    assert diff == [], diff


def test_concurrent_starts_take_turns(fresh_database):
    errors = []

    def start():
        engine = create_engine(fresh_database)
        try:
            migrate.upgrade(engine)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            engine.dispose()
    threads = [threading.Thread(target=start) for _ in range(3)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert errors == []


def test_schema_equals_the_reference_sql(pg_admin, pg_template):
    """The first revision builds the same schema as docs/plans/postgres/sql/0001_init.sql (columns, constraints, indexes)."""
    if not REFERENCE_SQL.exists():
        pytest.skip("reference SQL is not in this environment")
    import os
    from conftest import create_database, drop_database
    name = f"obd_ref_{os.getpid()}"
    url = create_database(pg_admin, name)
    reference = create_engine(url)
    with reference.connect() as connection:
        connection.exec_driver_sql(REFERENCE_SQL.read_text(encoding="utf-8"))
        connection.commit()
    first = f"obd_rev1_{os.getpid()}"
    first_url = create_database(pg_admin, first)
    migrated = create_engine(first_url)
    migrate.upgrade(migrated, "0001")                                  # later revisions are tested on their own

    def describe(engine):
        with engine.connect() as c:
            columns = c.execute(text("""
                select c.relname, a.attname, format_type(a.atttypid, a.atttypmod), a.attnotnull, a.attgenerated, a.attidentity,
                       pg_get_expr(d.adbin, d.adrelid)
                from pg_attribute a join pg_class c on c.oid = a.attrelid join pg_namespace n on n.oid = c.relnamespace
                left join pg_attrdef d on d.adrelid = a.attrelid and d.adnum = a.attnum
                where n.nspname = 'public' and c.relkind = 'r' and a.attnum > 0 and not a.attisdropped
                  and c.relname <> 'alembic_version' order by 1, 2""")).all()
            constraints = c.execute(text("""
                select conrelid::regclass::text, conname, contype, pg_get_constraintdef(oid) from pg_constraint
                where connamespace = 'public'::regnamespace and conrelid::regclass::text <> 'alembic_version' order by 1, 2""")).all()
            indexes = c.execute(text("""
                select tablename, indexname, indexdef from pg_indexes
                where schemaname = 'public' and tablename <> 'alembic_version' order by 1, 2""")).all()
            view = c.execute(text("select pg_get_viewdef('cost_daily'::regclass)")).scalar_one()
        return columns, constraints, indexes, view

    try:
        wanted, got = describe(reference), describe(migrated)
    finally:
        reference.dispose()
        migrated.dispose()
        drop_database(pg_admin, name)
        drop_database(pg_admin, first)
    assert wanted[3] == got[3], "view"
    for label, left, right in zip(("columns", "constraints", "indexes"), wanted, got):
        only_reference, only_migrated = set(map(tuple, left)) - set(map(tuple, right)), set(map(tuple, right)) - set(map(tuple, left))
        assert not only_reference and not only_migrated, f"{label}: only in the reference {sorted(only_reference)}; only in the migration {sorted(only_migrated)}"


# ---------------------------------------------------------------- request log

def test_request_log_hashes_the_text_and_keeps_no_sentence_by_default(database):
    text_ = "make the roof blue"
    row_id = request_log.record("describe", text_, route="edit", decided_by="rules", generator="house",
                                spec={"subject": {"family": "house", "kind": "cottage"}}, latency_ms=12.4)
    assert row_id
    with database.session_scope() as session:
        row = session.get(RequestLog, row_id)
        assert row.text_sha == hashlib.sha256(text_.encode()).hexdigest()[:12] and row.text_len == len(text_)
        assert row.sentence is None and (row.route, row.decided_by, row.generator) == ("edit", "rules", "house")
        assert (row.family, row.kind, row.latency_ms, row.surface) == ("house", "cottage", 12, "describe")
        dump = json.dumps(session.execute(text("select to_jsonb(r) from request_log r")).scalar_one())
    assert text_ not in dump                                    # the words are nowhere in the row


def test_request_log_keeps_the_sentence_only_when_switched_on(database):
    assert request_log.get_keep_text() is False
    assert request_log.set_keep_text(True) and request_log.get_keep_text() is True
    row_id = request_log.record("describe", "  a red car ", route="generator", decided_by="llm")
    with database.session_scope() as session:
        assert session.get(RequestLog, row_id).sentence == "a red car"
    request_log.set_keep_text(False)
    row_id = request_log.record("describe", "a red car", route="generator", decided_by="llm")
    with database.session_scope() as session:
        assert session.get(RequestLog, row_id).sentence is None
        assert session.scalar(select(AppSetting.value).where(AppSetting.key == "keep_request_text")) is False


def test_request_log_prunes_rows_older_than_180_days(database):
    now = dt.datetime.now(dt.timezone.utc)
    with database.session_scope() as session:
        session.add_all([RequestLog(ts=now - dt.timedelta(days=d), route=f"r{d}") for d in (400, 181, 179, 1)])
    assert request_log.prune() == 2
    with database.session_scope() as session:
        assert sorted(session.scalars(select(RequestLog.route))) == ["r1", "r179"]
    assert request_log.prune() == 0


def test_a_failing_database_never_breaks_the_caller(database, monkeypatch):
    database.configure("postgresql+psycopg://nobody:x@127.0.0.1:1/none")       # nothing listens there
    assert request_log.record("describe", "hello", route="clarify", decided_by="rules") is None
    assert usage_log.record("agent", {"prompt": 1, "completion": 1}, "m") is None
    assert request_log.prune() == 0
    assert database.status()["state"] == "unavailable"


@pytest.fixture
def registry(monkeypatch):
    generator_bridge.set_registry(REGISTRY)
    monkeypatch.setattr(fast_path, "history_stats", lambda force=False: None)
    yield
    generator_bridge.set_registry(None)


@pytest.fixture
def provider(monkeypatch):
    calls = []

    async def acompletion(**kwargs):
        calls.append(kwargs)
        return reply({"intent": "new_model", "subject": {"family": "truck", "kind": "pickup"}, "colours": {"body": "Green"}})
    monkeypatch.setattr(litellm, "acompletion", acompletion)

    async def params(llm_id=None):
        return HAIKU
    monkeypatch.setattr(understand, "model_params", params)
    monkeypatch.setenv("LDRAW_NOVA_EUR_PER_USD", "1")
    return calls


def test_describe_and_estimate_write_the_request_log(database, registry, provider):
    free = asyncio.run(fast_path.describe("make the roof blue", current=HOUSE))
    assert free["route"] == "edit" and free["understood_by"] == "rules"
    guess = fast_path.rules_guess("Build a small red car with four black wheels")
    with database.session_scope() as session:
        rows = session.scalars(select(RequestLog).order_by(RequestLog.id)).all()
        assert [r.surface for r in rows] == ["describe", "estimate"]
        edit, estimate = rows
        assert (edit.route, edit.decided_by, edit.generator, edit.build_kind, edit.usage_event_id) == ("edit", "rules", "house", "generator", None)
        assert edit.latency_ms is not None and edit.fields["n_edits"] == len(free["changes"]) and edit.text_len == len("make the roof blue")
        assert (estimate.route, estimate.decided_by) == (guess["route"], "rules") and estimate.fields["confidence"] == guess["confidence"]
        assert session.scalar(select(UsageEvent.id)) is None                      # no model call, no cost event


# ---------------------------------------------------------------- cost history

def test_usage_event_stores_tokens_cost_and_unpriced_as_null(database, monkeypatch):
    monkeypatch.setenv("LDRAW_NOVA_EUR_PER_USD", "0.9")
    priced = usage_log.record("agent", {"prompt": 100, "completion": 10, "cache_read": 5, "cache_write": 2, "cost_usd": 0.012345678},
                              "anthropic/claude-opus-5-5", chat_id="b-my-house", message_id=7)
    unpriced = usage_log.record("worker", {"prompt": 50, "completion": 5}, "openai/x", chat_id="c1", worker_id="w1")
    assert usage_log.record("agent", None, "m") is None and usage_log.record("nonsense", {"prompt": 1}, "m") is None
    with database.session_scope() as session:
        a, b = session.get(UsageEvent, priced), session.get(UsageEvent, unpriced)
        assert (a.kind, a.chat_id, a.build_id, a.message_id, a.model) == ("agent", "b-my-house", "my-house", 7, "anthropic/claude-opus-5-5")
        assert (a.prompt, a.completion, a.cache_read, a.cache_write) == (100, 10, 5, 2)
        assert a.cost_usd == Decimal("0.012346") and a.eur_per_usd == Decimal("0.9")
        assert (b.kind, b.worker_id, b.build_id, b.cost_usd) == ("worker", "w1", None, None)


def test_deleting_the_chat_keeps_its_spend(database, store):
    chat = store.create_chat()
    usage_log.record("agent", {"prompt": 1, "completion": 1, "cost_usd": 1.0}, "m", chat_id=chat["id"])
    store.delete_chat(chat["id"])
    assert usage_log.costs()["totals"]["cost_usd"] == 1.0


def test_understanding_call_cost_is_recorded_and_linked_to_the_request_log(database, registry, provider):
    decision = asyncio.run(fast_path.describe("a green pickup with a white roof", build_id="b1"))
    assert decision["understood_by"] == "llm" and decision["understanding_cost_eur"] > 0
    with database.session_scope() as session:
        event = session.scalars(select(UsageEvent)).one()
        row = session.scalars(select(RequestLog)).one()
        assert event.kind == "understand" and event.model == HAIKU["model"] and event.prompt == 2400 and event.completion == 250
        assert event.cost_usd is not None and float(event.cost_usd) * 1.0 == pytest.approx(decision["understanding_cost_eur"], abs=1e-6)
        assert row.usage_event_id == event.id and row.decided_by == "llm" and row.fields["understanding_cost_eur"] > 0
    # the same for a request that needs a second attempt: one event, summed over attempts
    assert asyncio.run(understand.understand("hello there", params=HAIKU, use_rules=False)).usage_event_id


def test_planner_steps_are_recorded_as_agent_events_with_their_message_id(database, monkeypatch, store):
    entry = llm_config.create({"model_name": "opus", "litellm_params": {"model": "anthropic/claude-opus-5-5", "api_key": "k"},
                               "capabilities": {"tools": True, "vision": True}})
    fake, calls = scripted([[chunk(content="done")]])
    monkeypatch.setattr(litellm, "acompletion", fake)
    monkeypatch.setattr(agent_loop, "usage_record", lambda response: {"prompt": 1000, "completion": 100, "cache_read": 0, "cache_write": 0})
    monkeypatch.setenv("LDRAW_NOVA_EUR_PER_USD", "1")
    chat = store.create_chat()

    async def run():
        await agent.start_turn(store, chat["id"], "hello", entry["id"], {"permissions": "full"})
        await run_state._runs[chat["id"]].task
    asyncio.run(run())
    messages = store.messages(chat["id"])
    reply_message = next(m for m in messages if m.get("_usage"))
    with database.session_scope() as session:
        event = session.scalars(select(UsageEvent)).one()
    assert (event.kind, event.chat_id, event.message_id) == ("agent", chat["id"], reply_message["id"])
    assert float(event.cost_usd) == pytest.approx(reply_message["_usage"]["cost_usd"], abs=1e-6)
    # the per-chat view in the messages is unchanged
    assert usage_log.costs()["totals"]["cost_usd"] == pytest.approx(reply_message["_usage"]["cost_usd"], abs=1e-6)


def test_worker_steps_are_recorded_as_worker_events(database, monkeypatch, tmp_path):
    entry = llm_config.create({"model_name": "haiku", "litellm_params": {"model": "anthropic/claude-haiku-5-5", "api_key": "k"},
                               "capabilities": {"tools": False, "vision": False}})
    fake, _calls = scripted([[chunk(content="all done")]])
    monkeypatch.setattr(litellm, "acompletion", fake)
    monkeypatch.setattr(agent_loop, "usage_record", lambda response: {"prompt": 10, "completion": 5, "cache_read": 0, "cache_write": 0})
    sink = agent_loop.FileSink(tmp_path / "w1.jsonl")
    ended = asyncio.run(agent_loop.run_steps(
        entry=entry, params={"model": "anthropic/claude-haiku-5-5"}, sink=sink, tools=None, max_steps=3,
        build_messages=lambda stored: [{"role": "user", "content": "go"}], run_tool=None, ref=lambda p: str(p),
        usage_context={"kind": "worker", "chat_id": "c9", "worker_id": "w1"}))
    assert ended == "done"
    with database.session_scope() as session:
        event = session.scalars(select(UsageEvent)).one()
    assert (event.kind, event.chat_id, event.worker_id, event.message_id) == ("worker", "c9", "w1", None)


# ---------------------------------------------------------------- GET /api/costs

@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_costs_api_groups_by_day_and_kind_with_inclusive_dates(database, client, monkeypatch):
    monkeypatch.setenv("LDRAW_NOVA_EUR_PER_USD", "0.5")

    def at(day, hour=12):
        return dt.datetime(2026, 10, day, hour, tzinfo=dt.timezone.utc)
    with database.session_scope() as session:
        session.add_all([
            UsageEvent(ts=at(1), kind="agent", cost_usd=Decimal("1.5")), UsageEvent(ts=at(1, 23), kind="agent", cost_usd=Decimal("0.5")),
            UsageEvent(ts=at(1), kind="understand", cost_usd=Decimal("0.01")), UsageEvent(ts=at(2), kind="worker", cost_usd=None),
            UsageEvent(ts=at(3), kind="agent", cost_usd=Decimal("2"))])
    everything = client.get("/api/costs").json()
    assert [(d["day"], d["kind"], d["calls"], d["cost_usd"], d["cost_eur"], d["unpriced_calls"]) for d in everything["days"]] == [
        ("2026-10-01", "agent", 2, 2.0, 1.0, 0), ("2026-10-01", "understand", 1, 0.01, 0.005, 0),
        ("2026-10-02", "worker", 1, 0.0, 0.0, 1), ("2026-10-03", "agent", 1, 2.0, 1.0, 0)]
    assert everything["totals"] == {"calls": 5, "cost_usd": 4.01, "cost_eur": 2.005, "unpriced_calls": 1}
    window = client.get("/api/costs", params={"from": "2026-10-02", "to": "2026-10-02"}).json()
    assert [d["day"] for d in window["days"]] == ["2026-10-02"] and window["from"] == "2026-10-02"
    assert [d["day"] for d in client.get("/api/costs", params={"from": "2026-10-03"}).json()["days"]] == ["2026-10-03"]
    assert client.get("/api/costs", params={"from": "yesterday"}).status_code == 422
    assert client.get("/api/costs", params={"from": "2026-10-05", "to": "2026-10-01"}).status_code == 422


def test_costs_api_is_503_without_a_database(client):
    import db
    db.reset()
    assert client.get("/api/costs").status_code == 503
    assert client.get("/api/environment").json()["database"]["state"] == "off"


def test_request_log_switch_endpoint(database, client):
    assert client.get("/api/request-log/settings").json() == {"keep_request_text": False, "retention_days": 180}
    assert client.put("/api/request-log/settings", json={"keep_request_text": True}).json()["keep_request_text"] is True
    assert client.get("/api/request-log/settings").json()["keep_request_text"] is True


def test_environment_reports_the_database(database, client):
    state = client.get("/api/environment").json()["database"]
    assert state["state"] == "ok" and "PostgreSQL" in state["detail"]
    database.configure("postgresql+psycopg://nobody:x@127.0.0.1:1/none")
    assert client.get("/api/environment").json()["database"]["state"] == "unavailable"


def test_revision_0002_adds_the_draft_extra_column_and_frees_proposal_chat(fresh_database):
    """0002 on top of 0001: build_draft.extra exists with its default, proposal.chat_id has no foreign key, and it downgrades."""
    from alembic import command
    engine = create_engine(fresh_database)
    migrate.upgrade(engine, "0001")
    with engine.connect() as connection:
        connection.execute(text("insert into build (id, title) values ('b', 'B')"))
        connection.execute(text("insert into build_draft (build_id) values ('b')"))
        connection.commit()
    migrate.upgrade(engine)
    with engine.connect() as connection:
        assert connection.execute(text("select extra from build_draft")).scalar_one() == {}
        connection.execute(text("insert into proposal (id, build_id, chat_id) values ('0123456789ab', 'b', 'no-such-chat')"))
        connection.commit()
        command.downgrade(migrate.alembic_config(connection), "0001")
        connection.commit()
        assert connection.execute(text("select chat_id from proposal")).scalar_one() is None       # the dangling id is cleared
        assert "extra" not in {c["name"] for c in inspect(connection).get_columns("build_draft")}
    engine.dispose()
