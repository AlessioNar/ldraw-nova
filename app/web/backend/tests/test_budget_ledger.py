"""The shared budget ledger (docs/plans/done/swarm-subagents/04-shared-budget.md): planner plus workers under one cap."""
import asyncio
import threading

import litellm
import pytest

import agent_loop
import budget
import llm_config
import model_catalog
import settings
import subagents
import usage
from test_agent import chunk, scripted, tool_call_chunks
from test_subagents import delegate, save_result


@pytest.fixture
def worker(monkeypatch):
    entry = llm_config.create({"model_name": "worker", "litellm_params": {"model": "openai/gpt-6-luna", "api_key": "sk-x"},
                               "capabilities": {"tools": True, "vision": False}})
    monkeypatch.setenv("LDRAW_NOVA_SUBAGENTS", "1")
    monkeypatch.setenv("LDRAW_NOVA_WORKER_LLM_ID", entry["id"])
    real = model_catalog.entry_profile
    monkeypatch.setattr(model_catalog, "entry_profile", lambda e: {**real(e), "pricing": {"input": 10.0, "output": 10.0, "currency": "USD"}})
    return entry


def _flat_usage(monkeypatch, prompt=10000, completion=10000):
    """Every step bills the same: with the test price of $10/M and 1 EUR per USD that is 0.2 EUR for the defaults."""
    monkeypatch.setattr(agent_loop, "usage_record", lambda response: {"prompt": prompt, "completion": completion,
                                                                      "cache_read": 0, "cache_write": 0})
    monkeypatch.setenv("LDRAW_NOVA_EUR_PER_USD", "1")


def _rec(usd, **extra):
    return {"role": "assistant", "_usage": {"prompt": 1, "completion": 1, "cache_read": 0, "cache_write": 0, "cost_usd": usd}, **extra}


def test_two_concurrent_workers_share_one_budget(monkeypatch, worker, ctx):
    _flat_usage(monkeypatch)
    ctx.store.add_message(ctx.chat_id, {**_rec(0.10), "content": "plan"})
    ctx.options = {"permissions": "full", "budget_eur": 0.50}
    fake, calls = scripted([[*tool_call_chunks(f"w{i}", "list_files", {})] for i in range(40)])
    monkeypatch.setattr(litellm, "acompletion", fake)

    async def both():
        return await asyncio.gather(*(subagents.delegate(ctx, role="builder", task="t", max_eur=0.30) for _ in range(2)))
    results = asyncio.run(both())
    assert all("status over_budget" in r.content for r in results)
    for r in results:
        save_result(ctx, r)
    totals = usage.usage_totals(ctx.store.messages(ctx.chat_id))
    assert totals["cost_eur"] <= 0.50 + 0.2 + 1e-9 and totals["worker_cost_eur"] > 0       # one step of overshoot at most
    assert len(calls) <= 4 and sum(r.record["_worker"]["steps"] for r in results) == len(calls)


def test_ledger_reservations_clamp_and_stay_thread_safe(monkeypatch):
    monkeypatch.setenv("LDRAW_NOVA_EUR_PER_USD", "1")
    ledger = budget.BudgetLedger(0.50)
    stored = [_rec(0.10)]
    granted = []
    threads = [threading.Thread(target=lambda i=i: granted.append(ledger.reserve(f"w{i}", 0.30, stored))) for i in range(5)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert round(sum(granted), 6) == 0.40 and max(granted) == 0.30 and ledger.remaining_eur(stored) == pytest.approx(0)
    ledger.add("w0", 0.05)
    ledger.release("w0")
    assert ledger.live_eur == pytest.approx(0.05)
    # saving a record with the worker's id moves its cost from the ledger into the stored totals, never both
    assert ledger.planner_spent_eur(stored) == pytest.approx(0.15)
    assert ledger.planner_spent_eur(stored + [_rec(0.05, role="tool", _worker={"id": "w0"})]) == pytest.approx(0.15)


def test_planner_check_counts_unsaved_worker_cost_and_old_chats_still_total(monkeypatch, worker, ctx):
    _flat_usage(monkeypatch)
    ctx.options = {"permissions": "full", "budget_eur": 5.0}
    fake, _calls = scripted([[chunk(content="done")]])
    monkeypatch.setattr(litellm, "acompletion", fake)
    result = delegate(ctx, role="researcher", task="t")
    assert ctx.ledger.planner_spent_eur(ctx.store.messages(ctx.chat_id)) == pytest.approx(0.2)   # record not yet saved
    save_result(ctx, result)
    assert ctx.ledger.planner_spent_eur(ctx.store.messages(ctx.chat_id)) == pytest.approx(0.2)
    # a chat from before the ledger: hidden `_subagent` steps still count in the totals
    old = [_rec(0.5, _ui_only=True, _hidden=True, _subagent=True, _worker="ab12")]
    totals = usage.usage_totals(old)
    assert totals["cost_eur"] == pytest.approx(0.5 * settings.eur_per_usd(), abs=1e-3)
    assert totals["worker_cost_eur"] == totals["cost_eur"]
    assert usage.usage_totals([])["worker_cost_eur"] == 0


def test_killed_worker_is_still_counted(monkeypatch, worker, ctx):
    _flat_usage(monkeypatch)
    ctx.options = {"permissions": "full", "budget_eur": 5.0}
    seen = []
    first, _ = scripted([[*tool_call_chunks("w1", "list_files", {})]])

    async def hang_second(**kwargs):
        seen.append(1)
        if len(seen) == 1:
            return await first(**kwargs)
        await asyncio.sleep(3600)
    monkeypatch.setattr(litellm, "acompletion", hang_second)

    async def go():
        task = asyncio.ensure_future(subagents.delegate(ctx, role="builder", task="t", max_eur=1.0))
        while len(seen) < 2 and not task.done():
            await asyncio.sleep(0.01)
        assert not task.done(), task.result()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(go())
    [record] = [m for m in ctx.store.messages(ctx.chat_id) if m.get("_worker")]
    assert record["_worker"]["status"] == "cancelled" and record["_ui_only"] and record["_usage"]["cost_usd"] > 0
    assert usage.usage_totals(ctx.store.messages(ctx.chat_id))["cost_eur"] > 0


def test_unpriced_worker_is_refused_under_a_budget(monkeypatch, worker, ctx):
    real = model_catalog.entry_profile
    monkeypatch.setattr(model_catalog, "entry_profile", lambda e: {**real(e), "pricing": None})
    fake, calls = scripted([[chunk(content="done")]])
    monkeypatch.setattr(litellm, "acompletion", fake)
    ctx.options = {"permissions": "full", "budget_eur": 1.0}
    with pytest.raises(subagents.ToolError, match="no known price"):
        asyncio.run(subagents.delegate(ctx, role="researcher", task="t"))
    assert not calls
    ctx.options = {"permissions": "full"}                                          # no budget: allowed
    assert "status" in asyncio.run(subagents.delegate(ctx, role="researcher", task="t")).content
