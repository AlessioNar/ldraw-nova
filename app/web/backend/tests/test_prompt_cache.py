"""The system prompt's cache breakpoint, and the pieces that must tolerate it."""
import os

import pytest

import agent
import inference
import settings
import toolkit
import tools
from store import get_store


@pytest.fixture
def ctx():
    os.chmod(settings.DATA_DIR.parent, 0o755)
    store = get_store()
    chat = store.create_chat()
    return tools.ToolContext(chat["id"], store, lambda *_: None)


def test_stable_part_carries_the_guides_and_no_volatile_state(ctx):
    stable, volatile = agent.system_prompt_parts(ctx.store, ctx.chat_id)
    # The expensive, re-sent-every-step content is what sits behind the breakpoint.
    assert toolkit.instructions() in stable
    for path in toolkit.BUILDER_GUIDES:
        assert (settings.TOOLKIT_DIR / path).read_text() in stable
    # ... and the parts that change as the agent works are not.
    assert "Current output files:" in volatile
    assert "Current output files:" not in stable
    assert agent.system_prompt(ctx.store, ctx.chat_id) == stable + volatile


def test_stable_part_survives_the_agent_writing_files(ctx):
    before, _ = agent.system_prompt_parts(ctx.store, ctx.chat_id)
    (ctx.work_dir / "plan.json").write_text("{}")
    (ctx.work_dir / "NOTES.md").write_text("progress so far")
    after, volatile = agent.system_prompt_parts(ctx.store, ctx.chat_id)
    # Byte-identical, or every step re-bills the whole prefix.
    assert before == after
    assert "plan.json" in volatile and "progress so far" in volatile


def test_breakpoint_only_for_providers_that_accept_it(ctx):
    cached = agent.system_message(ctx.store, ctx.chat_id, "anthropic/claude-opus-5", {})
    assert [b.get("cache_control") for b in cached["content"]] == [{"type": "ephemeral"}, None]

    plain = agent.system_message(ctx.store, ctx.chat_id, "openai/gpt-5", {})
    assert isinstance(plain["content"], str)
    assert "cache_control" not in plain["content"]
    # Same prompt text either way.
    assert "".join(b["text"] for b in cached["content"]) == plain["content"]


def test_mode_prompt_stays_outside_the_cached_prefix(ctx):
    agent_mode = agent.system_message(ctx.store, ctx.chat_id, "anthropic/claude-opus-5", {"mode": "agent"})
    plan_mode = agent.system_message(ctx.store, ctx.chat_id, "anthropic/claude-opus-5", {"mode": "plan"})
    assert agent_mode["content"][0]["text"] == plan_mode["content"][0]["text"]
    assert "PLAN MODE" in plan_mode["content"][1]["text"]


@pytest.mark.parametrize("content, expected", [
    ("plain prompt", "plain prompt NOTE"),
    ([{"type": "text", "text": "stable", "cache_control": {"type": "ephemeral"}},
      {"type": "text", "text": "tail"}], "tail NOTE"),
])
def test_trim_note_appends_after_the_breakpoint(content, expected):
    """`list += str` would silently splice characters into the block list."""
    message = {"role": "system", "content": content}
    inference._append_system(message, " NOTE")
    assert inference._system_tail(message) == expected
    if isinstance(content, list):
        assert len(message["content"]) == 2
        assert message["content"][0]["text"] == "stable"   # cached prefix untouched


# --- rolling breakpoint on the history, and the optional trailing context ---

import json

import system_prompt

CACHING, PLAIN = "anthropic/claude-opus-5", "openai/gpt-5"
HISTORY = [
    {"role": "user", "content": "build a car"},
    {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "type": "function",
                                                            "function": {"name": "read_file", "arguments": "{}"}}]},
    {"role": "tool", "tool_call_id": "c1", "name": "read_file", "content": "file text"},
]


def _request(ctx, model, options=None, history=HISTORY):
    from inference import bounded_history
    messages = [agent.system_message(ctx.store, ctx.chat_id, model, options or {}), *history]
    messages, _ = bounded_history(messages, model, None)
    return system_prompt.outbound_messages(ctx.store, ctx.chat_id, messages, model, options or {})


def _marks(messages):
    return [m for m in messages
            if "cache_control" in m or any("cache_control" in b for b in (m["content"] if isinstance(m.get("content"), list) else []))]


def test_last_message_gets_a_breakpoint_for_caching_models(ctx):
    sent = _request(ctx, CACHING)
    assert sent[-1]["content"] == [{"type": "text", "text": "file text", "cache_control": {"type": "ephemeral"}}]
    assert sent[-1]["role"] == "tool" and sent[-1]["tool_call_id"] == "c1"
    assert len(_marks(sent)) == 2                      # system prefix + rolling
    assert len(_marks(sent)) <= system_prompt.MAX_CACHE_BREAKPOINTS


def test_no_breakpoint_for_other_models_or_when_disabled(ctx, monkeypatch):
    assert "cache_control" not in json.dumps(_request(ctx, PLAIN))
    monkeypatch.setattr(settings, "ROLLING_CACHE_BREAKPOINT", False)
    assert len(_marks(_request(ctx, CACHING))) == 1    # only the system prefix


def test_breakpoint_is_never_stored_or_shared_with_the_history(ctx):
    history = json.loads(json.dumps(HISTORY))
    _request(ctx, CACHING, history=history)
    assert history == HISTORY                          # input untouched: no leak into the saved chat
    assert "cache_control" not in json.dumps(HISTORY)


@pytest.mark.parametrize("last, expect", [
    ({"role": "user", "content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]}, "b"),
    ({"role": "assistant", "content": None, "tool_calls": [{"id": "x"}]}, None),
])
def test_breakpoint_on_block_lists_and_contentless_messages(last, expect):
    sent = system_prompt.with_rolling_breakpoint([{"role": "system", "content": "s"}, last], CACHING)
    if expect:
        assert sent[-1]["content"][0].get("cache_control") is None and sent[-1]["content"][-1]["cache_control"]
    else:
        assert sent[-1]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in json.dumps(last)


def test_breakpoint_respects_the_provider_limit():
    marked = [{"role": "user", "content": [{"type": "text", "text": str(i), "cache_control": {"type": "ephemeral"}}]}
              for i in range(4)]
    assert system_prompt.with_rolling_breakpoint(marked, CACHING) is marked


def test_stable_prefix_bytes_do_not_change_between_steps(ctx):
    first = _request(ctx, CACHING)
    (ctx.work_dir / "plan.json").write_text("{}")
    (ctx.work_dir / "NOTES.md").write_text("notes")
    second = _request(ctx, CACHING, history=[*HISTORY, {"role": "assistant", "content": "ok"}])
    assert json.dumps(first[0]["content"][0]) == json.dumps(second[0]["content"][0])
    # the old last message loses its mark and is otherwise the same bytes: only the tail is new
    assert first[1:-1] == second[1:len(first) - 1]
    assert first[-1]["content"][0]["text"] == second[len(first) - 1]["content"]


def test_tail_stays_in_system_by_default(ctx):
    sent = _request(ctx, CACHING, {"mode": "plan"})
    assert "Current output files:" in sent[0]["content"][1]["text"]
    assert sent[-1]["role"] == "tool"


@pytest.mark.parametrize("model", [CACHING, PLAIN])
def test_tail_after_history_shape(ctx, monkeypatch, model):
    monkeypatch.setattr(settings, "TAIL_AFTER_HISTORY", True)
    (ctx.work_dir / "NOTES.md").write_text("handover")
    sent = _request(ctx, model, {"mode": "plan"})
    stable, _ = agent.system_prompt_parts(ctx.store, ctx.chat_id)
    system = sent[0]
    assert system["role"] == "system" and all(m["role"] != "system" for m in sent[1:])
    text = system["content"] if isinstance(system["content"], str) else "".join(b["text"] for b in system["content"])
    assert text == stable                               # nothing volatile left in front of the history
    assert [m["role"] for m in sent[1:-1]] == ["user", "assistant", "tool"]
    trailer = sent[-1]
    assert trailer["role"] == "user" and isinstance(trailer["content"], str)
    assert "Current output files:" in trailer["content"] and "handover" in trailer["content"]
    assert "PLAN MODE" in trailer["content"]
    if model == CACHING:
        assert system["content"][0]["cache_control"]
        assert sent[-2]["content"][-1]["cache_control"]  # rolling mark sits before the trailer
        assert "cache_control" not in json.dumps(trailer)
        assert len(_marks(sent)) == 2
    else:
        assert "cache_control" not in json.dumps(sent)


def test_tail_after_history_keeps_the_trim_note_out_of_the_cached_prefix(ctx, monkeypatch):
    monkeypatch.setattr(settings, "TAIL_AFTER_HISTORY", True)
    messages = [agent.system_message(ctx.store, ctx.chat_id, CACHING, {}), *HISTORY]
    inference._append_system(messages[0], " TRIMNOTE")
    sent = system_prompt.outbound_messages(ctx.store, ctx.chat_id, messages, CACHING, {})
    assert "TRIMNOTE" not in json.dumps(sent[0]) and "TRIMNOTE" in sent[-1]["content"]
