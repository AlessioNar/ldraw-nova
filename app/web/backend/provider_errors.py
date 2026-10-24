"""Provider key checks and plain-language provider errors. Never includes a key or a raw provider message."""
from __future__ import annotations

import hashlib

import httpx
import litellm

import environment_config
import model_discovery
import providers

# Provider key checks use each provider's free lookup, never a completion, so a check spends no tokens.
KEY_CHECKS = {
    "anthropic": ("ANTHROPIC_API_KEY", "https://api.anthropic.com/v1/models?limit=1",
                  lambda key: {"x-api-key": key, "anthropic-version": "2023-06-01"}),
    "openai": ("OPENAI_API_KEY", "https://api.openai.com/v1/models", lambda key: {"Authorization": f"Bearer {key}"}),
    "mistral": ("MISTRAL_API_KEY", "https://api.mistral.ai/v1/models", lambda key: {"Authorization": f"Bearer {key}"}),
    # OpenRouter's model list is public; /key is the free call that actually checks the key.
    "openrouter": ("OPENROUTER_API_KEY", "https://openrouter.ai/api/v1/key", lambda key: {"Authorization": f"Bearer {key}"}),
}
NAMES = {"anthropic": "Anthropic", "openai": "OpenAI", "mistral": "Mistral", "openrouter": "OpenRouter"}

_rejected: dict[str, str] = {}   # provider id -> fingerprint of the key that was rejected (memory only)


def _fingerprint(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def provider_id(model: str) -> str:
    return providers.of(model).prefix.rstrip("/")


def record(provider: str, key: str, rejected: bool) -> None:
    if rejected:
        _rejected[provider] = _fingerprint(key)
    else:
        _rejected.pop(provider, None)


def rejected_providers() -> list[str]:
    """Providers whose current saved key is the one already known to be rejected."""
    env = environment_config.snapshot()
    return sorted(p for p, fp in _rejected.items()
                  if (key := env.get(KEY_CHECKS[p][0])) and _fingerprint(key) == fp)


async def check_key(provider: str, key: str, remember: bool = True) -> dict:
    """Free key check. status: ok | rejected | unreachable | error. remember=False for keys that are not the provider key."""
    _name, url, headers = KEY_CHECKS[provider]
    shown = NAMES[provider]
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
            await model_discovery._fetch_json(client, url, headers(key))
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        if code in (401, 403):
            if remember:
                record(provider, key, True)
            return {"ok": False, "status": "rejected", "error": f"Key rejected by {shown}. Paste a new key and save it."}
        return {"ok": False, "status": "error", "error": f"{shown} answered HTTP {code}, so the key was not checked."}
    except Exception:  # noqa: BLE001 - never echo an error that could contain the key
        return {"ok": False, "status": "unreachable", "error": f"Could not reach {shown}. The key is saved but not checked."}
    if remember:
        record(provider, key, False)
    return {"ok": True, "status": "ok", "message": "Key accepted (checked with the provider's free models lookup; no tokens used)."}


def describe(exc: Exception, model: str, own_key: bool = False) -> str | None:
    """A plain-words message for a provider failure, or None when the cause is not recognised."""
    pid = provider_id(model)
    shown = NAMES.get(pid) or providers.label(model) or "The provider"
    short = model[len(providers.of(model).prefix):] or model
    text = str(exc).lower()
    if isinstance(exc, (litellm.AuthenticationError, litellm.PermissionDeniedError)):
        if own_key:
            return f"{shown} rejected the API key stored on this agent for {short}. Open Settings and replace the agent's key."
        if pid in KEY_CHECKS and (key := environment_config.snapshot().get(KEY_CHECKS[pid][0])):
            record(pid, key, True)
        return f"{shown} rejected the API key for {short}. Open Settings, paste a new {shown} key and save it."
    if isinstance(exc, litellm.RateLimitError) or "credit balance" in text or "quota" in text:
        return (f"{shown} refused the request for {short}: the rate limit is reached or your credit is used up. "
                f"Wait a minute and try again, or add credit with {shown}.")
    if isinstance(exc, litellm.NotFoundError):
        return f"{shown} does not know the model {short}. Open Settings and pick another model."
    if isinstance(exc, (litellm.APIConnectionError, litellm.Timeout, litellm.ServiceUnavailableError)):
        return f"Could not reach {shown} for {short}. Check your internet connection and try again."
    return None
