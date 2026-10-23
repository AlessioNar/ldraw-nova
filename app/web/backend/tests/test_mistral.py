import asyncio
import json

import httpx
from fastapi.testclient import TestClient

import environment_config
import inference
import main
import model_catalog
import model_discovery
import providers

IDS = ["mistral/mistral-large-4-0", "mistral/mistral-medium-3-5", "mistral/mistral-small-2603", "mistral/mistral-large-2512",
       "mistral/ministral-14b-2512", "mistral/ministral-8b-2512", "mistral/ministral-3b-2512"]


def test_provider_record():
    record = providers.of("mistral/mistral-large-4-0")
    assert record.label == "Mistral" and record.discovery == "mistral"
    assert "https://api.mistral.ai/v1" in record.api_bases
    # Mistral caches by prompt_cache_key; it takes no Anthropic-style cache_control block.
    assert not providers.supports_prompt_caching("mistral/mistral-large-4-0")
    assert next(p for p in providers.catalogue() if p["provider"] == "mistral")["discovery"] is True


def test_presets_are_builder_supported_and_sourced():
    presets = [m for m in model_catalog.CATALOG if m["model"].startswith("mistral/")]
    assert [m["model"] for m in presets] == IDS
    for preset in presets:
        assert model_catalog.builder_supported(preset["model"])
        assert preset["source_url"].startswith("https://docs.mistral.ai/") and preset["verified_at"] == "2026-10-23"
        assert preset["pricing"]["currency"] == "USD" and preset["pricing"]["input"] > 0
        assert preset["max_output_tokens"] is None  # not published by Mistral


def test_other_providers_unchanged():
    assert providers.of("openai/gpt-6-sol").effort == "reasoning_effort"
    assert providers.of("openrouter/x/y").effort == "openrouter"
    assert providers.of("anthropic/x").effort == "anthropic"
    assert providers.supports_prompt_caching("anthropic/x")
    assert providers.of("mistral/x").prefix == "mistral/" and providers.of("ollama/x").effort is None


def test_effort_goes_in_extra_body():
    entry = {"model_name": "m", "litellm_params": {"model": "mistral/mistral-medium-3-5", "api_key": "k"}}
    params = asyncio.run(inference.params_for(entry, {"effort": "high"}))
    assert params["extra_body"] == {"reasoning_effort": "high"} and "reasoning_effort" not in params
    assert "extra_body" not in asyncio.run(inference.params_for(entry, {}))
    assert model_catalog.profile("mistral/mistral-medium-3-5")["efforts"] == ["none", "high"]
    assert model_catalog.profile("mistral/ministral-8b-2512")["efforts"] == []


def test_discovery_parses_mistral_model_card(monkeypatch):
    seen = {}
    async def fetch(client, url, headers=None):
        seen.update(url=url, headers=headers)
        return {"id": "mistral-large-4-0", "max_context_length": 1_048_576,
                "capabilities": {"function_calling": True, "vision": False}}
    monkeypatch.setattr(model_discovery, "_fetch_json", fetch)
    entry = {"litellm_params": {"model": "mistral/mistral-large-4-0", "api_key": "sk-test"}}
    result = asyncio.run(model_discovery.discover(entry))
    assert seen["url"] == "https://api.mistral.ai/v1/models/mistral-large-4-0" and seen["headers"] == {"Authorization": "Bearer sk-test"}
    assert result["lookup_status"] == "live" and result["context_window"] == 1_048_576
    assert result["tools"] is True and result["vision"] is False
    assert result["pricing"]["input"] == 1.36  # the API has no prices: the published ones stay


def test_discovery_falls_back_when_lookup_fails(monkeypatch):
    async def boom(client, url, headers=None):
        raise httpx.ConnectError("down")
    monkeypatch.setattr(model_discovery, "_fetch_json", boom)
    entry = {"litellm_params": {"model": "mistral/mistral-large-4-0", "api_key": "sk-test"}}
    result = asyncio.run(model_discovery.discover(entry))
    assert result["lookup_status"] == "unavailable" and result["context_window"] == 1_000_000


def test_key_route_uses_free_models_list(monkeypatch):
    monkeypatch.setattr(environment_config, "snapshot", lambda: {"MISTRAL_API_KEY": "sk-mistral"})
    client = TestClient(main.app)
    assert client.get("/api/llm-providers/keys").json()["keys"]["mistral"] is True
    seen = {}
    async def fetch(http, url, headers=None):
        seen.update(url=url, headers=headers)
        return {"data": []}
    monkeypatch.setattr(model_discovery, "_fetch_json", fetch)
    result = client.post("/api/llm-providers/mistral/test").json()
    assert result["ok"] and "sk-mistral" not in json.dumps(result)
    assert seen["url"] == "https://api.mistral.ai/v1/models" and seen["headers"] == {"Authorization": "Bearer sk-mistral"}
    async def rejected(http, url, headers=None):
        raise httpx.HTTPStatusError("no", request=httpx.Request("GET", url), response=httpx.Response(401))
    monkeypatch.setattr(model_discovery, "_fetch_json", rejected)
    assert "rejected" in client.post("/api/llm-providers/mistral/test").json()["error"]
    monkeypatch.setattr(environment_config, "snapshot", lambda: {})
    assert client.post("/api/llm-providers/mistral/test").json()["ok"] is False
