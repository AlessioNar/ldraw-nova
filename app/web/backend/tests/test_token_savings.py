"""Token-saving defaults: tail after the history, masking on, lean prompt for models that cannot cache, 1 h cache."""
import pytest

import settings
import system_prompt
import toolkit
import turn

CLAUDE = "anthropic/claude-opus-5-5"
PLAIN = "openai/gpt-5"


@pytest.fixture
def defaults(monkeypatch):
    """Undo conftest's legacy layout: the module-level defaults the app ships with."""
    monkeypatch.delenv("LDRAW_NOVA_MASK_OLD_TOOL_OUTPUT")
    for name in ("LDRAW_NOVA_TAIL_AFTER_HISTORY", "LDRAW_NOVA_CACHE_TTL", "LDRAW_NOVA_LEAN_PROMPT", "LDRAW_NOVA_MAX_STEPS"):
        monkeypatch.delenv(name, raising=False)


def reload_settings(monkeypatch):
    import importlib
    importlib.reload(settings)
    return settings


def test_shipped_defaults(defaults, monkeypatch):
    fresh = reload_settings(monkeypatch)
    try:
        assert fresh.TAIL_AFTER_HISTORY is True and fresh.mask_old_tool_output() is True
        assert fresh.CACHE_TTL == "1h" and fresh.LEAN_PROMPT == "auto" and fresh.max_steps() == 80
        monkeypatch.setenv("LDRAW_NOVA_TAIL_AFTER_HISTORY", "0")
        monkeypatch.setenv("LDRAW_NOVA_MAX_STEPS", "5")
        again = reload_settings(monkeypatch)
        assert again.TAIL_AFTER_HISTORY is False and again.max_steps() == 5
    finally:
        monkeypatch.undo()
        reload_settings(monkeypatch)


def test_one_hour_marker_only_for_anthropic(monkeypatch):
    monkeypatch.setattr(settings, "CACHE_TTL", "1h")
    assert system_prompt.cache_control(CLAUDE) == {"type": "ephemeral", "ttl": "1h"}
    assert system_prompt.cache_control("openrouter/anthropic/claude-sonnet-5") == {"type": "ephemeral"}
    monkeypatch.setattr(settings, "CACHE_TTL", "5m")
    assert system_prompt.cache_control(CLAUDE) == {"type": "ephemeral"}


def test_the_ttl_reaches_every_breakpoint(ctx, monkeypatch):
    monkeypatch.setattr(settings, "CACHE_TTL", "1h")
    system = system_prompt.system_message(ctx.store, ctx.chat_id, CLAUDE, {})
    sent = system_prompt.with_rolling_breakpoint([system, {"role": "user", "content": "hi"}], CLAUDE)
    assert system["content"][0]["cache_control"]["ttl"] == "1h"
    assert sent[-1]["content"][-1]["cache_control"]["ttl"] == "1h"


def test_lean_prompt_selection(monkeypatch):
    monkeypatch.setattr(settings, "LEAN_PROMPT", "auto")
    assert system_prompt.use_lean_prompt(PLAIN, {}) is True                                  # cannot cache: pays per step
    assert system_prompt.use_lean_prompt(CLAUDE, {"context_tokens": 1_000_000}) is False
    assert system_prompt.use_lean_prompt(CLAUDE, {"context_tokens": 100_000}) is True        # small window
    monkeypatch.setattr(settings, "LEAN_PROMPT", "off")
    assert system_prompt.use_lean_prompt(PLAIN, {}) is False
    monkeypatch.setattr(settings, "LEAN_PROMPT", "on")
    assert system_prompt.use_lean_prompt(CLAUDE, {"context_tokens": 1_000_000}) is True


def test_lean_prompt_lists_the_guides_instead_of_inlining_them(ctx):
    full, _ = system_prompt.system_prompt_parts(ctx.store, ctx.chat_id)
    lean, _ = system_prompt.system_prompt_parts(ctx.store, ctx.chat_id, True)
    assert toolkit.instructions() in lean and len(lean) < len(full) - 20_000
    for path in toolkit.guide_paths():
        assert f"- {path}:" in lean
        assert (settings.TOOLKIT_DIR / path).read_text() not in lean and (settings.TOOLKIT_DIR / path).read_text() in full
    assert "NOT preloaded" in lean and "already loaded, no tool reread needed" in full
    assert "Working efficiently" in lean and "Working efficiently" not in full


def test_the_default_layout_keeps_the_prefix_cacheable(defaults, ctx, monkeypatch):
    reload_settings(monkeypatch)
    try:
        before = system_prompt.system_message(ctx.store, ctx.chat_id, CLAUDE, {"context_tokens": 1_000_000})
        (ctx.work_dir / "zz-listing-marker.json").write_text("{}")
        after = system_prompt.system_message(ctx.store, ctx.chat_id, CLAUDE, {"context_tokens": 1_000_000})
        assert before == after and "zz-listing-marker" not in str(after)          # the listing moved behind the history
    finally:
        monkeypatch.undo()
        reload_settings(monkeypatch)


def test_turn_has_no_hard_coded_step_limit():
    assert not hasattr(turn, "MAX_STEPS")
