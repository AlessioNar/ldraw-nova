"""What actually reaches Anthropic: the real turn loop and LiteLLM against a mock Messages API.

MockAnthropic answers 400 to anything the documented schema forbids (cache_control
off a content block, more than four markers, orphaned tool results, empty text
blocks), so every turn that finishes here also proved the request valid.
"""
import asyncio
import json
import os

import litellm
import pytest

import settings
import system_prompt
from mock_providers import MockAnthropic, anthropic_cache_markers, anthropic_problems, reply, run_chat, same_role_runs

MODEL = "anthropic/claude-sonnet-5"
PROGRESS = ("toolu_1", "report_progress", {"summary": "step one"})
EPHEMERAL = {"type": "ephemeral"}


@pytest.fixture(autouse=True)
def writable_data_dir():
    os.chmod(settings.DATA_DIR.parent, 0o755)


def run(store, script, text="hi", options=None, followups=(), model=MODEL):
    with MockAnthropic(script) as server:
        chat_id, events = run_chat(store, {"model": model, "api_base": f"http://127.0.0.1:{server.port}"},
                                   text, options, followups)
    assert not [e for e in events if e[0] == "turn_error"], store.messages(chat_id)[-1]
    return chat_id, events, server


def last_block(body):
    """The block a trailing breakpoint lands on: LiteLLM moves a tool message's marker into the
    tool_result's own content list, so look one level down for those."""
    block = body["messages"][-1]["content"][-1]
    if block["type"] == "tool_result" and isinstance(block["content"], list):
        return block["content"][-1]
    return block


TWO_STEPS = [reply("Looking.", [PROGRESS], usage=dict(input=100, cache_write=5000)),
             reply("Done.", usage=dict(input=20, cache_read=5000, cache_write=30))]


def test_rolling_breakpoint_reaches_the_wire_also_behind_a_tool_result(store):
    chat_id, events, server = run(store, TWO_STEPS)
    first, second = server.bodies()
    # step 1: the user message is last
    assert anthropic_cache_markers(first) == ["system[0]", "messages[0].content[0]"]
    assert first["messages"][0]["content"] == [{"type": "text", "text": "hi", "cache_control": EPHEMERAL}]
    # step 2: the tool result is last; the marker sits on its content block
    assert [m["role"] for m in second["messages"]] == ["user", "assistant", "user"]
    assert second["messages"][2]["content"] == [
        {"type": "tool_result", "tool_use_id": "toolu_1",
         "content": [{"type": "text", "text": "step one", "cache_control": EPHEMERAL}]}]
    assert anthropic_cache_markers(second) == ["system[0]", "messages[2].content[0].content[0]"]
    for body in (first, second):
        assert body["system"][0]["cache_control"] == EPHEMERAL
        assert "cache_control" not in body["system"][1]
        assert last_block(body)["cache_control"] == EPHEMERAL
        assert len(anthropic_cache_markers(body)) <= 4
        assert anthropic_problems(body) == [] and same_role_runs(body) == []
        assert body["stream"] is True


def test_breakpoint_moves_forward_and_never_accumulates(store):
    script = [reply("", [(f"toolu_{i}", "report_progress", {"summary": f"s{i}"})]) for i in range(6)] + [reply("Done.")]
    _, _, server = run(store, script)
    assert len(server.requests) == 7
    for body in server.bodies():
        markers = anthropic_cache_markers(body)
        assert len(markers) == 2 and markers[0] == "system[0]"
        assert markers[1].startswith(f"messages[{len(body['messages']) - 1}]")     # only the last message is marked


def test_parallel_tool_results_share_one_user_turn_marked_on_the_last(store):
    calls = [(f"toolu_{i}", "report_progress", {"summary": f"s{i}"}) for i in range(2)]
    _, _, server = run(store, [reply("", calls), reply("Done.")])
    user = server.bodies()[1]["messages"][2]
    assert [b["tool_use_id"] for b in user["content"]] == ["toolu_0", "toolu_1"]
    assert anthropic_cache_markers(server.bodies()[1]) == ["system[0]", "messages[2].content[1].content[0]"]


def test_stored_history_is_free_of_cache_control(store):
    chat_id, _, _ = run(store, TWO_STEPS + [reply("Again.")], followups=["more"])
    assert "cache_control" not in json.dumps(store.messages(chat_id))
    assert "cache_control" not in (settings.CHATS_DIR / chat_id / "messages.jsonl").read_text()


def test_usage_cache_counters_flow_into_events_and_the_saved_messages(store):
    chat_id, events, _ = run(store, TWO_STEPS)
    # LiteLLM folds Anthropic's cache reads/writes into prompt_tokens
    expected = [{"prompt": 5100, "completion": 5, "cache_read": 0, "cache_write": 5000},
                {"prompt": 5050, "completion": 5, "cache_read": 5000, "cache_write": 30}]
    def plain(records):                                   # the cost is checked in test_cost_and_budget.py
        return [{k: v for k, v in r.items() if k != "cost_usd"} for r in records]
    assert plain(d for name, d in events if name == "usage") == expected
    assert plain(m["_usage"] for m in store.messages(chat_id) if m["role"] == "assistant") == expected


def test_flag_off_removes_only_the_rolling_marker(store, monkeypatch):
    monkeypatch.setattr(settings, "ROLLING_CACHE_BREAKPOINT", False)
    _, _, server = run(store, TWO_STEPS)
    for body in server.bodies():
        assert anthropic_cache_markers(body) == ["system[0]"]
        assert anthropic_problems(body) == []


def test_tail_after_history_keeps_alternating_turns_and_the_cached_prefix(store, monkeypatch):
    monkeypatch.setattr(settings, "TAIL_AFTER_HISTORY", True)
    _, _, server = run(store, TWO_STEPS + [reply("Again.")], followups=["more"])
    first, second, third = server.bodies()
    tail = "[Workspace state for this step, added by the app. It is not a message from the user.]\n\nCurrent output files:"
    for body in (first, second, third):
        # the system turn is just the cached stable block: LiteLLM drops the empty second block
        assert len(body["system"]) == 1 and body["system"][0]["cache_control"] == EPHEMERAL
        assert body["messages"][-1]["content"][-1]["text"].startswith(tail)
        assert "cache_control" not in body["messages"][-1]["content"][-1]
        assert anthropic_problems(body) == [] and same_role_runs(body) == []
        assert len(anthropic_cache_markers(body)) == 2
    # the breakpoint is on the history, i.e. the block just before the tail, in the same user turn
    assert first["messages"][0]["content"][0]["cache_control"] == EPHEMERAL
    result = second["messages"][2]["content"]
    assert [b["type"] for b in result] == ["tool_result", "text"]               # LiteLLM folded the tail into the tool-result turn
    assert result[0]["content"][-1]["cache_control"] == EPHEMERAL
    assert third["messages"][4]["content"][0] == {"type": "text", "text": "more", "cache_control": EPHEMERAL}


def test_prefix_up_to_the_marker_is_stable_between_steps(store):
    """What the previous step cached must be byte-identical in the next request, markers aside."""
    _, _, server = run(store, TWO_STEPS)
    first, second = server.bodies()

    def strip(value):
        if isinstance(value, dict):
            return {k: strip(v) for k, v in value.items() if k != "cache_control"}
        return [strip(v) for v in value] if isinstance(value, list) else value

    assert strip(second["system"]) == strip(first["system"])
    assert strip(second["messages"][0]) == strip(first["messages"][0])


def test_non_streaming_request_carries_the_same_markers(store):
    """The turn loop always streams; a non-streaming call (connection test, other callers) must work too."""
    messages = [{"role": "user", "content": "build a car"},
                {"role": "assistant", "content": None, "tool_calls": [
                    {"id": "toolu_1", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}]},
                {"role": "tool", "tool_call_id": "toolu_1", "name": "read_file", "content": "file text"}]
    messages = system_prompt.with_rolling_breakpoint(messages, MODEL)

    async def call(port):
        return await litellm.acompletion(model=MODEL, api_key="sk-dummy", api_base=f"http://127.0.0.1:{port}",
                                         messages=messages, max_tokens=100)

    with MockAnthropic([reply("ok", usage=dict(input=3, cache_read=7, cache_write=11))]) as server:
        response = asyncio.run(call(server.port))
    body = server.bodies()[0]
    assert "stream" not in body and anthropic_problems(body) == []
    assert anthropic_cache_markers(body) == ["messages[2].content[0].content[0]"]
    assert (response.usage.cache_read_input_tokens, response.usage.cache_creation_input_tokens) == (7, 11)
    assert "cache_control" not in json.dumps(messages[:-1])


def test_marker_on_an_image_block_is_valid(store):
    """A render fed back to the model is a user turn ending in an image block."""
    messages = [{"role": "user", "content": "build"},
                {"role": "user", "content": [{"type": "text", "text": "Renders:"},
                                             {"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBORw0KGgo="}}]}]
    messages = system_prompt.with_rolling_breakpoint(messages, MODEL)

    async def call(port):
        return await litellm.acompletion(model=MODEL, api_key="sk-dummy", api_base=f"http://127.0.0.1:{port}",
                                         messages=messages, max_tokens=100)

    with MockAnthropic([reply("ok")]) as server:
        asyncio.run(call(server.port))
    body = server.bodies()[0]
    assert anthropic_problems(body) == []
    assert body["messages"][-1]["content"][-1]["type"] == "image"
    assert body["messages"][-1]["content"][-1]["cache_control"] == EPHEMERAL


def test_the_mock_enforces_the_documented_schema():
    """The wire assertions above are only worth something if the mock refuses what Anthropic refuses."""
    cc = {"cache_control": EPHEMERAL}
    base = {"model": "m", "max_tokens": 1}
    block = lambda text="x": {"type": "text", "text": text, **cc}  # noqa: E731
    assert anthropic_problems({**base, "messages": [{"role": "user", "content": [block()]}]}) == []
    assert any("Extra inputs" in p for p in anthropic_problems({**base, "messages": [{"role": "user", "content": "x", **cc}]}))
    five = [{"role": "user", "content": [block()]}, {"role": "assistant", "content": [block()]}] * 2 + [{"role": "user", "content": [block()]}]
    assert any("maximum of 4" in p for p in anthropic_problems({**base, "messages": five}))
    assert any("non-empty" in p for p in anthropic_problems({**base, "messages": [{"role": "user", "content": [block(" ")]}]}))
    orphan = {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t", "content": "x"}]}
    assert any("tool_result" in p for p in anthropic_problems({**base, "messages": [{"role": "user", "content": "a"}, orphan]}))
    nested = {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t", "content": [block()]}]}
    assert anthropic_cache_markers({**base, "messages": [nested]}) == ["messages[0].content[0].content[0]"]
