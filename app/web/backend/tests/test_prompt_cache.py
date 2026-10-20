"""The system prompt's cache breakpoint, and the pieces that must tolerate it."""
import os

import pytest

import system_prompt
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
    stable, volatile = system_prompt.system_prompt_parts(ctx.store, ctx.chat_id)
    # The expensive, re-sent-every-step content is what sits behind the breakpoint.
    assert toolkit.instructions() in stable
    for path in toolkit.BUILDER_GUIDES:
        assert (settings.TOOLKIT_DIR / path).read_text() in stable
    # ... and the parts that change as the agent works are not.
    assert "Current output files:" in volatile
    assert "Current output files:" not in stable
    assert system_prompt.system_prompt(ctx.store, ctx.chat_id) == stable + volatile


def test_stable_part_survives_the_agent_writing_files(ctx):
    before, _ = system_prompt.system_prompt_parts(ctx.store, ctx.chat_id)
    (ctx.work_dir / "plan.json").write_text("{}")
    (ctx.work_dir / "NOTES.md").write_text("progress so far")
    after, volatile = system_prompt.system_prompt_parts(ctx.store, ctx.chat_id)
    # Byte-identical, or every step re-bills the whole prefix.
    assert before == after
    assert "plan.json" in volatile and "progress so far" in volatile


def test_breakpoint_only_for_providers_that_accept_it(ctx):
    cached = system_prompt.system_message(ctx.store, ctx.chat_id, "anthropic/claude-opus-5", {})
    assert [b.get("cache_control") for b in cached["content"]] == [{"type": "ephemeral"}, None]

    plain = system_prompt.system_message(ctx.store, ctx.chat_id, "openai/gpt-5", {})
    assert isinstance(plain["content"], str)
    assert "cache_control" not in plain["content"]
    # Same prompt text either way.
    assert "".join(b["text"] for b in cached["content"]) == plain["content"]


def test_mode_prompt_stays_outside_the_cached_prefix(ctx):
    agent_mode = system_prompt.system_message(ctx.store, ctx.chat_id, "anthropic/claude-opus-5", {"mode": "agent"})
    plan_mode = system_prompt.system_message(ctx.store, ctx.chat_id, "anthropic/claude-opus-5", {"mode": "plan"})
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
