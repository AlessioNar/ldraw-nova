"""OpenRouter, OpenAI chat completions and OpenAI Responses requests, as LiteLLM sends them to a mock server."""
import json
import os

import pytest

import settings
from mock_providers import MockChat, MockResponses, reply, run_chat

pytestmark = pytest.mark.filterwarnings("ignore::UserWarning")      # LiteLLM's pydantic noise on mocked Responses events

PROGRESS = ("call_1", "report_progress", {"summary": "step one"})
EPHEMERAL = {"type": "ephemeral"}


@pytest.fixture(autouse=True)
def writable_data_dir():
    os.chmod(settings.DATA_DIR.parent, 0o755)


def run(store, server, model, script, options=None, followups=()):
    with server(script) as srv:
        chat_id, events = run_chat(store, {"model": model, "api_base": f"http://127.0.0.1:{srv.port}/v1"},
                                   "hi", options, followups)
    assert not [e for e in events if e[0] == "turn_error"], store.messages(chat_id)[-1]
    return chat_id, events, srv


def markers(body):
    """Paths of every cache_control in a chat-completions body."""
    found = []
    for i, message in enumerate(body["messages"]):
        content = message.get("content")
        found += [f"messages[{i}]"] if "cache_control" in message else []
        found += [f"messages[{i}].content[{j}]" for j, b in enumerate(content if isinstance(content, list) else []) if "cache_control" in b]
    return found


STEPS = [reply("Looking.", [PROGRESS], usage=dict(input=100, cache_read=40, cache_write=5000)),
         reply("Done.", usage=dict(input=20, cache_read=5000))]


def test_openrouter_claude_gets_breakpoints_on_system_and_last_message(store):
    _, _, server = run(store, MockChat, "openrouter/anthropic/claude-sonnet-5", STEPS)
    first, second = server.bodies()
    assert first["model"] == "anthropic/claude-sonnet-5"             # LiteLLM strips the gateway prefix
    assert server.requests[0]["headers"]["authorization"] == "Bearer sk-dummy"
    assert markers(first) == ["messages[0].content[0]", "messages[1].content[0]"]
    assert first["messages"][0]["content"][0]["cache_control"] == EPHEMERAL
    assert "cache_control" not in first["messages"][0]["content"][1]
    # unlike the Anthropic route, the tool message keeps its role and gets the block list
    assert [m["role"] for m in second["messages"]] == ["system", "user", "assistant", "tool"]
    assert second["messages"][3] == {"role": "tool", "tool_call_id": "call_1",
                                     "content": [{"type": "text", "text": "step one", "cache_control": EPHEMERAL}]}
    assert markers(second) == ["messages[0].content[0]", "messages[3].content[0]"]
    assert second["messages"][1] == {"role": "user", "content": "hi"}  # earlier history is back to plain strings


def test_openrouter_usage_reports_cache_reads_but_not_cache_writes(store):
    """OpenRouter puts cache writes in prompt_tokens_details.cache_write_tokens."""
    chat_id, events, _ = run(store, MockChat, "openrouter/anthropic/claude-sonnet-5", STEPS)
    assert [d for name, d in events if name == "usage"] == [
        {"prompt": 100, "completion": 5, "cache_read": 40, "cache_write": 5000},
        {"prompt": 20, "completion": 5, "cache_read": 5000, "cache_write": 0}]


def test_openrouter_flag_off_and_other_vendors(store, monkeypatch):
    _, _, server = run(store, MockChat, "openrouter/openai/gpt-6-sol", STEPS)
    assert all("cache_control" not in json.dumps(b) for b in server.bodies())     # not an Anthropic model
    monkeypatch.setattr(settings, "ROLLING_CACHE_BREAKPOINT", False)
    _, _, server = run(store, MockChat, "openrouter/anthropic/claude-sonnet-5", STEPS)
    assert [markers(b) for b in server.bodies()] == [["messages[0].content[0]"]] * 2


@pytest.mark.parametrize("tail", [False, True])
@pytest.mark.parametrize("server, model", [(MockChat, "openai/gpt-4o"), (MockResponses, "openai/gpt-6-luna")])
def test_non_caching_models_never_see_cache_control(store, monkeypatch, server, model, tail):
    monkeypatch.setattr(settings, "TAIL_AFTER_HISTORY", tail)
    chat_id, events, srv = run(store, server, model, STEPS + [reply("Again.")], followups=["more"])
    assert len(srv.requests) == 3
    assert all("cache_control" not in json.dumps(b) for b in srv.bodies())
    if server is MockChat:
        assert isinstance(srv.bodies()[0]["messages"][0]["content"], str)         # plain-string system prompt
    assert [d["cache_read"] for n, d in events if n == "usage"] == [40, 5000, 0]


def test_tail_after_history_on_chat_completions(store, monkeypatch):
    monkeypatch.setattr(settings, "TAIL_AFTER_HISTORY", True)
    _, _, server = run(store, MockChat, "openrouter/anthropic/claude-sonnet-5", STEPS)
    second = server.bodies()[1]
    # no merge here: the tail is its own user message after the tool message, as documented
    assert [m["role"] for m in second["messages"]] == ["system", "user", "assistant", "tool", "user"]
    assert markers(second) == ["messages[0].content[0]", "messages[3].content[0]"]
    assert second["messages"][4]["content"].startswith("[Workspace state for this step, added by the app.")
    assert len(second["messages"][0]["content"]) == 1


# --- OpenRouter reasoning_details ------------------------------------------

def _text(i, piece):
    return {"type": "reasoning.text", "text": piece, "id": "rs_1", "index": 0, "format": "anthropic-claude-v1"}


PIECES = ["Let me ", "think ", "about the ", "wheels."]
DETAILS = [[_text(0, p)] for p in PIECES] + [[{"type": "reasoning.encrypted", "data": "opaque", "id": "rs_1", "index": 1,
                                                "format": "anthropic-claude-v1"}]]
REASONING = [reply("Looking.", [PROGRESS], reasoning_details=DETAILS), reply("Done.")]


def test_reasoning_details_stream_into_storage_and_back_out_unmerged(store, monkeypatch):
    monkeypatch.setattr(settings, "COALESCE_REASONING", False)
    chat_id, _, server = run(store, MockChat, "openrouter/anthropic/claude-sonnet-5", REASONING)
    sent = [d for chunk in DETAILS for d in chunk]
    assert next(m for m in store.messages(chat_id) if m["role"] == "assistant")["_reasoning_details"] == sent
    assistant = next(m for m in server.bodies()[1]["messages"] if m["role"] == "assistant")
    assert assistant["reasoning_details"] == sent
    assert not any(k.startswith("_") for k in assistant)


def test_coalesced_reasoning_details_keep_the_text_and_round_trip(store, monkeypatch):
    monkeypatch.setattr(settings, "COALESCE_REASONING", True)
    chat_id, _, server = run(store, MockChat, "openrouter/anthropic/claude-sonnet-5", REASONING)
    stored = next(m for m in store.messages(chat_id) if m["role"] == "assistant")["_reasoning_details"]
    assert [d["type"] for d in stored] == ["reasoning.text", "reasoning.encrypted"]
    assert stored[0] == _text(0, "".join(PIECES))
    assistant = next(m for m in server.bodies()[1]["messages"] if m["role"] == "assistant")
    assert assistant["reasoning_details"] == stored                    # the next request carries the coalesced blocks
    assert "".join(d.get("text", "") for d in assistant["reasoning_details"]) == "".join(PIECES)
    assert assistant["reasoning_details"][1]["data"] == "opaque"
