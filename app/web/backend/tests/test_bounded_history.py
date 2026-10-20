"""bounded_history equivalence with the previous implementation, and reasoning coalescing."""
import copy
import json
import os
import random
from pathlib import Path

import litellm
import pytest

import inference
import providers
from history import llm_history

CHATS = Path(os.environ.get("LDRAW_NOVA_RECORDED_CHATS", "/nonexistent"))


def _old_bounded_history(messages, model, budget):
    """Verbatim copy of bounded_history on origin/master before this change."""
    result = copy.deepcopy(messages)
    removed = 0

    def count():
        try:
            return litellm.token_counter(model=model, messages=result)
        except Exception:
            return len(json.dumps(result).encode())

    while budget and count() > budget - 4096:
        starts = [i for i, m in enumerate(result) if m.get("_turn_start", m["role"] == "user")]
        if len(starts) < 2:
            rounds = [i for i, m in enumerate(result) if m.get("tool_calls")]
            if len(rounds) <= 2:
                raise ValueError("The latest turn exceeds the context budget. Increase it or start a new chat.")
            begin, end = rounds[0], rounds[1]
            removed += end - begin
            result[begin:end] = []
            note = "\nEarlier completed tool rounds were omitted to fit context. Consult the current NOTES.md and saved output reports; do not assume omitted checks passed."
            if note not in inference._system_tail(result[0]):
                inference._append_system(result[0], note)
            continue
        end = starts[1]
        removed += end - 1
        result = [result[0], *result[end:]]
        while len(result) > 1 and result[1]["role"] == "tool":
            result.pop(1)
    for message in result:
        message.pop("_turn_start", None)
    return result, removed


def _both(messages, model, budget):
    snapshot = copy.deepcopy(messages)
    outcomes = []
    for fn in (_old_bounded_history, inference.bounded_history):
        try:
            outcomes.append(fn(messages, model, budget))
        except ValueError as exc:
            outcomes.append(str(exc))
        assert messages == snapshot  # input is never mutated
    return outcomes


def _synthetic(rng, cache_blocks):
    system = {"role": "system", "content": [{"type": "text", "text": "stable " * 50, "cache_control": {"type": "ephemeral"}},
                                            {"type": "text", "text": "tail"}] if cache_blocks else "system prompt"}
    messages = [system]
    for turn in range(rng.randint(1, 5)):
        messages.append({"role": "user", "content": "task %d " % turn + "x" * rng.randint(1, 3000), "_turn_start": True})
        for step in range(rng.randint(0, 6)):
            call = f"c{turn}-{step}"
            messages.append({"role": "assistant", "content": None, "_llm_model": "m",
                             "tool_calls": [{"id": call, "type": "function", "function": {"name": "run_shell", "arguments": json.dumps({"cmd": "ls " * rng.randint(1, 50)})}}]})
            messages.append({"role": "tool", "tool_call_id": call, "name": "run_shell", "content": "out " * rng.randint(1, 800)})
            if rng.random() < 0.3:
                messages.append({"role": "user", "content": "Renders produced by the tool calls above:", "_turn_start": False})
        messages.append({"role": "assistant", "content": "answer " * rng.randint(1, 400)})
    return messages


@pytest.mark.parametrize("cache_blocks", [False, True])
def test_matches_previous_implementation_on_synthetic_histories(cache_blocks):
    rng = random.Random(7)
    trimmed = 0
    for _ in range(60):
        messages = _synthetic(rng, cache_blocks)
        for budget in (None, 0, 100_000, 6000, 5000, 4500):
            old, new = _both(messages, "fake", budget)
            assert old == new
            trimmed += isinstance(new, tuple) and bool(new[1])
    assert trimmed > 20  # the trimming paths were exercised


def test_matches_previous_implementation_without_tokenizer(monkeypatch):
    def broken(**kwargs):
        raise RuntimeError("no tokenizer")
    monkeypatch.setattr(litellm, "token_counter", broken)
    rng = random.Random(3)
    for _ in range(20):
        messages = _synthetic(rng, True)
        for budget in (None, 100_000, 6000):
            old, new = _both(messages, "fake", budget)
            assert old == new


def test_repeated_steps_reuse_cached_counts(monkeypatch):
    messages = _synthetic(random.Random(1), False)
    inference.bounded_history(messages, "fake", 100_000)
    calls = []
    real = litellm.token_counter
    monkeypatch.setattr(litellm, "token_counter", lambda **kw: calls.append(1) or real(**kw))
    inference.bounded_history(messages, "fake", 100_000)
    assert calls == []
    messages.append({"role": "user", "content": "one more", "_turn_start": True})
    inference.bounded_history(messages, "fake", 100_000)
    assert len(calls) == 1


def test_under_budget_does_not_deepcopy(monkeypatch):
    monkeypatch.setattr(copy, "deepcopy", lambda *a: pytest.fail("deepcopy under budget"))
    messages = _synthetic(random.Random(2), True)
    result, removed = inference.bounded_history(messages, "fake", 1_000_000)
    assert removed == 0 and all("_turn_start" not in m for m in result)
    result[0]["content"][-1]["text"] += "mutated"  # the system blocks are the caller's own copy
    assert not messages[0]["content"][-1]["text"].endswith("mutated")


def test_reasoning_details_are_counted_and_can_trigger_trimming():
    block = [{"type": "reasoning.text", "text": "word " * 20, "format": "unknown", "index": 0} for _ in range(300)]
    messages = [{"role": "system", "content": "s"},
                {"role": "user", "content": "old", "_turn_start": True},
                {"role": "assistant", "content": "a", "reasoning_details": copy.deepcopy(block)},
                {"role": "user", "content": "new", "_turn_start": True}]
    old, new = _both(messages, "fake", 8000)
    assert old[1] == 0            # the old counter does not see the reasoning
    assert new[1] > 0 and new[0][1]["content"] == "new"
    assert not any(m.get("reasoning_details") for m in new[0])
    plain = [dict(m) for m in messages]
    plain[2].pop("reasoning_details")
    assert inference.reasoning_text(messages[2]) == "word " * 6000
    assert _both(plain, "fake", 8000)[0] == _both(plain, "fake", 8000)[1]


@pytest.mark.skipif(not CHATS.is_dir(), reason="recorded chats not available (LDRAW_NOVA_RECORDED_CHATS)")
def test_matches_previous_implementation_on_recorded_chats():
    checked = trimmed = 0
    for chat in ("20260928-125940-5e681c", "20260929-213629-dd4aed", "20260930-201212-16f983"):
        rows = [json.loads(line) for line in (CHATS / chat / "messages.jsonl").read_text().splitlines()]
        model = next(m["_llm_model"] for m in rows if m.get("_llm_model"))
        assert not providers.of(model).reasoning_details
        for end in range(2, len(rows) + 1, 9):
            history = llm_history(rows[:end], False, lambda ref: None, model, mark_turns=True)
            messages = [{"role": "system", "content": "system"}, *history]
            for budget in (1_000_000, 60_000, 30_000):
                old, new = _both(messages, model, budget)
                assert old == new
                checked += 1
                trimmed += isinstance(new, tuple) and bool(new[1])
    assert checked > 100 and trimmed > 10


def _blocks(chat):
    rows = [json.loads(line) for line in (CHATS / chat / "messages.jsonl").read_text().splitlines()]
    return [m["_reasoning_details"] for m in rows if m.get("_reasoning_details")]


@pytest.mark.skipif(not CHATS.is_dir(), reason="recorded chats not available (LDRAW_NOVA_RECORDED_CHATS)")
@pytest.mark.parametrize("chat", ["20261003-100404-9e6e55", "20261003-110049-79b3d1"])
def test_coalescing_recorded_reasoning_is_lossless(chat):
    for details in _blocks(chat):
        merged = inference.coalesce_reasoning_details(details)
        assert "".join(d["text"] for d in merged) == "".join(d["text"] for d in details)
        assert len(merged) < len(details)
        assert {(d["type"], d["format"], d["index"]) for d in merged} == {(d["type"], d["format"], d["index"]) for d in details}


def test_coalescing_only_merges_plain_adjacent_text_and_keeps_order():
    t = lambda text, **kw: {"type": "reasoning.text", "text": text, "format": "unknown", "index": 0, **kw}
    enc = {"type": "reasoning.encrypted", "data": "abc", "format": "unknown", "index": 1}
    summary = {"type": "reasoning.summary", "summary": "s", "format": "unknown", "index": 2}
    signed = t("sig", signature="x")
    details = [t("a"), t("b"), enc, t("c"), t("d", index=1), t("e", index=1), summary, summary, signed, signed, t("f", id="i"), t("g", id="i"),
               t("h", id="j"), t("i", format="other"), {"type": "reasoning.text"}, "odd", t("j"), t("k")]
    original = copy.deepcopy(details)
    assert inference.coalesce_reasoning_details(details) == [
        t("ab"), enc, t("c"), t("de", index=1), summary, summary, signed, signed, t("fg", id="i"),
        t("h", id="j"), t("i", format="other"), {"type": "reasoning.text"}, "odd", t("jk")]
    assert details == original  # inputs are not mutated
    assert inference.coalesce_reasoning_details([]) == []
