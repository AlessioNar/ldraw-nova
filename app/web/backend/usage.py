"""Token usage bookkeeping: per-step records and per-chat running totals."""
from __future__ import annotations

from typing import Optional

import providers
import settings


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


def step_cost_usd(record: dict, pricing: Optional[dict], model: str, cache_ttl: str = "5m") -> Optional[float]:
    """Dollar cost of one step from the provider's per-million-token prices, or None when they are unknown.

    `prompt` includes the cached tokens (LiteLLM's convention). Anthropic bills a cache read at 0.1x and a
    write at 1.25x (2x for the 1 h cache); for every other provider the cache discount is not known, so a
    cached token is priced in full. That can overstate the cost but never understate it, which is the safe
    side for a budget.
    """
    if not pricing or pricing.get("currency") != "USD" or pricing.get("input") is None or pricing.get("output") is None:
        return None
    anthropic = providers.of(model).prefix == "anthropic/"
    read_x = pricing.get("cache_read_x", 0.1) if anthropic else 1.0
    write_x = (2.0 if cache_ttl == "1h" else 1.25) if anthropic else 1.0
    fresh = max(record["prompt"] - record["cache_read"] - record["cache_write"], 0)
    weighted = fresh + record["cache_read"] * read_x + record["cache_write"] * write_x
    price_in, price_out = pricing["input"], pricing["output"]
    for tier in sorted(pricing.get("tiers") or [], key=lambda t: t["over_prompt_tokens"]):
        if record["prompt"] > tier["over_prompt_tokens"]:      # e.g. Haiku 5.5: a prompt over 100k tokens costs 5x
            price_in, price_out = tier["input"], tier["output"]
    return (weighted * price_in + record["completion"] * price_out) / 1_000_000


def usage_totals(messages: list[dict]) -> dict:
    """Running totals for a chat, so a long build's cost is visible."""
    fields = ("prompt", "completion", "cache_read", "cache_write")
    totals = {field: 0 for field in fields}
    totals["steps"] = 0
    cost = 0.0
    worker_cost = 0.0
    unpriced = 0
    for message in messages:
        record = message.get("_usage")
        if not isinstance(record, dict):
            continue
        totals["steps"] += 1
        for field in fields:
            value = record.get(field)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                totals[field] += int(value)
        step = record.get("cost_usd")
        if isinstance(step, (int, float)) and not isinstance(step, bool):
            cost += step
            if message.get("_worker"):                    # a worker's aggregate record (or an old hidden `_subagent` step)
                worker_cost += step
        else:
            unpriced += 1
    rate = settings.eur_per_usd()
    totals.update(cost_usd=round(cost, 6), cost_eur=round(cost * rate, 4), eur_per_usd=rate, unpriced_steps=unpriced,
                  worker_cost_eur=round(worker_cost * rate, 4))
    return totals


def turn_stats(messages: list[dict]) -> dict:
    """Steps, cost and wall-clock duration of a chat's latest turn (everything after the last user message)."""
    start = max((i for i, m in enumerate(messages) if m.get("role") == "user"), default=-1)
    turn = messages[start + 1:]
    steps = sum(isinstance(m.get("_usage"), dict) for m in turn)
    stamps = [m["created_at"] for m in messages[max(start, 0):] if isinstance(m.get("created_at"), (int, float))]
    duration = round(stamps[-1] - stamps[0]) if start >= 0 and len(stamps) > 1 else None
    spent = sum(m["_usage"]["cost_usd"] for m in turn if isinstance(m.get("_usage"), dict)
                and isinstance(m["_usage"].get("cost_usd"), (int, float)) and not isinstance(m["_usage"]["cost_usd"], bool))
    return {"turn_steps": steps, "duration_s": duration, "request_cost_eur": round(spent * settings.eur_per_usd(), 4)}
