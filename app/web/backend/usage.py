"""Token usage bookkeeping: per-step records and per-chat running totals."""
from __future__ import annotations

from typing import Optional


def usage_record(response) -> Optional[dict]:
    """Token usage for one step, including the prompt-cache counters.

    Providers report caching differently through litellm: Anthropic adds
    cache_read_input_tokens / cache_creation_input_tokens, OpenAI-style
    providers put reads in prompt_tokens_details.cached_tokens (and OpenRouter
    writes in cache_creation_tokens / cache_write_tokens), and plenty
    report nothing. Returns None when there is nothing worth storing.
    """
    usage = getattr(response, "usage", None)
    if usage is None:
        return None

    def number(*names) -> int:
        for name in names:
            found = getattr(usage, name, None)
            if isinstance(found, (int, float)) and not isinstance(found, bool):
                return int(found)
        return 0

    details = getattr(usage, "prompt_tokens_details", None)
    cached = getattr(details, "cached_tokens", None) if details is not None else None
    written = getattr(details, "cache_creation_tokens", None) or getattr(details, "cache_write_tokens", None) if details is not None else None
    record = {"prompt": number("prompt_tokens"),
              "completion": number("completion_tokens"),
              "cache_read": number("cache_read_input_tokens") or (int(cached) if isinstance(cached, (int, float)) else 0),
              "cache_write": number("cache_creation_input_tokens") or (int(written) if isinstance(written, (int, float)) else 0)}
    return record if any(record.values()) else None


def usage_totals(messages: list[dict]) -> dict:
    """Running totals for a chat, so a long build's cost is visible."""
    fields = ("prompt", "completion", "cache_read", "cache_write")
    totals = {field: 0 for field in fields}
    totals["steps"] = 0
    for message in messages:
        record = message.get("_usage")
        if not isinstance(record, dict):
            continue
        totals["steps"] += 1
        for field in fields:
            value = record.get(field)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                totals[field] += int(value)
    return totals
