"""The fast-path decision engine with a mocked provider: routes, clamping, edits, the AI estimate, Settings, speed.

No live calls: litellm.acompletion is scripted and the generator registry comes from tests/generators_registry.json
(a dump of the real registry, so parameter schemas are the generators' own).
"""
import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace

import jsonschema
import litellm
import pytest
from fastapi.testclient import TestClient

import fast_path
import generator_bridge
import llm_config
import understand
import understand_eval
from main import app
from store import ChatStore

REGISTRY = json.loads((Path(__file__).with_name("generators_registry.json")).read_text(encoding="utf-8"))
ENTRIES = {e["id"]: e for e in REGISTRY["entries"]}
GOLDEN = understand_eval.load_golden()
HAIKU = {"model": "anthropic/claude-haiku-5-5", "api_key": "test-not-a-key"}
HOUSE = {"generator": "house", "params": ENTRIES["house"]["defaults"]}
CAR = {"generator": "grand-tourer", "params": {**ENTRIES["grand-tourer"]["defaults"]}}
REAL_HISTORY = fast_path.history_stats
ROUTES = {"generator", "edit", "needs_ai", "clarify"}


def reply(spec, **usage):
    use = {"prompt_tokens": 2400, "completion_tokens": 250, "cache_read_input_tokens": 2300, "cache_creation_input_tokens": 0, **usage}
    call = SimpleNamespace(function=SimpleNamespace(name="emit_spec", arguments=spec if isinstance(spec, str) else json.dumps(spec)))
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(tool_calls=[call], content=None))],
                           usage=SimpleNamespace(**use))


@pytest.fixture(autouse=True)
def registry(monkeypatch):
    generator_bridge.set_registry(REGISTRY)
    monkeypatch.setattr(fast_path, "history_stats", lambda force=False: None)
    yield
    generator_bridge.set_registry(None)


@pytest.fixture
def provider(monkeypatch):
    """Scripted acompletion: .specs maps the request text to the spec the 'model' returns; .calls records every call."""
    class Script:
        def __init__(self):
            self.specs, self.calls = {}, []

        async def __call__(self, **kwargs):
            self.calls.append(kwargs)
            text = kwargs["messages"][1]["content"].rsplit("REQUEST: ", 1)[1].split("\n")[0]
            return reply(self.specs[text])
    script = Script()
    monkeypatch.setattr(litellm, "acompletion", script)

    async def params(llm_id=None):
        return HAIKU
    monkeypatch.setattr(understand, "model_params", params)
    return script


def describe(text, current=None, previous=None):
    return asyncio.run(fast_path.describe(text, current=current, previous_text=previous))


def valid(decision):
    entry = ENTRIES[decision["generator"]]
    jsonschema.Draft202012Validator(entry["params"]).validate(decision["params"])


# ------------------------------------------------------------------ rules path: no model call, no cost

def test_home_presets_route_without_any_call(provider):
    car = describe("Build a small red car with four black wheels")
    assert car["route"] == "generator" and car["generator"] == "grand-tourer" and car["cost_eur"] == 0.0
    assert car["params"]["body"] == "red" and car["params"]["wheels"] == "black" and car["understanding_cost_eur"] == 0.0
    assert car["params"]["length"] == ENTRIES["grand-tourer"]["defaults"]["length"]          # "small" = the compact default
    valid(car)
    cottage = describe("A 6 x 8 cottage with a door, two windows and a sloped roof")
    assert cottage["generator"] == "house" and cottage["params"]["width"] == 6 and cottage["params"]["depth"] == 8
    assert cottage["params"]["windows"] == 2 and cottage["params"]["door_side"] != "none"
    assert any("studs" in n for n in cottage["notes"])                      # the unit assumption is stated, not asked
    valid(cottage)
    assert describe("Build a spiral staircase around a tall stone tower")["route"] == "needs_ai"
    crane = describe("Design a harbour crane with a long boom and a cargo dock")
    assert crane["route"] == "needs_ai" and crane["estimate"]["eur_high"] > crane["estimate"]["eur_low"] and crane["reason"]
    assert provider.calls == []


def test_every_rules_golden_prompt_decides_for_free(provider):
    rules = [g for g in GOLDEN if g.get("rules")]
    assert len(rules) >= 8
    for g in rules:
        current = HOUSE if g["expected"]["intent"] == "edit" else None
        decision = describe(g["prompt"], current, previous="make the roof blue")
        assert decision["route"] in ROUTES and decision["understanding_cost_eur"] == 0.0, g["id"]
        if decision["route"] in ("generator", "edit"):
            valid(decision)
    assert provider.calls == []


def test_rules_recolour_is_an_edit_of_the_current_build(provider):
    decision = describe("make the roof blue", HOUSE)
    assert decision["route"] == "edit" and decision["generator"] == "house"
    assert decision["changes"] == [{"param": "roof_colour", "label": "roof colour", "from": "dark red", "to": "blue"}]
    assert decision["params"]["roof_colour"] == "blue" and decision["params"]["wall_colour"] == HOUSE["params"]["wall_colour"]
    assert provider.calls == []
    assert describe("paint the body green", CAR)["params"]["body"] == "green"


def test_undo_returns_the_history_action_and_no_param_change(provider):
    decision = describe("undo", HOUSE)
    assert decision["route"] == "edit" and decision["history"] == "undo" and decision["changes"] == []
    assert describe("undo")["route"] == "clarify"                            # nothing to undo without a build


# ------------------------------------------------------------------ the whole golden set through the mocked model

def test_every_golden_prompt_gets_a_valid_decision(provider):
    for g in GOLDEN:
        provider.specs[g["prompt"]] = g["expected"]
    seen = set()
    for g in GOLDEN:
        edit = g["expected"]["intent"] == "edit"
        decision = describe(g["prompt"], HOUSE if edit and g["expected"].get("subject", {}).get("family") != "car" else CAR if edit else None)
        assert decision["route"] in ROUTES, g["id"]
        assert decision["understanding_cost_eur"] is not None and decision["understanding_cost_eur"] >= 0
        seen.add(decision["route"])
        if decision["route"] in ("generator", "edit"):
            valid(decision)
        if decision["route"] == "needs_ai":
            assert decision["spec"] and decision["reason"] and set(decision["estimate"]) >= {"eur_low", "eur_high", "minutes_low", "minutes_high"}
        if decision["route"] == "clarify":
            assert decision["question"]
    assert seen == ROUTES


def test_real_world_requests_pick_the_right_generator(provider):
    cases = {"a truck": "tipper-truck", "build a green pickup": "pickup", "yellow delivery van please": "delivery-van",
             "a black touring motorbike": "touring-motorcycle", "a small blue harbour launch": "harbour-launch",
             "white courier jet with red accents": "courier-jet", "Build a small white and blue starship": "starfighter",
             "a house with a red roof and a chimney": "house"}
    for g in GOLDEN:
        provider.specs[g["prompt"]] = g["expected"]
    for text, generator in cases.items():
        decision = describe(text)
        assert decision["route"] == "generator" and decision["generator"] == generator, (text, decision)
    house = describe("a house 12 studs wide and 10 deep, 8 courses tall")
    assert (house["params"]["width"], house["params"]["depth"], house["params"]["courses"]) == (12, 10, 8)
    assert describe("a house with a red roof and a chimney")["params"]["chimney"] == "right"
    for text in ("Build the Millennium Falcon", "a big grey space freighter", "a helicopter", "technic gearbox", "a steam train", "build a red car using only parts I own"):
        assert describe(text)["route"] == "needs_ai", text
    assert describe("hello")["route"] == "clarify" and describe("build something cool")["route"] == "clarify"


# ------------------------------------------------------------------ clamping and validation

def test_out_of_range_values_are_clamped_with_notes():
    house = ENTRIES["house"]
    params, notes = fast_path.clamp(house, {"width": 40, "depth": 9, "windows": -3, "roof_style": "pyramid", "wall_colour": "Light Bluish Gray",
                                            "roof_colour": "plaid", "bogus": 1})
    assert params["width"] == 24 and params["depth"] == 10 and params["windows"] == 0 and params["roof_style"] == "gable"
    assert params["wall_colour"] == "light bluish grey" and params["roof_colour"] == house["defaults"]["roof_colour"]
    assert len(notes) == 6 and any("width 40 adjusted to 24" in n for n in notes) and any("bogus" in n for n in notes)
    jsonschema.Draft202012Validator(house["params"]).validate(params)


def test_a_spec_with_extreme_numbers_still_yields_valid_params(provider):
    spec = {"intent": "new_model", "subject": {"family": "house", "kind": "cottage"},
            "size": {"studs": {"width": 99, "depth": 3, "height_courses": 40}}, "colours": {"roof": "Flat Silver", "body": "Dark Bluish Gray"},
            "features": [{"name": "windows", "count": 20}]}
    provider.specs["huge cottage"] = spec
    decision = describe("huge cottage")
    assert decision["route"] == "generator" and decision["params"]["width"] == 24 and decision["params"]["depth"] == 6
    assert decision["params"]["courses"] == 14 and decision["params"]["windows"] == 8 and decision["params"]["roof_colour"] == "silver"
    assert any("adjusted" in n for n in decision["notes"])
    valid(decision)


def test_scale_words_move_size_params_around_the_default():
    notes = []
    sizes = {s: fast_path._size_params(ENTRIES["grand-tourer"], {"size": {"scale": s}}, notes)["length"]
             for s in ("micro", "mini", "small", "medium", "large")}
    assert sizes["micro"] == 14 and sizes["small"] == 18 and sizes["micro"] < sizes["mini"] <= sizes["small"] < sizes["medium"] < sizes["large"] <= 32


# ------------------------------------------------------------------ edit route: param diffs

def test_edit_scale_param_from_the_model(provider):
    provider.specs["4 studs longer"] = {"intent": "edit", "subject": {"family": "car"},
                                        "edits": [{"op": "scale_param", "target": "whole", "delta": "longer", "amount": 4, "unit": "studs"}]}
    decision = describe("4 studs longer", CAR)
    assert decision["route"] == "edit"
    assert decision["changes"] == [{"param": "length", "label": "length", "from": 18, "to": 22}]
    assert provider.calls and decision["understanding_cost_eur"] > 0
    provider.specs["much longer"] = {"intent": "edit", "subject": {"family": "car"},
                                     "edits": [{"op": "scale_param", "target": "whole", "delta": "longer", "amount": 100, "unit": "studs"}]}
    clamped = describe("much longer", CAR)
    assert clamped["params"]["length"] == 32 and any("adjusted to 32" in n for n in clamped["notes"])


def test_edit_features_colours_and_unmappable_changes(provider):
    provider.specs["add a chimney and remove the porch"] = {"intent": "edit", "subject": {"family": "house"}, "edits": [
        {"op": "add_feature", "target": "chimney"}, {"op": "remove_feature", "target": "porch"}, {"op": "add_feature", "target": "spiral staircase"}]}
    decision = describe("add a chimney and remove the porch", {"generator": "house", "params": {"porch": True}})
    assert decision["route"] == "edit"
    assert {c["param"]: (c["from"], c["to"]) for c in decision["changes"]} == {"chimney": ("none", "right"), "porch": (True, False)}
    assert any("staircase" in n for n in decision["notes"])                   # applied what maps, said what did not
    provider.specs["swap the cab for a flatbed"] = {"intent": "edit", "subject": {"family": "house"}, "edits": [{"op": "swap_part", "target": "cab", "value": "flatbed"}]}
    assert describe("swap the cab for a flatbed", HOUSE)["route"] == "needs_ai"
    provider.specs["two more windows"] = {"intent": "edit", "subject": {"family": "house"}, "edits": [{"op": "add_feature", "target": "window", "count": 2}]}
    assert describe("two more windows", HOUSE)["changes"][0]["to"] == HOUSE["params"]["windows"] + 2


def test_edit_replace_model_switches_generator_and_keeps_colours(provider):
    provider.specs["make it a truck instead"] = {"intent": "edit", "subject": {"family": "car"}, "edits": [{"op": "replace_model", "target": "whole", "value": "tipper truck"}]}
    current = {"generator": "grand-tourer", "params": {**CAR["params"], "body": "red"}}
    decision = describe("make it a truck instead", current)
    assert decision["route"] == "generator" and decision["generator"] == "tipper-truck" and decision["params"]["body"] == "red"


# ------------------------------------------------------------------ needs_ai estimate from history

def test_ai_estimate_default_without_history():
    assert fast_path.ai_estimate() == {**fast_path.DEFAULT_ESTIMATE, "basis": "default"}


def test_summarise_uses_quartiles_and_needs_three_chats():
    assert fast_path.summarise([1.0, 2.0], [10, 20]) is None
    stats = fast_path.summarise([1.0, 1.5, 2.0, 3.0, 9.0], [10, 15, 20, 30, 90])
    assert (stats["eur_low"], stats["eur_median"], stats["eur_high"]) == (1.5, 2.0, 3.0)
    assert (stats["minutes_low"], stats["minutes_median"], stats["minutes_high"]) == (15, 20, 30)


def test_estimate_reads_finished_chats_from_the_store(tmp_path, monkeypatch):
    store = ChatStore(tmp_path / "chats", tmp_path / "out")
    monkeypatch.setattr(fast_path, "get_store", lambda: store)
    monkeypatch.setattr(fast_path, "history_stats", REAL_HISTORY)
    for minutes, cost in ((10, 0.5), (20, 1.0), (30, 1.5), (40, 3.0)):
        chat = store.create_chat()
        start = 1000.0
        store.add_message(chat["id"], {"role": "user", "content": "build", "created_at": start})
        store.add_message(chat["id"], {"role": "assistant", "content": "done", "created_at": start + minutes * 60,
                                       "_usage": {"prompt": 1, "completion": 1, "cache_read": 0, "cache_write": 0, "cost_usd": cost}})
    store.create_chat()                                                       # an empty chat counts for nothing
    stats = fast_path.history_stats(force=True)
    assert stats["chats"] == 4 and stats["minutes_median"] == 25 and stats["eur_low"] < stats["eur_median"] < stats["eur_high"]
    estimate = fast_path.ai_estimate()
    assert estimate["basis"].startswith("quartiles of 4") and estimate["minutes_high"] > estimate["minutes_low"]
    fast_path._history.update(at=-1e9, stats=None)


# ------------------------------------------------------------------ Settings role

@pytest.fixture
def client():
    llm_config.set_understanding_default(None)
    with TestClient(app) as c:
        yield c
    llm_config.set_understanding_default(None)


def make(name, model, tools=True):
    return llm_config.create({"model_name": name, "litellm_params": {"model": model, "api_key": "sk-x"}, "capabilities": {"tools": tools, "vision": False}})


def test_understanding_model_role_persists_and_is_listed(client):
    other = make("u", "openai/gpt-6-luna")
    assert client.get("/api/llm-models").json()["understanding_id"] is None
    assert client.put("/api/llm-understanding/default", json={"id": other["id"]}).json() == {"understanding_id": other["id"]}
    assert client.get("/api/llm-models").json()["understanding_id"] == other["id"]
    assert llm_config.understanding_default() == other["id"]
    assert client.put("/api/llm-understanding/default", json={"id": "nope"}).status_code == 400
    assert client.put("/api/llm-understanding/default", json={"id": None}).json() == {"understanding_id": None}
    client.put("/api/llm-understanding/default", json={"id": other["id"]})
    llm_config.delete(other["id"])
    assert client.get("/api/llm-models").json()["understanding_id"] is None


def test_understanding_model_resolution_order(client, monkeypatch):
    import inference
    seen = []

    async def params_for(entry, options):
        seen.append(entry["id"])
        return {"model": entry["litellm_params"]["model"]}
    monkeypatch.setattr(inference, "params_for", params_for)
    monkeypatch.delenv("LDRAW_NOVA_UNDERSTAND_LLM_ID", raising=False)
    for entry in llm_config.list_entries()[0]:
        llm_config.delete(entry["id"])
    assert asyncio.run(understand.model_params()) is None                    # nothing configured: rules only
    main = make("main", "openai/gpt-6-luna")
    assert asyncio.run(understand.model_params()) and seen[-1] == main["id"]  # no Haiku: the main model
    haiku = make("haiku", understand.DEFAULT_MODEL)
    asyncio.run(understand.model_params())
    assert seen[-1] == haiku["id"]                                           # Haiku 5.5 once added
    llm_config.set_understanding_default(main["id"])
    asyncio.run(understand.model_params())
    assert seen[-1] == main["id"]                                            # the Settings choice wins
    asyncio.run(understand.model_params(haiku["id"]))
    assert seen[-1] == haiku["id"]                                           # an explicit id wins over Settings
    for entry in (main, haiku):
        llm_config.delete(entry["id"])


# ------------------------------------------------------------------ endpoints

def test_describe_endpoint_contract(client, provider):
    provider.specs["a green pickup with a white roof"] = {"intent": "new_model", "subject": {"family": "truck", "kind": "pickup"},
                                                          "colours": {"body": "Green", "roof": "White"}}
    body = client.post("/api/describe", json={"text": "a green pickup with a white roof", "build_id": "b1"}).json()
    assert body["route"] == "generator" and body["generator"] == "pickup" and body["params"]["roof"] == "white"
    assert body["build_id"] == "b1" and body["understanding_cost_eur"] > 0 and body["understood_by"] == "llm"
    assert {"notes", "confidence", "cost_eur", "seconds_estimate"} <= set(body)
    assert client.post("/api/describe", json={"text": "  "}).status_code == 422
    assert client.post("/api/describe", json={"text": "make the roof blue", "current": {"generator": "nope", "params": {}}}).status_code == 422
    edit = client.post("/api/describe", json={"text": "make the roof blue", "current": HOUSE}).json()
    assert edit["route"] == "edit" and edit["understanding_cost_eur"] == 0.0
    assert len(provider.calls) == 1


def test_describe_without_any_model_falls_back_to_keywords(client, monkeypatch):
    async def none(llm_id=None):
        return None
    monkeypatch.setattr(understand, "model_params", none)
    body = client.post("/api/describe", json={"text": "I want a yellow pickup"}).json()
    assert body["route"] == "generator" and body["generator"] == "pickup" and body["understood_by"] == "rules_fallback"
    assert body["understanding_cost_eur"] == 0.0 and body["confidence"] <= 0.5 and "keywords" in body["notes"][0]


def test_estimate_endpoint_is_rules_only_and_fast(client, provider):
    cases = {"Build a small red car with four black wheels": ("generator", 1.0), "a red pickup": ("generator", None),
             "Design a harbour crane with a long boom and a cargo dock": ("needs_ai", 1.0), "a steam train": ("needs_ai", None),
             "hello": ("clarify", None)}
    slowest = 0.0
    for text, (route, confidence) in cases.items():
        started = time.perf_counter()
        body = client.get("/api/describe/estimate", params={"text": text}).json()
        slowest = max(slowest, time.perf_counter() - started)
        assert body["route"] == route, text
        assert confidence is None or body["confidence"] == confidence
        assert 0 <= body["confidence"] <= 1
    assert slowest < 0.2
    fast = client.get("/api/describe/estimate", params={"text": "a red pickup"}).json()
    assert fast["hint"].startswith("Fast path: free") and fast["took_ms"] < 200 and fast["confidence"] < 1
    ai = client.get("/api/describe/estimate", params={"text": "a steam train"}).json()
    assert ai["hint"].startswith("AI build: ~€") and ai["estimate"]["minutes_high"] > ai["estimate"]["minutes_low"]
    assert client.get("/api/describe/estimate", params={"text": "make the roof blue", "generator": "house"}).json()["route"] == "edit"
    assert client.get("/api/describe/estimate").json()["route"] == "clarify"
    assert provider.calls == []                                               # the estimate never reaches the model


def test_estimate_without_a_loaded_registry_is_still_instant(client, provider):
    generator_bridge.set_registry(None)
    started = time.perf_counter()
    body = client.get("/api/describe/estimate", params={"text": "a red pickup"}).json()
    assert time.perf_counter() - started < 0.2 and body["route"] == "generator"
    assert client.get("/api/describe/estimate", params={"text": "a steam train"}).json()["route"] == "needs_ai"


def test_registry_dump_matches_the_toolkit_when_available():
    """The fixture is a dump of the real registry; with a toolkit interpreter, check that it is still current."""
    import settings
    if not Path(settings.TOOLKIT_PYTHON).exists():
        pytest.skip("no toolkit interpreter")
    generator_bridge.set_registry(None)
    fresh = asyncio.run(generator_bridge.ensure())
    if fresh is None:
        pytest.skip("toolkit cannot be imported here")
    generator_bridge._cache_path().unlink(missing_ok=True)
    generators = [e for e in fresh["entries"] if e.get("kind") != "template"]      # templates: data, tested below
    assert {e["id"]: e["params"] for e in generators} == {k: v["params"] for k, v in ENTRIES.items()}


def test_a_template_wins_only_a_kind_no_generator_owns():
    """Templates (docs/plans/build-templates) sit next to the generators as "template:<id>"; a generator wins a tie."""
    flatbed = {"id": "template:flatbed-truck", "kind": "template", "title": "Flatbed truck", "families": ["truck"],
               "kinds": ["flatbed truck", "flatbed"], "quality": "Derived from a model", "defaults": {"z_delta": 0},
               "params": {"type": "object", "properties": {
                   "colour_body": {"type": ["string", "integer"], "x-colour": True, "default": 14},
                   "z_delta": {"enum": [0, 2, 4], "default": 0}}}}
    cottage = dict(flatbed, id="template:willowbank-cottage", families=["house", "building"], kinds=["cottage"])
    generator_bridge.set_registry(dict(REGISTRY, entries=REGISTRY["entries"] + [flatbed, cottage]))
    try:
        entry, _ = fast_path.choose({"intent": "new_model", "subject": {"family": "truck", "kind": "flatbed truck"}})
        assert entry["id"] == "template:flatbed-truck"
        entry, _ = fast_path.choose({"intent": "new_model", "subject": {"family": "house", "kind": "cottage"}})
        assert entry["id"] == "house"
        params, notes = fast_path.params_from_spec(flatbed, {"subject": {"family": "truck"}, "colours": {"body": "red"}})
        assert params["colour_body"] == "red" and params["z_delta"] == 0
    finally:
        generator_bridge.set_registry(REGISTRY)


def test_estimate_with_a_generator_loads_a_cold_registry(client, provider, monkeypatch):
    loaded = []

    async def ensure():
        loaded.append(1)
        generator_bridge.set_registry(REGISTRY)
        return REGISTRY

    generator_bridge.set_registry(None)
    monkeypatch.setattr(generator_bridge, "cached", lambda: REGISTRY if loaded else None)
    monkeypatch.setattr(generator_bridge, "ensure", ensure)
    body = client.get("/api/describe/estimate", params={"text": "make the roof blue", "generator": "house"}).json()
    assert body["route"] == "edit" and loaded == [1]
