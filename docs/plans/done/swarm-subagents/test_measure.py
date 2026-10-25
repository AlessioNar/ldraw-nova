"""Offline checks of measure.py: dry run, the hard cap, classification, and agreement with the app's pricing code.

  python -m pytest docs/plans/done/swarm-subagents/test_measure.py -q          # host: everything except the app-pricing check
  (inside the app image with the backend mounted, the app-pricing check runs too)
"""
import asyncio
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import measure  # noqa: E402


def fake_response(prompt, completion, read=0, write=0, calls=()):
    tool_calls = [SimpleNamespace(id=f"c{i}", function=SimpleNamespace(name=n, arguments=a)) for i, (n, a) in enumerate(calls)]
    return SimpleNamespace(usage=SimpleNamespace(prompt_tokens=prompt, completion_tokens=completion,
                                                 cache_read_input_tokens=read, cache_creation_input_tokens=write),
                           choices=[SimpleNamespace(message=SimpleNamespace(content="done" if not calls else None, tool_calls=tool_calls))])


ROW = measure.page_row("anthropic/claude-haiku-5-5")
TASK = measure.TASKS[0]


def run(responses, cap_eur, rate=0.92, max_steps=20, validate_results=None):
    """Drive run_task with scripted responses; returns (result, budget, number of completion calls)."""
    seen = {"calls": 0}
    budget = measure.Budget(cap_eur, rate)
    verdicts = iter(validate_results or [])

    async def complete(messages, schemas):
        seen["calls"] += 1
        return responses[min(seen["calls"] - 1, len(responses) - 1)]

    async def dispatch(name, raw):
        return "ok"

    async def validate(path):
        return next(verdicts, None)

    result = asyncio.run(measure.run_task(TASK, complete=complete, dispatch=dispatch, validate=validate,
                                          cost_fn=lambda rec: measure.local_step_cost(rec, ROW), row=ROW, budget=budget,
                                          schemas=[measure.TOOLS_PLACEHOLDER_SCHEMA], max_steps=max_steps))
    return result, budget, seen["calls"]


def test_dry_run_prints_plan_and_sends_nothing(capsys, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert measure.main(["capability"]) == 0
    out = capsys.readouterr().out
    assert out.count("--- task ") == 5 and "DRY RUN" in out and "Hard total cap: 1.00 EUR" in out and "Worst case" in out
    assert "anthropic/claude-haiku-5-5" in out


def test_live_needs_a_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(SystemExit, match="ANTHROPIC_API_KEY"):
        measure.main(["capability", "--live"])


def test_cap_stops_before_the_call_that_could_pass_it():
    # each step bills 60k fresh prompt tokens + 4k output = about $0.0082; a 0.01 EUR cap allows one call at most
    heavy = fake_response(60_000, 4_000, calls=[("write_file", "{}")])
    result, budget, calls = run([heavy], cap_eur=0.01)
    assert result.stop == "cap_eur" and "cap 0.01 EUR" in result.error
    assert budget.spent_eur <= 0.01 and calls <= 1


def test_cap_is_never_exceeded_over_many_steps():
    heavy = fake_response(20_000, 3_000, calls=[("write_file", "{}")])
    result, budget, calls = run([heavy], cap_eur=0.05)
    assert result.stop == "cap_eur"
    assert budget.spent_eur <= 0.05
    assert result.steps == calls and 0 < calls < 20


def test_step_cap_and_finish_and_cache_flag():
    loop = fake_response(5_000, 100, read=4_000, write=500, calls=[("run_toolkit", '{"arguments": ["validate", "x"]}')])
    result, _b, calls = run([loop], cap_eur=10, max_steps=3)
    assert result.stop == "step_cap" and calls == 3 and result.prefix_cached and result.self_validations == 3
    finish = [fake_response(5_000, 100, write=5_000, calls=[("write_file", "{}")]), fake_response(5_200, 50, read=5_000, write=200)]
    result, _b, _c = run(finish, cap_eur=10, validate_results=[False, True, True])
    summary = result.summary(0.92)
    assert result.stop == "finished" and summary["valid_first_try"] is False and summary["repair_rounds"] == 1 and summary["valid_final"]
    assert summary["prefix_cached_from_step_2"] is True and summary["cache_read_total"] == 5_000


def test_unpriceable_step_stops_the_run():
    budget = measure.Budget(1.0, 0.92)

    async def complete(messages, schemas):
        return fake_response(100, 10)

    async def noop(*_a):
        return "ok"

    result = asyncio.run(measure.run_task(TASK, complete=complete, dispatch=noop, validate=noop, cost_fn=lambda rec: None, row=ROW,
                                          budget=budget, schemas=[]))
    assert result.stop == "error"


def test_classification():
    c = measure.classify_step
    assert c([("my_parts", {})]) == "research"
    assert c([("run_toolkit", {"arguments": ["discover", "search", "x"]})]) == "research"
    assert c([("run_toolkit", {"arguments": ["validate", "a.mpd"]}), ("view_image", {})]) == "validation"
    assert c([("write_file", {"path": "output/a.plan.json"}), ("run_toolkit", {"arguments": ["validate", "a"]})]) == "building"
    assert c([("run_shell", {"command": "./ldraw-agent build p.json && ./ldraw-agent inventory check"})]) == "building"
    assert c([("run_shell", {"command": "./ldraw-agent validate a.mpd --geometry"})]) == "validation"
    assert c([("run_shell", {"command": "grep -rn cache ldraw_tools | head"})]) == "research"
    assert c([]) == "other"


def test_parse_validate():
    ok = '[exit code 0, 1s]\n--- stdout ---\n{"checks_passed": true, "diagnostics": [{"severity": "warning"}]}'
    bad = '--- stdout ---\n{"checks_passed": true, "diagnostics": [{"severity": "error"}]}'
    assert measure.parse_validate(ok) is True and measure.parse_validate(bad) is False and measure.parse_validate("Error: nope") is False


def test_local_cost_matches_the_app():
    """Needs the backend (inside the app image); skipped on a bare host."""
    sys.path[:0] = [str(HERE.parents[3] / "app" / "web" / "backend"), "/app"]
    try:
        import model_catalog
        import usage
    except ImportError:
        pytest.skip("app modules not importable here")
    for model in ("anthropic/claude-haiku-5-5", "anthropic/claude-opus-5-5"):
        for rec in ({"prompt": 150_000, "completion": 900, "cache_read": 100_000, "cache_write": 20_000},
                    {"prompt": 30_000, "completion": 700, "cache_read": 0, "cache_write": 30_000}):
            app = usage.step_cost_usd(rec, model_catalog.profile(model)["pricing"], model, "1h")
            assert abs(app - measure.local_step_cost(rec, measure.page_row(model))) < 1e-9


def test_script_runs_as_a_program():
    out = subprocess.run([sys.executable, str(HERE / "measure.py"), "capability"], capture_output=True, text=True, check=True)
    assert "DRY RUN" in out.stdout
