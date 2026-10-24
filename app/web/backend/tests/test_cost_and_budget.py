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


def test_price_book_matches_the_pricing_page_and_reaches_the_profile():
    haiku = model_catalog.profile("anthropic/claude-haiku-5-5")["pricing"]
    assert (haiku["input"], haiku["output"], haiku["currency"]) == (0.1, 0.5, "USD")
    assert haiku["tiers"] == [{"over_prompt_tokens": 100_000, "input": 0.5, "output": 2.5}]
    assert model_catalog.profile("anthropic/claude-opus-5-5")["pricing"]["cache_read_x"] == 0.05
    assert model_catalog.profile("anthropic/claude-sonnet-5-5")["pricing"]["input"] == 2
    assert model_catalog.profile("anthropic/claude-opus-5-5")["pricing"]["note"]          # the catalogue's own fields survive


def test_opus_and_sonnet_55_cache_reads_cost_a_twentieth():
    pricing = model_catalog.profile("anthropic/claude-opus-5-5")["pricing"]
    cost = usage.step_cost_usd(record(1_000_000, 0, read=1_000_000), pricing, "anthropic/claude-opus-5-5")
    assert cost == pytest.approx(0.20)                                                  # $0.20 per MTok read, per the pricing page


def test_haiku_55_prompts_over_100k_tokens_cost_five_times_more():
    pricing = model_catalog.profile("anthropic/claude-haiku-5-5")["pricing"]
    short = usage.step_cost_usd(record(100_000, 1000), pricing, "anthropic/claude-haiku-5-5")
    long = usage.step_cost_usd(record(100_001, 1000), pricing, "anthropic/claude-haiku-5-5")
    assert short == pytest.approx(100_000 * 0.1 / 1e6 + 1000 * 0.5 / 1e6)
    assert long == pytest.approx(100_001 * 0.5 / 1e6 + 1000 * 2.5 / 1e6)
    read = usage.step_cost_usd(record(200_000, 0, read=200_000), pricing, "anthropic/claude-haiku-5-5")
    assert read == pytest.approx(200_000 * 0.1 * 0.5 / 1e6)                              # $0.05 per MTok read above 100k


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


def test_budget_is_per_request_not_per_chat(monkeypatch, store):
    """Earlier requests' cost does not count against a new request; a follow-up keeps its full budget."""
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

    asyncio.run(run(2.0))                       # the chat already cost 2.50, yet this request has its own 2.00
    assert len(calls) == 1
    assert not any(m.get("_budget_reached") for m in store.messages(chat["id"]))


def test_ledger_counts_only_the_current_request(monkeypatch):
    import budget
    monkeypatch.setenv("LDRAW_NOVA_EUR_PER_USD", "1")
    old = [{"role": "assistant", "_usage": {**record(1, 1), "cost_usd": 3.0}}]
    ledger = budget.BudgetLedger(2.0)
    assert ledger.remaining_eur(old) == -1.0    # no request started: whole chat counts (old behaviour)
    ledger.begin_request(old)
    assert ledger.remaining_eur(old) == 2.0 and ledger.planner_spent_eur(old) == 0.0
    both = old + [{"role": "assistant", "_usage": {**record(1, 1), "cost_usd": 0.5}}]
    assert ledger.remaining_eur(both) == 1.5
    assert ledger.reserve("w1", 5.0, both) == 1.5      # workers reserve from what is left of the request budget


def test_turn_stats_report_the_requests_cost(monkeypatch):
    monkeypatch.setenv("LDRAW_NOVA_EUR_PER_USD", "1")
    messages = [{"role": "user", "content": "a"}, {"role": "assistant", "_usage": {**record(1, 1), "cost_usd": 1.5}},
                {"role": "user", "content": "b"}, {"role": "assistant", "_usage": {**record(1, 1), "cost_usd": 0.25}}]
    stats = usage.turn_stats(messages)
    assert stats["request_cost_eur"] == 0.25 and usage.usage_totals(messages)["cost_eur"] == 1.75


def test_budget_stops_a_turn_between_steps_not_only_before_the_first(monkeypatch, store):
    """Step 1 uses a tool and pushes the chat over its budget: the model must not be called a second time."""
    import importlib
    entry = llm_config.create({"model_name": "opus", "litellm_params": {"model": "anthropic/claude-opus-5-5", "api_key": "k"},
                               "capabilities": {"tools": True, "vision": True}})
    from test_agent import tool_call_chunks
    fake, calls = scripted([[*tool_call_chunks("c1", "list_files", {})], [chunk(content="should not be asked")]])
    monkeypatch.setattr(litellm, "acompletion", fake)
    monkeypatch.setenv("LDRAW_NOVA_EUR_PER_USD", "1")
    for name in ("agent_loop", "turn"):                    # wherever the step loop lives in this version
        try:
            module = importlib.import_module(name)
        except ImportError:
            continue
        if hasattr(module, "usage_record"):
            monkeypatch.setattr(module, "usage_record", lambda response: {"prompt": 1000, "completion": 1000, "cache_read": 0, "cache_write": 0})
    chat = store.create_chat()

    async def run():
        await agent.start_turn(store, chat["id"], "build", entry["id"], {"permissions": "full", "budget_eur": 0.01})
        await run_state._runs[chat["id"]].task

    asyncio.run(run())
    messages = store.messages(chat["id"])
    assert len(calls) == 1, "the second step must not run after the budget was exceeded"
    assert any(m.get("_usage", {}).get("cost_usd", 0) > 0.01 for m in messages)
    assert messages[-1].get("_notice") and "Budget reached" in messages[-1]["content"]
    assert messages[-1]["_budget_reached"]["limit_eur"] == 0.01 and messages[-1]["_budget_reached"]["spent_eur"] >= 0.01
