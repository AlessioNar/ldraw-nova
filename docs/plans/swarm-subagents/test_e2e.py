"""Tests for e2e.py: no app import, no network, no key. Run: python -m pytest docs/plans/swarm-subagents/test_e2e.py"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import e2e  # noqa: E402


def test_default_dry_run_is_one_run_under_three_eur(capsys):
    assert e2e.main([]) == 0
    out = capsys.readouterr().out
    assert "DRY RUN" in out and "spaceship" in out
    assert "small-sonnet+haiku-1" in out and out.count("| small-") == 1
    assert "Worst case if every run hits its budget: 2.50 EUR" in out
    assert e2e.DEFAULT_TOTAL_CAP_EUR == 3.0 and e2e.DEFAULT_BUDGET_EUR == 2.5


def test_full_matrix_dry_run_reports_worst_case_and_needs_approval(capsys):
    e2e.main(["--configs", ",".join(e2e.CONFIGS), "--prompts", ",".join(e2e.PROMPTS), "--budget-eur", "4", "--total-cap-eur", "48"])
    out = capsys.readouterr().out
    assert "Runs planned: 12" in out and "48.00 EUR" in out


def test_default_cap_blocks_extra_runs_and_says_so(capsys):
    e2e.main(["--configs", "sonnet,opus"])
    out = capsys.readouterr().out
    assert "at most 1 of them" in out and "owner's approval" in out


def test_live_without_key_refuses(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    try:
        e2e.main(["--live"])
    except SystemExit as exc:
        assert "ANTHROPIC_API_KEY" in str(exc)
    else:
        raise AssertionError("expected SystemExit")


def test_total_cap_skips_run_that_could_pass_it():
    runs = e2e.plan_runs(["sonnet", "opus"], ["small"], 2)
    calls = []

    def runner(run):
        calls.append(run["run_id"])
        return {**run, "status": "ok", "eur_total": 1.2}

    results = e2e.execute_plan(runs, runner, 1.5, 3.0)
    assert calls == ["small-sonnet-1", "small-sonnet-2"]   # 2.4 spent after two; 2.4 + 1.5 > 3.0 blocks the rest
    assert [r["status"] for r in results] == ["ok", "ok", "skipped_total_cap", "skipped_total_cap"]


def test_crashed_run_is_charged_its_worst_case():
    runs = e2e.plan_runs(["sonnet"], ["small"], 3)

    def runner(run):
        raise RuntimeError("boom")

    results = e2e.execute_plan(runs, runner, 1.5, 3.0)
    assert [r["status"] for r in results] == ["error", "error", "skipped_total_cap"]


def _m(role, t, **kw):
    return {"role": role, "created_at": t, **kw}


def test_timing_split():
    msgs = [
        _m("user", 0.0, content="go"),
        _m("assistant", 10.0, _usage={"completion": 5}, tool_calls=[]),            # model 10 s
        _m("tool", 14.0, name="run_shell"),
        _m("tool", 16.0, name="run_shell"),                                         # tool 6 s
        _m("user", 16.5, _hidden=True),
        _m("assistant", 21.5, _usage={"completion": 5}),                            # model 5 s, no tool
    ]
    assert e2e.timing_split(msgs) == {"model_s": 15.0, "tool_s": 6.0, "steps": 2}


def test_delegation_summary_counts_statuses_and_repairs():
    def call(cid, name, args):
        return {"id": cid, "function": {"name": name, "arguments": json.dumps(args)}}
    msgs = [
        _m("assistant", 1, tool_calls=[call("a", "delegate", {"role": "builder"}), call("b", "delegate", {"role": "researcher"})]),
        _m("tool", 2, name="delegate", tool_call_id="a", content="Worker x: status partial\n", _worker={"steps": 4}),
        _m("tool", 3, name="delegate", tool_call_id="b", content="Worker y: status ok\n", _worker={"steps": 2}),
        _m("assistant", 4, tool_calls=[call("c", "delegate", {"role": "builder"})]),
        _m("tool", 5, name="delegate", tool_call_id="c", content="Worker z: status ok\n"),
        _m("tool", 6, name="publish_model", content=json.dumps({"checks_passed": False})),
        _m("tool", 7, name="publish_model", content=json.dumps({"checks_passed": True})),
    ]
    summary = e2e.delegation_summary(msgs)
    assert summary["delegations"] == 3 and summary["statuses"] == {"partial": 1, "ok": 2}
    assert summary["repair_rounds"] == 2 and summary["validate_passed"] is True


def test_redact_and_report():
    assert e2e.redact("x sk-abc y", ["sk-abc"]) == "x [key] y"
    row = {"prompt": "small", "config": "sonnet+haiku", "status": "ok", "eur_total": 1.0, "eur_planner": 0.8, "eur_workers": 0.2,
           "steps": 10, "delegations": 0, "published": True}
    assert "| small | sonnet+haiku | ok | 1.000 | 0.800 | 0.200 | 10 |" in e2e.report_text([row])
