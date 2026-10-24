"""Resident pipeline: stable cache keys, CLI-identical output, cache hits, worker protocol."""
import io
import json

import pytest

from ldraw_tools import pipeline
from ldraw_tools.builder import build_plan, load_plan
from ldraw_tools.common import dumps
from ldraw_tools.vehicles import vehicle_plan

OPTIONS = dict(geometry=True, glb=True, renders=[])


def test_cache_key_ignores_key_order_and_whitespace():
    a = {"name": "x", "sections": [{"id": "a", "at": [0, 0, 0]}]}
    b = json.loads('{"sections": [{"at": [0, 0, 0],   "id": "a"}], "name": "x"}')
    assert pipeline.cache_key(a, "v1", OPTIONS, "/lib") == pipeline.cache_key(b, "v1", OPTIONS, "/lib")


def test_cache_key_is_pinned():
    # A change here silently drops every user's cached builds: make it deliberate.
    assert pipeline.cache_key({"a": 1}, "v1", {"geometry": True}, "/lib") == \
        "ee0398006c98199442d5395914701e8d566bf3da2450b2be66f61754fe30b942"


def test_cache_key_changes_with_plan_version_options_and_library():
    base = pipeline.cache_key({"a": 1}, "v1", OPTIONS, "/lib")
    assert pipeline.cache_key({"a": 2}, "v1", OPTIONS, "/lib") != base
    assert pipeline.cache_key({"a": 1}, "v2", OPTIONS, "/lib") != base
    assert pipeline.cache_key({"a": 1}, "v1", {**OPTIONS, "glb": False}, "/lib") != base
    assert pipeline.cache_key({"a": 1}, "v1", OPTIONS, "/other") != base


def test_toolkit_version_is_stable_within_a_process():
    assert pipeline.toolkit_version() == pipeline.toolkit_version()


def test_option_normalisation_ignores_view_order():
    assert pipeline._normalise_options(True, False, ["top", "home", "top"]) == pipeline._normalise_options(True, False, ("home", "top"))


def test_serve_survives_bad_requests_and_answers_ping():
    lines = io.StringIO('not json\n{"id": 7, "op": "nope"}\n{"id": 8, "op": "ping"}\n')
    out = io.StringIO()
    assert pipeline.serve(lines, out) == 0
    replies = [json.loads(line) for line in out.getvalue().splitlines()]
    assert replies[0]["ok"] is False and replies[0]["id"] is None
    assert replies[1]["ok"] is False and replies[1]["id"] == 7
    assert replies[2]["ok"] is True and replies[2]["id"] == 8 and replies[2]["pong"]


def test_build_matches_the_cli_path_and_second_call_is_a_cache_hit(official, tmp_path):
    plan = vehicle_plan("grand-tourer")
    reference = tmp_path / "reference"
    reference.mkdir()
    (reference / "plan.json").write_text(dumps(plan) + "\n")
    expected, _, _ = build_plan(load_plan(reference / "plan.json"), official)

    runner = pipeline.Pipeline()
    out = tmp_path / "out"
    first = runner.run(out, vehicle="grand-tourer", name="car", geometry=False, glb=False)
    assert first["ok"] and not first["cached"]
    assert (out / "car.mpd").read_bytes() == expected.encode()
    second = runner.run(out, vehicle="grand-tourer", name="car", geometry=False, glb=False)
    assert second["cached"] and second["key"] == first["key"] and second["seconds"] < 1
    (out / "car.mpd").unlink()
    assert runner.run(out, vehicle="grand-tourer", name="car", geometry=False, glb=False)["cached"]
    assert (out / "car.mpd").read_bytes() == expected.encode()                 # restored from the cache entry


def test_rejects_plans_with_includes(tmp_path):
    runner = pipeline.Pipeline()
    runner.library = tmp_path
    with pytest.raises(ValueError):
        runner.run(tmp_path, plan={"includes": ["x.json"]}, glb=False)
