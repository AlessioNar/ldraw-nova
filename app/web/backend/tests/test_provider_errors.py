"""Key validation and plain-language provider errors. Every provider call is mocked."""
import asyncio
import json

import httpx
import litellm
import pytest
from fastapi.testclient import TestClient

import agent
import environment_config
import llm_config
import main
import model_discovery
import provider_errors
import run_state

SECRET = "sk-ant-SECRET"


@pytest.fixture(autouse=True)
def clean_registry():
    provider_errors._rejected.clear()
    yield
    provider_errors._rejected.clear()


def status_fetch(code):
    async def fetch(http, url, headers=None):
        if code is None:
            raise httpx.ConnectError("boom " + SECRET)
        if code != 200:
            raise httpx.HTTPStatusError("no " + SECRET, request=httpx.Request("GET", url), response=httpx.Response(code))
        return {"data": []}
    return fetch


def test_provider_test_reports_rejected_unreachable_and_ok(monkeypatch):
    monkeypatch.setattr(environment_config, "snapshot", lambda: {"ANTHROPIC_API_KEY": SECRET})
    client = TestClient(main.app)
    monkeypatch.setattr(model_discovery, "_fetch_json", status_fetch(401))
    r = client.post("/api/llm-providers/anthropic/test").json()
    assert r["status"] == "rejected" and "Key rejected by Anthropic" in r["error"] and SECRET not in json.dumps(r)
    assert client.get("/api/llm-providers/keys").json()["rejected"] == ["anthropic"]
    monkeypatch.setattr(model_discovery, "_fetch_json", status_fetch(None))
    r = client.post("/api/llm-providers/anthropic/test").json()
    assert r["status"] == "unreachable" and "saved but not checked" in r["error"] and SECRET not in json.dumps(r)
    assert client.get("/api/llm-providers/keys").json()["rejected"] == ["anthropic"]   # unreachable does not clear it
    monkeypatch.setattr(model_discovery, "_fetch_json", status_fetch(500))
    assert client.post("/api/llm-providers/anthropic/test").json()["status"] == "error"
    monkeypatch.setattr(model_discovery, "_fetch_json", status_fetch(200))
    assert client.post("/api/llm-providers/anthropic/test").json()["status"] == "ok"
    assert client.get("/api/llm-providers/keys").json()["rejected"] == []


def test_rejected_flag_clears_when_the_key_changes(monkeypatch):
    env = {"OPENAI_API_KEY": "old"}
    monkeypatch.setattr(environment_config, "snapshot", lambda: env)
    provider_errors.record("openai", "old", True)
    assert provider_errors.rejected_providers() == ["openai"]
    env["OPENAI_API_KEY"] = "new"
    assert provider_errors.rejected_providers() == []


def test_agent_own_key_test_checks_the_key_first(monkeypatch):
    entry = llm_config.create({"model_name": "own", "litellm_params": {"model": "anthropic/claude-sonnet-5-5", "api_key": SECRET}})
    monkeypatch.setattr(model_discovery, "_fetch_json", status_fetch(401))

    async def never(**_kw):
        raise AssertionError("no completion when the free check rejects the key")
    monkeypatch.setattr(litellm, "acompletion", never)
    r = TestClient(main.app).post(f"/api/llm-models/{entry['id']}/test").json()
    assert r["ok"] is False and "Key rejected by Anthropic" in r["error"] and SECRET not in json.dumps(r)
    assert provider_errors.rejected_providers() == []   # an agent key is not the provider key


def make_exc(cls, message="x " + SECRET):
    if cls is litellm.APIConnectionError:
        return cls(message=message, llm_provider="anthropic", model="m")
    return cls(message, "anthropic", "m")


@pytest.mark.parametrize("cls,needle", [
    (litellm.AuthenticationError, "rejected the API key"),
    (litellm.RateLimitError, "rate limit"),
    (litellm.NotFoundError, "does not know the model"),
    (litellm.APIConnectionError, "Could not reach"),
])
def test_describe_names_provider_and_model_never_the_key(monkeypatch, cls, needle):
    monkeypatch.setattr(environment_config, "snapshot", lambda: {"ANTHROPIC_API_KEY": SECRET})
    text = provider_errors.describe(make_exc(cls), "anthropic/claude-sonnet-5-5")
    assert needle in text and "Anthropic" in text and "claude-sonnet-5-5" in text and SECRET not in text
    assert provider_errors.describe(RuntimeError("weird"), "anthropic/x") is None
    assert provider_errors.describe(litellm.BadRequestError("Your credit balance is too low", "anthropic", "m"), "anthropic/x")


def test_failed_turn_saves_the_plain_message_and_marks_key(monkeypatch, store):
    monkeypatch.setattr(environment_config, "snapshot", lambda: {"ANTHROPIC_API_KEY": SECRET})
    entry = llm_config.create({"model_name": "s", "litellm_params": {"model": "anthropic/claude-sonnet-5-5", "api_key": "os.environ/ANTHROPIC_API_KEY"},
                               "capabilities": {"tools": True}})

    async def broken(**_kw):
        raise make_exc(litellm.AuthenticationError)
    monkeypatch.setattr(litellm, "acompletion", broken)
    chat = store.create_chat()

    async def run():
        await agent.start_turn(store, chat["id"], "hello", entry["id"])
        await run_state._runs[chat["id"]].task
    asyncio.run(run())
    last = store.messages(chat["id"])[-1]
    assert last["_error"] and "Anthropic rejected the API key for claude-sonnet-5-5. Open Settings" in last["content"]
    assert SECRET not in json.dumps(store.messages(chat["id"]))
    assert provider_errors.rejected_providers() == ["anthropic"]
