"""The provider registry: lookup, gateway resolution, and parity with the
prefix checks it replaced."""
import pytest

import providers


@pytest.mark.parametrize("model, cacheable", [
    ("anthropic/claude-opus-5", True),
    ("openrouter/anthropic/claude-opus-5", True),
    ("bedrock/anthropic.claude-opus-5-v1:0", True),
    ("vertex_ai/claude-opus-5@20260101", True),
    ("openrouter/meta-llama/llama-5", False),
    ("bedrock/meta.llama5", False),
    ("vertex_ai/gemini-3-pro", False),
    ("openai/gpt-6", False),
    ("ollama_chat/qwen3", False),
    ("someprovider/model", False),
])
def test_prompt_caching_matches_the_old_prefix_list(model, cacheable):
    """Every model the removed CACHEABLE_PREFIXES tuple matched still caches."""
    assert providers.supports_prompt_caching(model) is cacheable


def test_longest_prefix_wins():
    assert providers.of("ollama_chat/qwen3").prefix == "ollama_chat/"
    assert providers.of("ollama/qwen3").prefix == "ollama/"


def test_unknown_provider_falls_back():
    assert providers.of("someprovider/model") is providers.UNKNOWN
    assert providers.label("someprovider/model") == "someprovider"


def test_effort_styles():
    assert providers.of("anthropic/x").effort == "anthropic"
    assert providers.of("openrouter/x/y").effort == "openrouter"
    assert providers.of("gemini/x").effort == "reasoning_effort"
    assert providers.of("mistral/x").effort is None


def test_only_openrouter_streams_reasoning_details():
    assert [p.prefix for p in providers.REGISTRY if p.reasoning_details] == ["openrouter/"]


def test_inner_strips_one_gateway():
    assert providers.inner("openrouter/anthropic/claude-opus-5") == "anthropic/claude-opus-5"
    assert providers.inner("anthropic/claude-opus-5") == "anthropic/claude-opus-5"


def test_catalogue_has_one_row_per_provider():
    names = [row["provider"] for row in providers.catalogue()]
    assert len(names) == len(set(names))
    assert {"anthropic", "openrouter", "chatgpt", "vertex_ai"} <= set(names)
