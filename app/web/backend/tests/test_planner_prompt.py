"""The planner prompt: delegation paragraph only when `delegate` is offered, constant across steps."""
import pytest

import llm_config
import system_prompt

CACHING = "anthropic/claude-opus-5"
FULL = {"permissions": "full"}


@pytest.fixture
def worker(monkeypatch):
    entry = llm_config.create({"model_name": "worker", "litellm_params": {"model": "openai/gpt-6-luna", "api_key": "sk-x"},
                               "capabilities": {"tools": True, "vision": False}})
    monkeypatch.setenv("LDRAW_NOVA_SUBAGENTS", "1")
    monkeypatch.setenv("LDRAW_NOVA_WORKER_LLM_ID", entry["id"])


def stable_text(ctx, options):
    return system_prompt.system_message(ctx.store, ctx.chat_id, CACHING, options)["content"][0]["text"]


def test_delegation_paragraph_only_when_delegate_is_offered(ctx, worker, monkeypatch):
    on = stable_text(ctx, FULL)
    assert system_prompt.DELEGATION_NOTE in on
    for options in ({"permissions": "ask"}, {"permissions": "read_only"}, {**FULL, "mode": "plan"}, {**FULL, "mode": "chat"}):
        assert system_prompt.DELEGATION_NOTE not in stable_text(ctx, options)
    monkeypatch.delenv("LDRAW_NOVA_SUBAGENTS")
    off = stable_text(ctx, FULL)
    assert system_prompt.DELEGATION_NOTE not in off
    assert on == off + system_prompt.DELEGATION_NOTE       # nothing else differs


def test_delegation_note_is_short():
    assert len(system_prompt.DELEGATION_NOTE) / 4 < 400


def test_stable_prompt_is_byte_identical_across_steps(ctx, worker):
    first = stable_text(ctx, FULL)
    (ctx.work_dir / "gen.py").write_text("print(1)")
    (ctx.work_dir / "NOTES.md").write_text("step 2 notes")
    (ctx.work_dir / "workers").mkdir()
    (ctx.work_dir / "workers" / "w1.ldr").write_text("0 x")
    assert stable_text(ctx, FULL) == first


def test_output_economy_rules_are_in_the_stable_prompt(ctx):
    text = stable_text(ctx, {})
    assert "at most two render-review-fix rounds" in text and "ldraw-agent part A B C" in text
