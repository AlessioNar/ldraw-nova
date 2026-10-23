"""Builder presets and published capabilities; no network work on UI reads.

The bundled snapshot has per-model sources and verification dates. Test refreshes
provider metadata when available, without mistaking a successful text response
for proof of vision, tool use, or a particular context limit.
"""
import copy
import json
import math
from pathlib import Path

import litellm

import inventories

EFFORTS = ["low", "medium", "high", "xhigh", "max"]
_PROFILES = json.loads(Path(__file__).with_name("model_catalog_data.json").read_text())
CATALOG = [entry for entry in _PROFILES if entry["preset"]]
OPENROUTER_CATALOG = [entry for entry in CATALOG if entry["model"].startswith("openrouter/")]


# Prices and cache terms read from https://platform.claude.com/docs/en/about-claude/pricing on 2026-10-23. They
# overlay the catalogue and LiteLLM's figures, which miss two things the page states: Haiku 5.5 costs 5x more for
# prompts over 100,000 tokens, and cache reads on Opus 5.5 / Sonnet 5.5 / Fable 5.1 are 0.05x / 0.05x / 0.025x of
# the input price (0.1x for the other models). Cache writes are 1.25x (5 minutes) and 2x (1 hour) everywhere.
PRICE_BOOK = {
    "anthropic/claude-haiku-5-5": {"input": 0.1, "output": 0.5, "cache_read_x": 0.1,
                                   "tiers": [{"over_prompt_tokens": 100_000, "input": 0.5, "output": 2.5}]},
    "anthropic/claude-sonnet-5-5": {"input": 2, "output": 10, "cache_read_x": 0.05},
    "anthropic/claude-opus-5-5": {"input": 4, "output": 20, "cache_read_x": 0.05},
    "anthropic/claude-fable-5-1": {"input": 10, "output": 50, "cache_read_x": 0.025},
}


def profile(model: str) -> dict:
    spec = _profile(model)
    book = PRICE_BOOK.get(model.replace("responses/", ""))
    if book:
        spec["pricing"] = {**(spec.get("pricing") or {}), **copy.deepcopy(book), "currency": "USD"}
    return spec


def _profile(model: str) -> dict:
    canonical = model.replace("responses/", "")
    known = next((m for m in _PROFILES if m["model"] == canonical), None)
    if known:
        return {**copy.deepcopy(known), "context_budgets": [known["context_window"]]}
    try:
        info = litellm.get_model_info(model)
    except Exception:
        info = {}
    window = info.get("max_input_tokens") or None
    def support(key):
        return info.get(key) if isinstance(info.get(key), bool) else None
    def rate(key):
        value = info.get(key)
        return round(value * 1_000_000, 8) if isinstance(value, (float, int)) and math.isfinite(value) and value >= 0 else None
    return {"model": model, "name": model, "context_window": window,
            "max_output_tokens": info.get("max_output_tokens"),
            "context_budgets": [window] if window else [], "default_effort": None,
            "tools": support("supports_function_calling"), "vision": support("supports_vision"),
            "reasoning": support("supports_reasoning"), "efforts": [],
            "pricing": {"input": rate("input_cost_per_token"), "output": rate("output_cost_per_token"), "currency": "USD"} if info else None,
            "source_label": "LiteLLM catalogue" if info else None,
            "source_url": None, "verified_at": None}


def builder_supported(model: str) -> bool:
    spec = profile(model)
    return spec["tools"] is True and spec["vision"] is True


def entry_profile(entry: dict) -> dict:
    params = entry["litellm_params"]
    spec = profile(params["model"])
    metadata = (entry.get("_connection_test") or {}).get("metadata")
    if metadata:
        spec.update(copy.deepcopy(metadata))
    extra = params.get("extra_body") or {}
    configured = (extra.get("reasoning", {}).get("effort") or params.get("reasoning_effort")
                  or params.get("output_config", {}).get("effort"))
    if configured in spec["efforts"]:
        spec["default_effort"] = configured
    return spec


def validate_options(entry: dict, options: dict | None) -> dict:
    value = {"mode": "agent", "permissions": "ask", "effort": None, "context_tokens": None, "budget_eur": None, **(options or {})}
    if value["mode"] not in ("plan", "agent"):
        raise ValueError("Unknown mode")
    if value["permissions"] not in ("ask", "full", "read_only"):
        raise ValueError("Unknown permission setting")
    spec = entry_profile(entry)
    if value["effort"] is None:
        value["effort"] = spec["default_effort"]
    if value["context_tokens"] is None:
        value["context_tokens"] = spec["context_window"]
    if value["effort"] and value["effort"] not in spec["efforts"]:
        raise ValueError("This effort is not supported by the selected model")
    if value["context_tokens"] is not None and value["context_tokens"] not in spec["context_budgets"]:
        raise ValueError("This context budget is not supported by the selected model")
    budget = value["budget_eur"]
    if budget is not None:
        if isinstance(budget, bool) or not isinstance(budget, (int, float)) or not 0 < budget <= 1000:
            raise ValueError("The budget must be an amount in euros above 0 and up to 1000")
        prices = spec.get("pricing") or {}
        if prices.get("input") is None or prices.get("output") is None or prices.get("currency") != "USD":
            raise ValueError("This model has no known prices, so a budget cannot be enforced")
    worker = value.get("worker_model_id", True)
    if worker is True:                                    # not given: the Settings / env default
        import llm_config                                 # lazy: llm_config imports this module
        worker = llm_config.default_worker_id()
    if worker is not None and not isinstance(worker, str):
        raise ValueError("The worker model must be a model id or empty")
    value["worker_model_id"] = worker or None
    inventories.validate_parts_options(value)
    return value
