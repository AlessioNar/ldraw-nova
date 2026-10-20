"""Token-usage capture: the per-provider shapes litellm hands us, and the totals."""
from types import SimpleNamespace

import agent


def response(**usage):
    return SimpleNamespace(usage=SimpleNamespace(**usage))


def test_anthropic_cache_counters():
    """litellm surfaces Anthropic's cache fields directly on usage."""
    record = agent.usage_record(response(prompt_tokens=1200, completion_tokens=340,
                                        cache_read_input_tokens=18000, cache_creation_input_tokens=0))
    assert record == {"prompt": 1200, "completion": 340, "cache_read": 18000, "cache_write": 0}


def test_openai_style_cached_tokens_in_details():
    """OpenAI-style providers report the cache read under prompt_tokens_details."""
    record = agent.usage_record(response(prompt_tokens=900, completion_tokens=120,
                                         prompt_tokens_details=SimpleNamespace(cached_tokens=512)))
    assert record["cache_read"] == 512
    assert record["cache_write"] == 0


def test_provider_reporting_nothing():
    assert agent.usage_record(SimpleNamespace()) is None
    assert agent.usage_record(SimpleNamespace(usage=None)) is None
    # All-zero is not worth a record, and would otherwise count as a step.
    assert agent.usage_record(response(prompt_tokens=0, completion_tokens=0)) is None


def test_non_numeric_fields_are_ignored():
    """A provider sending None/strings must not crash the turn."""
    record = agent.usage_record(response(prompt_tokens=10, completion_tokens=None,
                                         cache_read_input_tokens="n/a"))
    assert record == {"prompt": 10, "completion": 0, "cache_read": 0, "cache_write": 0}


def test_totals_sum_only_real_records():
    messages = [
        {"role": "user", "content": "build a car"},
        {"role": "assistant", "_usage": {"prompt": 100, "completion": 10, "cache_read": 0, "cache_write": 18000}},
        {"role": "tool", "content": "ok"},
        {"role": "assistant", "_usage": {"prompt": 150, "completion": 20, "cache_read": 18000, "cache_write": 0}},
        {"role": "assistant", "_usage": "not a dict"},
        {"role": "assistant"},
    ]
    assert agent.usage_totals(messages) == {"prompt": 250, "completion": 30, "cache_read": 18000,
                                            "cache_write": 18000, "steps": 2}


def test_totals_of_an_untouched_chat():
    assert agent.usage_totals([]) == {"prompt": 0, "completion": 0, "cache_read": 0,
                                      "cache_write": 0, "steps": 0}
