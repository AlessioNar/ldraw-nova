"""The understanding step with a mocked provider: schema, golden set, retry and fallback, cacheable prefix, cost."""
import asyncio
import json
import logging
from pathlib import Path
from types import SimpleNamespace

import jsonschema
import litellm
import pytest
from fastapi.testclient import TestClient

import model_catalog
import understand
import understand_eval
from main import app

STUB = understand.make_digest("""\
stub-car | car | coupe, sports car | palette:red,blue | body,wheels,glass
stub-house | house | cottage, shed | width,depth,courses | body,roof,door""")
HAIKU = {"model": "anthropic/claude-haiku-5-5", "api_key": "test-not-a-key"}
MISTRAL = {"model": "mistral/mistral-small-4", "api_key": "test-not-a-key"}
GOLDEN = understand_eval.load_golden()


def reply(spec, **usage):
    """A non-streaming completion carrying a forced tool call (spec may be a dict or raw text)."""
    arguments = spec if isinstance(spec, str) else json.dumps(spec)
    use = {"prompt_tokens": 2400, "completion_tokens": 250, "cache_read_input_tokens": 2300, "cache_creation_input_tokens": 0, **usage}
    call = SimpleNamespace(function=SimpleNamespace(name="emit_spec", arguments=arguments))
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(tool_calls=[call], content=None))],
                           usage=SimpleNamespace(**use))


@pytest.fixture
def provider(monkeypatch):
    """Script of replies (or exceptions); every acompletion call is recorded in .calls."""
    class Script:
        def __init__(self):
            self.replies, self.calls = [], []

        async def __call__(self, **kwargs):
            self.calls.append(kwargs)
            item = self.replies.pop(0) if self.replies else self.default(kwargs)
            if isinstance(item, Exception):
                raise item
            return item
    script = Script()
    script.default = lambda kwargs: pytest.fail("unscripted provider call")
    monkeypatch.setattr(litellm, "acompletion", script)
    return script


def run(coro):
    return asyncio.run(coro)


# ------------------------------------------------------------------ schemas and golden set

def test_schemas_are_valid_and_edit_objects_agree():
    jsonschema.Draft202012Validator.check_schema(understand.SPEC_SCHEMA)
    jsonschema.Draft202012Validator.check_schema(understand.EDIT_SCHEMA)
    assert understand.SPEC_SCHEMA["$defs"]["edit"] == understand.EDIT_SCHEMA["$defs"]["edit"]


def test_golden_set_shape():
    assert len(GOLDEN) >= 60
    assert len({g["id"] for g in GOLDEN}) == len(GOLDEN)
    assert {"owner-car", "owner-cottage", "owner-starship", "home-tower", "home-crane"} <= {g["id"] for g in GOLDEN}
    assert sum(g["expected"]["intent"] == "edit" for g in GOLDEN) >= 20
    for g in GOLDEN:
        assert understand.validate(g["expected"]) == [], g["id"]


def test_invalid_specs_are_rejected():
    assert understand.validate({"intent": "new_model"})                                    # no subject
    assert understand.validate({"intent": "new_model", "subject": {"family": "dragon"}})   # family not in the enum
    assert understand.validate({"intent": "new_model", "subject": {"family": "car"}, "colour": "Red"})   # unknown key
    assert understand.validate({"intent": "edit", "subject": {"family": "car"}})           # edit without edits
    assert understand.validate({"intent": "edit", "subject": {"family": "car"}, "edits": [{"op": "paint", "target": "x"}]})
    assert understand.validate("not a dict")


# ------------------------------------------------------------------ golden set through a mocked provider

def test_every_golden_prompt_validates_through_the_mock(provider):
    by_prompt = {}
    provider.default = lambda kwargs: reply(by_prompt[kwargs["messages"][1]["content"].rsplit("REQUEST: ", 1)[1]])
    for g in GOLDEN:
        by_prompt[g["prompt"]] = g["expected"]
    rows = run(understand_eval.run_golden(HAIKU, use_rules=False, digest=STUB))
    assert len(rows) == len(GOLDEN) and len(provider.calls) == len(GOLDEN)
    assert all(r["valid"] and r["source"] == "llm" and r["attempts"] == 1 for r in rows)
    summary = understand_eval.rates(rows)
    assert summary["family"]["rate"] == summary["colours"]["rate"] == summary["edits"]["rate"] == summary["intent"]["rate"] == 1.0
    assert summary["fallbacks"] == 0 and summary["median_cost_usd"] > 0


def test_rules_answer_trivial_cases_without_a_call(provider):
    rule_cases = [g for g in GOLDEN if g.get("rules")]
    assert len(rule_cases) >= 8
    for g in rule_cases:
        result = run(understand.understand(g["prompt"], current_spec=g.get("current_spec"), previous_text=g.get("previous_text"),
                                           params=HAIKU, digest=STUB))
        assert result.source == "rules" and result.attempts == 0, g["id"]
        assert understand.validate(result.spec) == [], g["id"]
        assert all(v is not False for v in understand_eval.match(g["expected"], result.spec).values()), (g["id"], result.spec)
    assert provider.calls == []


def test_rules_do_not_swallow_real_requests(provider):
    provider.replies = [reply({"intent": "new_model", "subject": {"family": "car"}})]
    result = run(understand.understand("make the car red and fast", current_spec={"intent": "new_model", "subject": {"family": "car"}},
                                       params=HAIKU, digest=STUB))
    assert result.source == "llm" and len(provider.calls) == 1


def test_keyword_fallback_is_valid_and_reasonable_without_a_model():
    rows = run(understand_eval.run_golden(None, use_rules=True))
    assert all(r["valid"] and r["source"] in ("rules", "rules_fallback") for r in rows)
    summary = understand_eval.rates(rows)
    assert summary["calls"] == 0
    assert summary["family"]["rate"] >= 0.7 and summary["colours"]["rate"] >= 0.7, summary


# ------------------------------------------------------------------ retry and fallback

SPEC = {"intent": "new_model", "subject": {"family": "car", "kind": "coupe"}, "colours": {"body": "Red"}}


def test_malformed_output_is_retried_once_then_succeeds(provider):
    provider.replies = [reply("this is not json"), reply(SPEC)]
    result = run(understand.understand("a red car", params=HAIKU, digest=STUB))
    assert result.source == "llm" and result.attempts == 2 and len(result.errors) == 1
    first, second = (c["messages"] for c in provider.calls)
    assert first[0] == second[0]                                            # the cached prefix is untouched by the retry
    assert "INVALID" not in first[1]["content"] and "INVALID" in second[1]["content"]
    assert result.usage["prompt"] == 4800                                    # both calls are paid and counted


def test_schema_violation_counts_as_malformed(provider):
    provider.replies = [reply({"intent": "new_model", "subject": {"family": "dragon"}}), reply(SPEC)]
    result = run(understand.understand("a red car", params=HAIKU, digest=STUB))
    assert result.attempts == 2 and result.source == "llm" and "dragon" in result.errors[0]


def test_two_failures_fall_back_to_rules(provider):
    provider.replies = [reply("{broken"), reply({"intent": "nope"})]
    result = run(understand.understand("a small blue truck", params=HAIKU, digest=STUB))
    assert result.source == "rules_fallback" and result.attempts == 2 and len(provider.calls) == 2   # exactly one retry
    assert understand.validate(result.spec) == []
    assert result.spec["subject"]["family"] == "truck" and result.spec["colours"] == {"body": "Blue"}


def test_provider_error_is_retried_then_falls_back(provider):
    provider.replies = [RuntimeError("503 overloaded"), TimeoutError("slow")]
    result = run(understand.understand("a house", params=HAIKU, digest=STUB))
    assert result.source == "rules_fallback" and result.attempts == 2 and result.spec["subject"]["family"] == "house"
    assert result.usage is None


def test_no_model_configured_uses_rules_without_calls(provider):
    result = run(understand.understand("a white jet", params=None, digest=STUB))
    assert result.source == "rules_fallback" and result.attempts == 0 and result.spec["subject"]["family"] == "aircraft"
    assert provider.calls == []


def test_json_in_text_is_accepted_for_models_that_ignore_tools(provider):
    text_reply = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(tool_calls=None, content="```json\n" + json.dumps(SPEC) + "\n```"))],
                                 usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5))
    provider.replies = [text_reply]
    assert run(understand.understand("a red car", params=MISTRAL, digest=STUB)).spec == SPEC


def test_colour_names_are_normalised_and_unknown_ones_reported(provider):
    odd = {"intent": "new_model", "subject": {"family": "car"}, "colours": {"body": "light bluish grey", "wheels": "chartreuse"}}
    provider.replies = [reply(odd)]
    result = run(understand.understand("a grey car with chartreuse wheels", params=HAIKU, digest=STUB))
    assert result.spec["colours"]["body"] == "Light Bluish Gray" and result.warnings == ["unknown colour 'chartreuse' for wheels"]


def test_request_is_capped_and_params_are_ours(provider):
    provider.replies = [reply(SPEC)]
    run(understand.understand("car " * 5000, params={**HAIKU, "stream": True, "tools": ["x"], "num_retries": 9}, digest=STUB))
    call = provider.calls[0]
    assert len(call["messages"][1]["content"]) < understand.MAX_REQUEST_CHARS + 100
    assert call["max_tokens"] == understand.MAX_OUTPUT_TOKENS and call["num_retries"] == 0
    assert "stream" not in call and call["tools"][0]["function"]["name"] == "emit_spec"


# ------------------------------------------------------------------ byte-stable prefix

def test_prefix_and_tool_are_byte_stable(provider):
    provider.default = lambda kwargs: reply(SPEC)
    for text, current in (("a red car", None), ("make the roof blue and more", SPEC), ("something else entirely", None)):
        run(understand.understand(text, current_spec=current, params=HAIKU, digest=STUB, options={"parts_mode": "prefer"}))
    systems = {json.dumps(c["messages"][0], sort_keys=True) for c in provider.calls}
    tools = {json.dumps(c["tools"], sort_keys=False) for c in provider.calls}
    assert len(systems) == 1 and len(tools) == 1
    assert understand.prefix(STUB) == understand.prefix(understand.make_digest(STUB.text))
    assert understand.prefix(STUB).encode() == understand.prefix(STUB).encode()
    system = provider.calls[0]["messages"][0]["content"]
    assert system[0]["text"] == understand.prefix(STUB) and system[0]["cache_control"] == {"type": "ephemeral"}


def test_prefix_holds_nothing_variable_and_changes_only_with_the_digest():
    base = understand.prefix(STUB)
    assert understand.prefix(understand.make_digest(STUB.text + "\nnew | car | x | y | z")) != base
    assert STUB.text in base and "Dark Bluish Gray" in base
    assert "REQUEST:" not in base
    assert json.dumps(understand.tool(), sort_keys=True) == json.dumps(understand.tool(), sort_keys=True)
    assert list(understand.tool()["function"]["parameters"]["properties"]) == sorted(understand.tool()["function"]["parameters"]["properties"])


def test_cache_control_only_where_the_provider_accepts_it_and_tool_choice_per_provider(provider):
    provider.default = lambda kwargs: reply(SPEC)
    run(understand.understand("a red car", params=MISTRAL, digest=STUB))
    call = provider.calls[0]
    assert isinstance(call["messages"][0]["content"], str) and call["tool_choice"] == "required"
    run(understand.understand("a red car", params=HAIKU, digest=STUB))
    assert provider.calls[1]["tool_choice"] == {"type": "function", "function": {"name": "emit_spec"}}


def test_digest_falls_back_when_the_registry_is_absent():
    digest = understand.load_digest()
    assert digest.text and len(digest.version) == 12
    assert understand.load_digest() == digest


def test_prompt_token_estimate_is_in_budget():
    """No tokenizer in the test image: 4 characters per token is the proposal's sizing; the cap is 3k in."""
    tokens = (len(understand.prefix(understand.load_digest())) + len(json.dumps(understand.tool()))) / 4
    assert tokens < 3000


# ------------------------------------------------------------------ cost logging

def test_cost_is_computed_from_the_price_book_and_logged(provider, caplog):
    provider.replies = [reply(SPEC)]
    with caplog.at_level(logging.INFO, logger="ldraw-nova.understand"):
        result = run(understand.understand("a red car", params=HAIKU, digest=STUB))
    price = model_catalog.PRICE_BOOK["anthropic/claude-haiku-5-5"]
    expected = ((100 + 2300 * price["cache_read_x"]) * price["input"] + 250 * price["output"]) / 1e6
    assert result.usage["cost_usd"] == pytest.approx(expected) and 0.00014 < expected < 0.00017
    assert result.usage["cache_read"] == 2300 and result.latency_s >= 0
    assert any("cost_usd" in r.getMessage() and "anthropic/claude-haiku-5-5" in r.getMessage() for r in caplog.records)


def test_retry_cost_adds_up(provider):
    provider.replies = [reply("junk"), reply(SPEC)]
    single = run(understand.understand("a red car", params=HAIKU, digest=STUB))
    provider.replies = [reply(SPEC)]
    one = run(understand.understand("a red car", params=HAIKU, digest=STUB))
    assert single.usage["cost_usd"] == pytest.approx(2 * one.usage["cost_usd"])


# ------------------------------------------------------------------ internal endpoint

def test_endpoint_runs_rules_and_reports_fallback_without_a_model():
    with TestClient(app) as client:
        shortcut = client.post("/api/understand", json={"text": "undo", "current_spec": SPEC}).json()
        assert shortcut["source"] == "rules" and shortcut["spec"]["edits"][0]["op"] == "undo"
        fallback = client.post("/api/understand", json={"text": "a green van"}).json()
        assert fallback["source"] == "rules_fallback" and fallback["spec"]["subject"]["family"] == "van"
        assert client.post("/api/understand", json={"text": ""}).json()["source"] == "none"
