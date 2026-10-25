"""Prompt regression set for the natural-language fast path (NL wave 3, #43).

tests/nl_prompts.json holds the 54 prompts of docs/plans/active/nl-review/prompts.json plus prompts added for new generators, each with
the route and generator the model-less path (rules, then the keyword reading) chose when it was recorded. The understanding model
is stubbed away, so the run is free and deterministic; what it guards is routing and the generator registry, not model quality.
When a change moves a route on purpose, re-record and review the diff:

    NL_REGRESSION_RECORD=1 pytest tests/test_nl_regression.py

A live run against the cheap model stays a manual step (`python understand_eval.py --live`); it is not part of this set.
"""
import asyncio
import json
import os
from pathlib import Path

import pytest

import fast_path
import generator_bridge
import understand

HERE = Path(__file__).parent
PROMPTS = HERE / "nl_prompts.json"
REGISTRY = json.loads((HERE / "generators_registry.json").read_text(encoding="utf-8"))
ENTRIES = {e["id"]: e for e in REGISTRY["entries"]}
# What the workspace passes for each context: a generator build at its defaults, or nothing.
OPEN_BUILD = {"new": None, "house": "house", "car": "grand-tourer", "nongen": None, "tree": "tree", "train": "toy-train", "ship": "cargo-ship",
              "chair": "chair", "table": "dining-table", "bed": "bed", "shelf": "bookshelf", "sofa": "sofa"}
ROWS = json.loads(PROMPTS.read_text(encoding="utf-8"))["prompts"]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    generator_bridge.set_registry(REGISTRY)
    monkeypatch.setattr(fast_path, "history_stats", lambda force=False: None)

    async def no_model(llm_id=None):
        return None
    monkeypatch.setattr(understand, "model_params", no_model)


def decide(row):
    generator = OPEN_BUILD[row["ctx"]]
    current = {"generator": generator, "params": dict(ENTRIES[generator]["defaults"])} if generator else None
    result = asyncio.run(fast_path.describe(row["text"], current=current))
    return {"route": result["route"], "generator": result.get("generator")}


def test_prompt_set_is_well_formed():
    ids = [r["id"] for r in ROWS]
    assert len(ids) == len(set(ids)) and len(ids) >= 54
    assert {r["ctx"] for r in ROWS} <= set(OPEN_BUILD)
    assert all(r["expect"]["route"] in ("generator", "edit", "needs_ai", "clarify") for r in ROWS)


@pytest.mark.parametrize("row", ROWS, ids=[r["id"] for r in ROWS])
def test_prompt_routes_as_recorded(row):
    got = decide(row)
    if os.environ.get("NL_REGRESSION_RECORD") == "1":
        row["expect"] = got
        PROMPTS.write_text(json.dumps({"note": json.loads(PROMPTS.read_text(encoding="utf-8"))["note"], "prompts": ROWS},
                                      indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        return
    assert got == row["expect"], row["text"]


def test_every_generator_is_reached_by_some_prompt():
    reached = {r["expect"].get("generator") for r in ROWS}
    for gid in ("building", "grand-tourer", "pickup", "starfighter", "touring-motorcycle", "tree", "toy-train", "cargo-ship",
                "stool", "chair", "dining-table", "bed", "sofa", "bookshelf"):
        assert gid in reached, gid


def test_coverage_share_does_not_drop():
    """The share of new-model prompts that a generator answers; raise the floor when coverage grows."""
    new = [r for r in ROWS if r["ctx"] == "new" and r["cat"] in ("new", "coverage")]
    answered = [r for r in new if r["expect"]["route"] == "generator"]
    assert len(answered) / len(new) >= 0.7
