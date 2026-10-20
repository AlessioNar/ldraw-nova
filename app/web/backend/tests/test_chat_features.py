import asyncio
import base64
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import agent
from history import llm_history
import run_state
import browser_auth
import inference
import llm_config
import model_catalog
from attachments import validate_images
from main import app
from tools import ToolContext, ToolResult


def test_catalog_and_model_specific_validation():
    entry = {"litellm_params": {"model": "openai/gpt-6-astra"}}
    assert "none" not in model_catalog.profile(entry["litellm_params"]["model"])["efforts"]
    with pytest.raises(ValueError):
        model_catalog.validate_options(entry, {"effort": "none"})
    with pytest.raises(ValueError):
        model_catalog.validate_options(entry, {"context_tokens": 2_000_000})
    assert model_catalog.validate_options(entry, {"effort": "max"})["permissions"] == "ask"
    assert model_catalog.profile("anthropic/claude-haiku-4-5-20251001")["efforts"] == []


def test_nested_keys_mask_and_roundtrip():
    entry = llm_config.create({"litellm_params": {"model": "openai/gpt-6-luna", "extra_headers": {"Authorization": "Bearer secret-value-1234"}}})
    public = llm_config.public(entry)
    assert "secret-value" not in json.dumps(public)
    updated = llm_config.update(entry["id"], public)
    assert updated["litellm_params"]["extra_headers"]["Authorization"] == "Bearer secret-value-1234"
    with pytest.raises(ValueError):
        llm_config.create(public)


def test_browser_entries_never_store_api_overrides():
    entry = llm_config.create({"auth_mode": "browser", "litellm_params": {
        "model": "openai/gpt-6-astra", "api_key": "secret", "api_base": "https://invalid.example"}})
    assert entry["litellm_params"] == {"model": "openai/gpt-6-astra"}
    with pytest.raises(ValueError):
        llm_config.create({"auth_mode": "browser", "litellm_params": {"model": "ollama/fake"}})


def test_request_uses_responses_and_provider_effort(monkeypatch):
    async def ready():
        pass
    monkeypatch.setattr(browser_auth, "openai_ready", ready)
    async def run():
        api = {"litellm_params": {"model": "openai/gpt-6-astra", "api_key": "test"}}
        params = await inference.params_for(api, {"effort": "max"})
        assert params["model"] == "openai/responses/gpt-6-astra"
        assert params["reasoning_effort"] == "max"
        api["auth_mode"] = "browser"
        params = await inference.params_for(api, {})
        assert params == {"model": "chatgpt/responses/gpt-6-astra"}
        claude = {"litellm_params": {"model": "anthropic/claude-opus-5-5"}}
        params = await inference.params_for(claude, {"effort": "xhigh"})
        assert params["output_config"] == {"effort": "xhigh"}
        assert params["thinking"] == {"type": "adaptive"}
    asyncio.run(run())


@pytest.mark.parametrize("approved", [True, False])
def test_approval_pauses_and_decision_is_single_use(monkeypatch, store, approved):
    called = []
    async def dispatch(*args):
        called.append(args)
        return ToolResult("executed")
    monkeypatch.setattr(agent, "dispatch", dispatch)
    chat = store.create_chat()
    run = run_state.Run(chat["id"], options={"mode": "agent", "permissions": "ask"})
    run_state._runs[chat["id"]] = run
    ctx = ToolContext(chat["id"], store, run.emit)
    async def check():
        task = asyncio.create_task(agent.execute_tool(run, ctx, "call", "run_shell", '{"command":"true"}'))
        await asyncio.sleep(0)
        assert not task.done() and not called
        [approval_id] = run.approvals
        assert not run_state.decide("unrelated-chat", approval_id, True)
        assert run_state.decide(chat["id"], approval_id, approved)
        assert not run_state.decide(chat["id"], approval_id, True)
        result = await task
        assert bool(called) is approved
        assert result.content == "executed" if approved else "denied" in result.content
        assert not run.approvals
    asyncio.run(check())


@pytest.mark.parametrize("options", [{"mode": "plan", "permissions": "full"}, {"mode": "chat"}, {"mode": "agent", "permissions": "read_only"}])
def test_modes_enforced_even_if_model_calls_forbidden_tool(monkeypatch, options):
    async def forbidden(*args):
        raise AssertionError("must never dispatch")
    monkeypatch.setattr(agent, "dispatch", forbidden)
    result = asyncio.run(agent.execute_tool(run_state.Run("chat", options=options), None, "call", "run_python", "{}"))
    assert "denied" in result.content


def test_cancel_pending_approval_cleans_up():
    run = run_state.Run("chat", options={"mode": "agent", "permissions": "ask"})
    async def check():
        task = asyncio.create_task(agent.execute_tool(run, None, "call", "run_shell", "{}"))
        await asyncio.sleep(0)
        assert run.approvals
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not run.approvals
    asyncio.run(check())


def test_context_retains_current_tool_exchange_and_drops_whole_old_turn(monkeypatch):
    monkeypatch.setattr(inference.litellm, "token_counter", lambda **kw: len(json.dumps(kw["messages"])))
    messages = [{"role": "system", "content": "system"},
                {"role": "user", "content": "old" * 5000, "_turn_start": True},
                {"role": "assistant", "content": "old answer"},
                {"role": "user", "content": "latest", "_turn_start": True},
                {"role": "assistant", "tool_calls": [{"id": "c"}]},
                {"role": "tool", "tool_call_id": "c", "content": "result"},
                {"role": "user", "content": "render", "_turn_start": False}]
    result, count = inference.bounded_history(messages, "fake", 6000)
    assert count == 2 and result[1]["content"] == "latest"
    assert result[-2]["tool_call_id"] == "c"
    assert all("_turn_start" not in m for m in result)
    assert messages[1]["_turn_start"]  # doesn't mutate the stored history
    messages[3]["content"] = "current" * 5000
    with pytest.raises(ValueError, match="latest turn"):
        inference.bounded_history(messages, "fake", 6000)


def test_images_reject_remote_urls_mime_spoof_and_bad_encoding():
    png = "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\nimage").decode()
    assert validate_images([png]) == [png]
    for bad in ["https://example.com/private", "data:image/svg+xml;base64,AAAA", png.replace("png", "jpeg"), "data:image/png;base64,abc"]:
        with pytest.raises(ValueError):
            validate_images([bad])
    with pytest.raises(ValueError):
        validate_images([png] * 5)


def test_model_switch_strips_signed_thinking_and_handles_images():
    messages = [{"role": "assistant", "content": "previous", "thinking_blocks": [{"signature": "signed"}], "_llm_model": "anthropic/old"},
                {"role": "user", "content": [{"type": "text", "text": "inspect"}, {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}]}]
    history = llm_history(messages, False, Path, "openai/new")
    assert "thinking_blocks" not in history[0]
    assert history[1]["content"] == "inspect"
    assert "thinking_blocks" in llm_history(messages, True, Path, "anthropic/old")[0]


def test_login_status_excludes_secrets_and_blocks_cross_origin(monkeypatch):
    monkeypatch.setitem(browser_auth._sessions, "openai", {"status": "pending", "url": "https://auth.openai.com/codex/device",
        "code": "ABCD", "access_token": "must-not-leak", "process": object(), "device_auth_id": "private"})
    client = TestClient(app)
    response = client.get("/api/auth/openai")
    assert response.json() == {"status": "pending", "url": "https://auth.openai.com/codex/device", "code": "ABCD"}
    assert client.post("/api/auth/openai/login", headers={"origin": "https://evil.example"}).status_code == 403
    assert client.get("/api/auth/unknown").status_code == 400


def test_expired_chatgpt_never_starts_interactive_login(monkeypatch):
    class Auth:
        def _read_auth_file(self): return {}
        def get_access_token(self): raise AssertionError("must not trigger implicit login")
    monkeypatch.setattr(browser_auth, "openai_authenticator", Auth)
    with pytest.raises(ValueError, match="Sign in again"):
        asyncio.run(browser_auth.openai_ready())
