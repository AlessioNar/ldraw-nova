"""Cost per step, the euro totals and the per-chat budget."""
import asyncio

import litellm
import pytest

import agent
import llm_config
import model_catalog
import run_state
import settings
import usage
from test_agent import chunk, scripted

OPUS = {"input": 4, "output": 20, "currency": "USD"}


def record(prompt, completion, read=0, write=0):
    return {"prompt": prompt, "completion": completion, "cache_read": read, "cache_write": write}


def test_anthropic_step_cost_weights_cache_reads_and_writes():
    # 1000 fresh + 8000 read (0.1x) + 1000 write (1.25x) input tokens, 500 output; prompt includes the cached ones
    step = record(10_000, 500, read=8000, write=1000)
    cost = usage.step_cost_usd(step, OPUS, "anthropic/claude-opus-5-5", "5m")
    assert cost == pytest.approx((1000 + 800 + 1250) * 4 / 1e6 + 500 * 20 / 1e6)
    one_hour = usage.step_cost_usd(step, OPUS, "anthropic/claude-opus-5-5", "1h")
    assert one_hour == pytest.approx((1000 + 800 + 2000) * 4 / 1e6 + 500 * 20 / 1e6)


def test_other_providers_pay_full_price_for_cached_tokens():
    step = record(10_000, 500, read=8000)
    cost = usage.step_cost_usd(step, OPUS, "openai/gpt-6-luna")
    assert cost == pytest.approx(10_000 * 4 / 1e6 + 500 * 20 / 1e6)          # no discount assumed: never understates


def test_unknown_or_foreign_prices_give_no_cost():
    step = record(100, 10)
    assert usage.step_cost_usd(step, None, "anthropic/x") is None
    assert usage.step_cost_usd(step, {"input": None, "output": 1, "currency": "USD"}, "anthropic/x") is None
    assert usage.step_cost_usd(step, {"input": 1, "output": 1, "currency": "EUR"}, "anthropic/x") is None


def test_totals_sum_the_stored_costs_and_convert_to_euros(monkeypatch):
    monkeypatch.setenv("LDRAW_NOVA_EUR_PER_USD", "0.5")
    messages = [{"role": "assistant", "_usage": {**record(10, 1), "cost_usd": 1.5}},
                {"role": "assistant", "_usage": {**record(10, 1), "cost_usd": 0.5}},
                {"role": "assistant", "_usage": record(10, 1)}]               # a step from before prices were stored
    totals = usage.usage_totals(messages)
    assert totals["cost_usd"] == 2.0 and totals["cost_eur"] == 1.0 and totals["eur_per_usd"] == 0.5
    assert totals["unpriced_steps"] == 1


def test_exchange_rate_falls_back_on_bad_values(monkeypatch):
    for bad in ("", "abc", "-1", "0"):
        monkeypatch.setenv("LDRAW_NOVA_EUR_PER_USD", bad)
        assert settings.eur_per_usd() == 0.92


def test_budget_validation():
    priced = {"litellm_params": {"model": "anthropic/claude-opus-5-5"}}
    assert model_catalog.validate_options(priced, {})["budget_eur"] is None
    assert model_catalog.validate_options(priced, {"budget_eur": 2})["budget_eur"] == 2
    for bad in (0, -1, 1001, True, "2"):
        with pytest.raises(ValueError, match="budget"):
            model_catalog.validate_options(priced, {"budget_eur": bad})
    unpriced = {"litellm_params": {"model": "custom/none"}}
    assert model_catalog.validate_options(unpriced, {})["budget_eur"] is None
    with pytest.raises(ValueError, match="no known prices"):
        model_catalog.validate_options(unpriced, {"budget_eur": 2})


def test_turn_stops_before_calling_the_model_once_the_budget_is_spent(monkeypatch, store):
    entry = llm_config.create({"model_name": "opus", "litellm_params": {"model": "anthropic/claude-opus-5-5", "api_key": "k"},
                               "capabilities": {"tools": True, "vision": True}})
    fake, calls = scripted([[chunk(content="ok")]])
    monkeypatch.setattr(litellm, "acompletion", fake)
    monkeypatch.setenv("LDRAW_NOVA_EUR_PER_USD", "1")
    chat = store.create_chat()
    store.add_message(chat["id"], {"role": "assistant", "content": "earlier", "_usage": {**record(10, 1), "cost_usd": 2.5}})

    async def run(budget):
        await agent.start_turn(store, chat["id"], "continue", entry["id"], {"permissions": "full", "budget_eur": budget})
        await run_state._runs[chat["id"]].task

    asyncio.run(run(2.0))
    notice = store.messages(chat["id"])[-1]
    assert calls == [] and notice["_notice"] and "Budget reached" in notice["content"] and "€2.50 of €2.00" in notice["content"]
    # A larger budget lets the next turn go through.
    asyncio.run(run(5.0))
    assert len(calls) == 1
