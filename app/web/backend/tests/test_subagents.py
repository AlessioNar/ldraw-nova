import asyncio

import litellm
import pytest

import llm_config
import model_catalog
import settings
import subagents
import tool_policy
import usage
from test_agent import chunk, scripted, tool_call_chunks


@pytest.fixture
def worker(monkeypatch):
    entry = llm_config.create({"model_name": "worker", "litellm_params": {"model": "openai/gpt-6-luna", "api_key": "sk-x"},
                               "capabilities": {"tools": True, "vision": False}})
    monkeypatch.setenv("LDRAW_NOVA_SUBAGENTS", "1")
    monkeypatch.setenv("LDRAW_NOVA_WORKER_LLM_ID", entry["id"])
    monkeypatch.setattr(model_catalog, "entry_profile", lambda e: {"pricing": {"input": 1000.0, "output": 1000.0, "currency": "USD"}})
    return entry


def names(options=None):
    return {t["function"]["name"] for t in tool_policy.available_tools(options or {})}


def delegate(ctx, **args):
    return asyncio.run(subagents.delegate_task(ctx, **args))


def test_tool_absent_unless_flag_and_worker_configured(monkeypatch, worker):
    assert "delegate_task" in names()
    monkeypatch.setenv("LDRAW_NOVA_SUBAGENTS", "0")
    assert "delegate_task" not in names()
    monkeypatch.setenv("LDRAW_NOVA_SUBAGENTS", "1")
    monkeypatch.setenv("LDRAW_NOVA_WORKER_LLM_ID", "")
    assert "delegate_task" not in names()
    monkeypatch.setenv("LDRAW_NOVA_WORKER_LLM_ID", worker["id"])
    assert "delegate_task" not in names({"mode": "plan"})        # read-only modes never delegate


def test_worker_runs_and_cost_lands_in_totals(monkeypatch, worker, ctx):
    fake, calls = scripted([
        [*tool_call_chunks("w1", "write_file", {"path": "w.txt", "content": "hi"})],
        [chunk(content="Wrote the note.")],
    ])
    monkeypatch.setattr(litellm, "acompletion", fake)
    result = delegate(ctx, task="write a note", files=["a.txt"])
    assert "Worker finished" in result.content and "w.txt" in result.content and "Wrote the note." in result.content
    assert (ctx.work_dir / "w.txt").read_text() == "hi"
    assert "a.txt" in calls[0]["messages"][1]["content"]
    assert {t["function"]["name"] for t in calls[0]["tools"]} == set(subagents.WORKER_TOOLS)
    saved = [m for m in ctx.store.messages(ctx.chat_id) if m.get("_subagent")]
    assert len(saved) == 2 and all(m["_usage"].get("cost_usd", 0) > 0 and m["_ui_only"] for m in saved)
    totals = usage.usage_totals(ctx.store.messages(ctx.chat_id))
    assert totals["steps"] == 2 and totals["cost_eur"] > 0


def test_step_cap_and_forbidden_tools(monkeypatch, worker, ctx):
    fake, calls = scripted([
        [*tool_call_chunks("w1", "publish_model", {"path": "x.mpd", "name": "x"})],
        [*tool_call_chunks("w2", "delegate_task", {"task": "again"})],
        [*tool_call_chunks("w3", "list_files", {})],
        [chunk(content="never reached")],
    ])
    monkeypatch.setattr(litellm, "acompletion", fake)
    result = delegate(ctx, task="loop", max_steps=3)
    assert "step cap (3 steps)" in result.content and len(calls) == 3
    tool_msgs = [m["content"] for m in calls[2]["messages"] if m["role"] == "tool"]
    assert all("not available to workers" in t for t in tool_msgs[:2])
    assert not [m for m in ctx.store.models(ctx.chat_id)]


def test_budget_stops_worker(monkeypatch, worker, ctx):
    ctx.options = {"budget_eur": 0.0001}
    ctx.store.add_message(ctx.chat_id, {"role": "assistant", "content": "", "_ui_only": True, "_usage": {
        "prompt": 1, "completion": 1, "cache_read": 0, "cache_write": 0, "cost_usd": 5.0}})
    fake, calls = scripted([[chunk(content="should not run")]])
    monkeypatch.setattr(litellm, "acompletion", fake)
    result = delegate(ctx, task="anything")
    assert "chat budget" in result.content and not calls


def test_summary_truncated(monkeypatch, worker, ctx):
    fake, _calls = scripted([[chunk(content="x" * 5000)]])
    monkeypatch.setattr(litellm, "acompletion", fake)
    assert len(delegate(ctx, task="talk").content) == subagents.SUMMARY_CHARS


def test_settings_default_off(monkeypatch):
    monkeypatch.delenv("LDRAW_NOVA_SUBAGENTS", raising=False)
    assert settings.subagents_enabled() is False
