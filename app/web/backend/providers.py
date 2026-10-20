"""One record per LLM provider, so provider behaviour is declared in a table
instead of sniffed with `model.startswith(...)` in every module that cares.

Before this existed the same knowledge was spread across inference.params_for
(which effort field to send), agent (prompt caching, OpenRouter reasoning
chunks), model_discovery (which live lookup to run) and the frontend's label
map. Adding a provider meant finding all of them.

A model string is `<provider>/<model>`, and a gateway nests the real provider:
`openrouter/anthropic/claude-opus-5`. Capabilities that belong to the model
rather than the transport (prompt caching) therefore resolve through the
gateway to the inner provider.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Provider:
    prefix: str                       # litellm model prefix, including the slash
    label: str                        # what the UI calls it
    effort: str | None = None         # "reasoning_effort" | "openrouter" | "anthropic" | None
    prompt_caching: bool = False      # accepts cache_control on a content block
    reasoning_details: bool = False   # streams OpenRouter-style reasoning_details blocks
    discovery: str | None = None      # live capability lookup: "openrouter" | "anthropic" | None
    gateway: bool = False             # serves other providers' models under its own prefix
    api_bases: frozenset[str] = field(default_factory=frozenset)


# `effort=None` means the provider rejects or ignores an effort field; sending
# `reasoning_effort` to one of those is how Mistral used to 400.
REGISTRY: tuple[Provider, ...] = (
    Provider(prefix="anthropic/", label="Claude", effort="anthropic", prompt_caching=True,
             discovery="anthropic",
             api_bases=frozenset({"https://api.anthropic.com", "https://api.anthropic.com/v1"})),
    Provider(prefix="openrouter/", label="OpenRouter", effort="openrouter", reasoning_details=True,
             discovery="openrouter", gateway=True,
             api_bases=frozenset({"https://openrouter.ai/api/v1"})),
    Provider(prefix="openai/", label="OpenAI", effort="reasoning_effort",
             api_bases=frozenset({"https://api.openai.com/v1"})),
    Provider(prefix="chatgpt/", label="ChatGPT", effort="reasoning_effort"),
    Provider(prefix="azure/", label="Azure OpenAI", effort="reasoning_effort"),
    Provider(prefix="gemini/", label="Gemini", effort="reasoning_effort"),
    Provider(prefix="vertex_ai/", label="Google Vertex AI", effort="reasoning_effort"),
    Provider(prefix="bedrock/", label="Amazon Bedrock", gateway=True),
    Provider(prefix="mistral/", label="Mistral"),
    Provider(prefix="ollama_chat/", label="Ollama"),
    Provider(prefix="ollama/", label="Ollama"),
)

# Longest prefix first so `openrouter/` is never shadowed by a shorter match and
# `ollama_chat/` wins over `ollama/`.
_BY_LENGTH = tuple(sorted(REGISTRY, key=lambda p: len(p.prefix), reverse=True))

# Anything litellm can route but we have no record for: usable, not curated.
UNKNOWN = Provider(prefix="", label="", effort="reasoning_effort")


def of(model: str) -> Provider:
    """The provider record for a model string, or UNKNOWN."""
    for provider in _BY_LENGTH:
        if model.startswith(provider.prefix):
            return provider
    return UNKNOWN


def is_known(model: str) -> bool:
    return of(model) is not UNKNOWN


def inner(model: str) -> str:
    """Strip one gateway prefix: openrouter/anthropic/x -> anthropic/x."""
    provider = of(model)
    return model[len(provider.prefix):] if provider.gateway else model


def supports_prompt_caching(model: str) -> bool:
    """Caching is the model's property, so resolve through a gateway to it.

    OpenRouter passes cache_control through to Anthropic, so
    openrouter/anthropic/* caches while openrouter/meta-llama/* does not.
    """
    provider = of(model)
    if provider.gateway:
        return supports_prompt_caching(inner(model))
    return provider.prompt_caching


def label(model_or_prefix: str) -> str:
    """Display name. Falls back to the bare prefix for uncurated providers."""
    provider = of(model_or_prefix)
    if provider is not UNKNOWN:
        return provider.label
    return model_or_prefix.split("/")[0]


def catalogue() -> list[dict]:
    """Provider metadata for the UI, so labels live in one place."""
    seen: dict[str, dict] = {}
    for provider in REGISTRY:
        name = provider.prefix.rstrip("/")
        seen.setdefault(name, {"provider": name, "label": provider.label,
                               "effort": provider.effort is not None,
                               "prompt_caching": provider.prompt_caching,
                               "discovery": provider.discovery is not None})
    return list(seen.values())
