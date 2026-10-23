"""Brief 03: the worker model is chosen per chat or in Settings, with env fallbacks, and refused when unusable."""
import asyncio

import pytest
from fastapi.testclient import TestClient

import agent
import llm_config
import model_catalog
import subagents
import tool_policy
from main import app

PRICES = {"input": 0.1, "output": 0.5, "currency": "USD"}


@pytest.fixture(autouse=True)
def clean_worker_default(monkeypatch):
    monkeypatch.delenv("LDRAW_NOVA_SUBAGENTS", raising=False)
    monkeypatch.delenv("LDRAW_NOVA_WORKER_MODEL_ID", raising=False)
    monkeypatch.delenv("LDRAW_NOVA_WORKER_LLM_ID", raising=False)
    llm_config.set_worker_default(None)
    yield
    llm_config.set_worker_default(None)


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def make(name="w", model="openai/gpt-6-luna", tools=True):
    return llm_config.create({"model_name": name, "litellm_params": {"model": model, "api_key": "sk-x"},
                              "capabilities": {"tools": tools, "vision": False}})


def priced(monkeypatch, pricing=PRICES):
    real = model_catalog.entry_profile
    monkeypatch.setattr(model_catalog, "entry_profile", lambda e: {**real(e), "pricing": pricing})


def names(options):
    return {t["function"]["name"] for t in tool_policy.available_tools({**options, "permissions": "full"})}


def test_no_worker_means_no_delegate_and_default_none():
    assert model_catalog.validate_options(make("planner"), {})["worker_model_id"] is None
    assert "delegate" not in names({})


def test_chat_option_enables_delegate_without_the_env_flag():
    worker = make()
    options = model_catalog.validate_options(make("planner"), {"worker_model_id": worker["id"]})
    assert options["worker_model_id"] == worker["id"] and "delegate" in names(options)
    assert "delegate" not in names({**options, "worker_model_id": None})        # "No workers" overrides any default


def test_settings_default_and_env_fallback(client, monkeypatch):
    worker, planner = make(), make("planner")
    assert client.put("/api/llm-workers/default", json={"id": worker["id"]}).json() == {"worker_id": worker["id"]}
    assert client.get("/api/llm-models").json()["worker_id"] == worker["id"]
    assert model_catalog.validate_options(planner, {})["worker_model_id"] == worker["id"]
    assert model_catalog.validate_options(planner, {"worker_model_id": None})["worker_model_id"] is None
    assert client.put("/api/llm-workers/default", json={"id": "nope"}).status_code == 400
    client.put("/api/llm-workers/default", json={"id": None})
    monkeypatch.setenv("LDRAW_NOVA_WORKER_MODEL_ID", worker["id"])
    assert model_catalog.validate_options(planner, {})["worker_model_id"] is None      # env alone needs the flag
    monkeypatch.setenv("LDRAW_NOVA_SUBAGENTS", "1")
    assert model_catalog.validate_options(planner, {})["worker_model_id"] == worker["id"]


def test_deleting_the_default_worker_clears_it():
    worker = make()
    llm_config.set_worker_default(worker["id"])
    llm_config.delete(worker["id"])
    assert llm_config.worker_default() is None


def test_worker_checks(monkeypatch):
    priced(monkeypatch)
    subagents.check_worker({"worker_model_id": None, "budget_eur": 1})
    subagents.check_worker({"worker_model_id": make()["id"], "budget_eur": 1})
    with pytest.raises(ValueError, match="no longer exists"):
        subagents.check_worker({"worker_model_id": "gone"})
    monkeypatch.setattr(llm_config, "capabilities", lambda e: {"tools": False, "vision": False})
    with pytest.raises(ValueError, match="tool calling"):
        subagents.check_worker({"worker_model_id": make("nt")["id"]})


def test_unpriced_worker_refused_only_with_a_budget(monkeypatch):
    priced(monkeypatch, {"input": None, "output": None, "currency": "USD"})
    worker = make()["id"]
    subagents.check_worker({"worker_model_id": worker})
    subagents.check_worker({"worker_model_id": worker, "budget_eur": None})
    with pytest.raises(ValueError, match="no known prices"):
        subagents.check_worker({"worker_model_id": worker, "budget_eur": 2})


def test_worker_effort_is_the_lowest_offered(monkeypatch):
    real = model_catalog.entry_profile
    monkeypatch.setattr(model_catalog, "entry_profile", lambda e: {**real(e), "efforts": ["high", "low", "medium"]})
    assert subagents.worker_effort(make()) == "low"
    monkeypatch.setattr(model_catalog, "entry_profile", lambda e: {**real(e), "efforts": []})
    assert subagents.worker_effort(make()) is None


def test_start_turn_refuses_a_missing_worker_and_keeps_the_chat_clean(store):
    planner = make("planner", "anthropic/claude-sonnet-5-5")
    chat = store.create_chat()
    with pytest.raises(ValueError, match="no longer exists"):
        asyncio.run(agent.start_turn(store, chat["id"], "hi", planner["id"], {"worker_model_id": "gone"}))
    assert not store.messages(chat["id"])
