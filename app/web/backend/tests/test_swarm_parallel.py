# ruff: noqa: F811
"""Brief 06: parallel workers, failure statuses, the app's validation gate with one repair round, Stop."""
import asyncio
import contextlib
import json
import re
import time

import litellm
import pytest
from litellm.types.utils import ChatCompletionDeltaToolCall, Function

import agent
import run_state
import sandbox
import settings
import subagents
import usage
import worker_isolation
from test_agent import chunk, tool_call_chunks
from test_subagents import GOOD_LDR, _planner_entry, _run_planner, _strip_marks, delegate, worker  # noqa: F401, F811 - fixture

BAD_LDR = "0 bad\n1 4 0 0 0 1 0 0 0 1 0 0 0 1 nosuchpart999.dat\n"
SPLIT_LDR = "0 split\n1 4 0 0 0 1 0 0 0 1 0 0 0 1 3001.dat\n1 4 4000 0 0 1 0 0 0 1 0 0 0 1 3001.dat\n"


def write_call(wid, content, name="a.ldr", call_id="w"):
    return tool_call_chunks(call_id, "write_file", {"path": f"output/workers/{wid}/{name}", "content": content})


class Fake:
    """A scripted provider keyed by worker: script(wid, n, kwargs) gives the chunks of that worker's n-th call."""

    def __init__(self, script, delay=0.0):
        self.script = script
        self.delay = delay if callable(delay) else (lambda wid, n, kw: delay)       # seconds, by (worker, call number, request)
        self.calls, self.now, self.peak, self.requests = {}, 0, 0, []

    async def __call__(self, **kwargs):
        system = kwargs["messages"][0]["content"]
        system = system if isinstance(system, str) else system[0]["text"]
        found = re.search(r"output/workers/(\w+)/", system)
        wid = found.group(1) if found else "planner"
        n = self.calls[wid] = self.calls.get(wid, 0) + 1
        self.requests.append((wid, n, kwargs))
        self.now += 1
        self.peak = max(self.peak, self.now)
        try:
            await asyncio.sleep(self.delay(wid, n, kwargs))
            chunks = self.script(wid, n, kwargs)
        finally:
            self.now -= 1

        async def stream():
            for c in chunks:
                yield c
        return stream()


def two_calls(calls):
    """One assistant step with several parallel tool calls: [(id, name, arguments)]."""
    out = []
    for index, (call_id, name, args) in enumerate(calls):
        out += [chunk(tool_calls=[ChatCompletionDeltaToolCall(index=index, id=call_id, type="function",
                                                              function=Function(name=name, arguments=""))]),
                chunk(tool_calls=[ChatCompletionDeltaToolCall(index=index, function=Function(arguments=json.dumps(args)))])]
    return out


def ask(ctx, **args):
    return subagents.delegate(ctx, **args)


# --- settings ---

def test_worker_limits_in_settings(monkeypatch):
    monkeypatch.delenv("LDRAW_NOVA_MAX_WORKERS", raising=False)
    monkeypatch.delenv("LDRAW_NOVA_MAX_DELEGATIONS", raising=False)
    assert settings.max_workers() == 3 and settings.max_delegations() == 8
    monkeypatch.setenv("LDRAW_NOVA_MAX_WORKERS", "99")
    assert settings.max_workers() == 6
    monkeypatch.setenv("LDRAW_NOVA_MAX_WORKERS", "0")
    assert settings.max_workers() == 1


# --- parallelism ---

def test_three_workers_run_at_once_and_a_fourth_waits(monkeypatch, worker, ctx):
    monkeypatch.setenv("LDRAW_NOVA_MAX_WORKERS", "3")
    ctx.options = {"permissions": "full"}
    fake = Fake(lambda wid, n, kw: [chunk(content="done")], delay=0.5)
    monkeypatch.setattr(litellm, "acompletion", fake)

    async def go():
        return await asyncio.gather(*[ask(ctx, role="researcher", task=f"t{i}") for i in range(4)])
    start = time.monotonic()
    results = asyncio.run(go())
    wall = time.monotonic() - start
    print(f"parallel wall time for 4 workers of 0.5 s (cap 3): {wall:.2f} s")
    assert fake.peak == 3                                       # three at once, the fourth waited for a free slot
    assert 0.95 < wall < 1.9                                    # two rounds; sequential would be 2.0 s or more
    assert all("status ok" in r.content for r in results)
    assert ctx.limits.delegations == 4


def test_delegation_cap_per_turn(monkeypatch, worker, ctx):
    monkeypatch.setenv("LDRAW_NOVA_MAX_DELEGATIONS", "2")
    ctx.options = {"permissions": "full"}
    monkeypatch.setattr(litellm, "acompletion", Fake(lambda wid, n, kw: [chunk(content="done")]))
    with pytest.raises(subagents.ToolError):                    # a rejected contract does not use up the cap
        asyncio.run(ask(ctx, role="boss", task="x"))
    for _ in range(2):
        assert "status ok" in asyncio.run(ask(ctx, role="researcher", task="x")).content
    with pytest.raises(subagents.ToolError, match="delegation cap reached: at most 2"):
        asyncio.run(ask(ctx, role="researcher", task="x"))


def test_planner_step_runs_delegates_in_parallel_and_saves_in_call_order(monkeypatch, worker, store):
    monkeypatch.setenv("LDRAW_NOVA_MAX_WORKERS", "3")
    planner = _planner_entry()

    def script(wid, n, kw):
        if wid == "planner":
            return (two_calls([("p1", "delegate", {"role": "researcher", "task": "slow one"}),
                               ("p2", "delegate", {"role": "researcher", "task": "fast one"}),
                               ("p3", "list_files", {})]) if n == 1 else [chunk(content="Planner done.")])
        slow = "slow one" in kw["messages"][1]["content"]
        return [chunk(content="slow worker finished" if slow else "fast worker finished")]

    def delay(wid, n, kw):
        text = kw["messages"][1]["content"] if wid != "planner" else ""
        return 0.6 if "slow one" in text else 0.1 if "fast one" in text else 0
    fake = Fake(script, delay)
    monkeypatch.setattr(litellm, "acompletion", fake)
    start = time.monotonic()
    chat_id = _run_planner(store, planner)
    print(f"planner step with two parallel delegates (0.6 s and 0.1 s): {time.monotonic() - start:.2f} s")
    messages = store.messages(chat_id)
    tools_saved = [m for m in messages if m["role"] == "tool"]
    assert [m["tool_call_id"] for m in tools_saved] == ["p1", "p2", "p3"]       # call order, not completion order
    assert "slow worker finished" in tools_saved[0]["content"] and "fast worker finished" in tools_saved[1]["content"]
    assert tools_saved[0]["_worker"]["id"] != tools_saved[1]["_worker"]["id"]
    assert fake.peak == 2                                                      # list_files is not run alongside them
    planner_calls = [r for r in fake.requests if r[0] == "planner"]
    assert len(planner_calls) == 2
    first, second = (_strip_marks(r[2]["messages"]) for r in planner_calls)    # the planner's cached prefix is untouched
    assert json.dumps(first[0]) == json.dumps(second[0]) and json.dumps(first[1:]) == json.dumps(second[1:len(first)])
    assert [m["role"] for m in second[len(first):]] == ["assistant", "tool", "tool", "tool"]
    assert usage.usage_totals(messages)["steps"] == 4                          # 2 planner steps + 2 worker records


def test_stop_cancels_all_workers_and_records_their_cost(monkeypatch, worker, store):
    planner = _planner_entry()
    state = {}

    def script(wid, n, kw):
        if wid == "planner":
            return two_calls([("p1", "delegate", {"role": "researcher", "task": "a"}),
                              ("p2", "delegate", {"role": "researcher", "task": "b"})])
        return tool_call_chunks(f"c{n}", "list_files", {}) if n == 1 else [chunk(content="never")]

    fake = Fake(script, lambda wid, n, kw: 0 if wid == "planner" else 0.05 if n == 1 else 30)
    monkeypatch.setattr(litellm, "acompletion", fake)
    import os
    os.chmod(settings.DATA_DIR.parent, 0o755)
    chat = store.create_chat()

    async def go():
        await agent.start_turn(store, chat["id"], "build", planner["id"], {"permissions": "full"})
        run = run_state._runs[chat["id"]]
        for _ in range(300):
            if sum(1 for r in fake.requests if r[0] != "planner" and r[1] == 2) >= 2:
                break
            await asyncio.sleep(0.02)
        state["stuck_in_flight"] = fake.now
        run.task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await run.task
    asyncio.run(go())
    messages = store.messages(chat["id"])
    cancelled = [m for m in messages if (m.get("_worker") or {}).get("status") == "cancelled"]
    assert state["stuck_in_flight"] == 2 and len(cancelled) == 2                 # both workers were mid-call when Stop came
    assert all(m["_usage"]["cost_usd"] > 0 and m.get("_hidden") for m in cancelled)
    assert usage.usage_totals(messages)["worker_cost_eur"] > 0
    assert any(m.get("content") == "Stopped." for m in messages)
    assert not [m for m in messages if m["role"] == "tool"]                     # no result was saved for the interrupted calls


# --- stray-write guard with concurrent workers ---

def test_stray_write_is_undone_once_with_concurrent_workers(monkeypatch, worker, ctx):
    ctx.options = {"permissions": "full"}
    ctx.work_dir.mkdir(parents=True, exist_ok=True)
    plan = ctx.work_dir / "plan.ldr"
    plan.write_text("original")
    sandbox.give_to_agent(plan)
    hijack = f"open({str(plan)!r},'w').write('hijacked')"

    def script(wid, n, kw):
        if "task A" in kw["messages"][1]["content"]:
            return tool_call_chunks("a1", "run_python", {"code": hijack}) if n == 1 else [chunk(content="A done")]
        return [chunk(content="B done")]

    def delay(wid, n, kw):
        return (0.05 if n == 1 else 0.6) if "task A" in kw["messages"][1]["content"] else 1.0
    monkeypatch.setattr(litellm, "acompletion", Fake(script, delay))

    async def go():
        async def late_b():
            await asyncio.sleep(0.3)                         # B starts after A's stray write, before A ends
            return await ask(ctx, role="researcher", task="task B")
        a = asyncio.create_task(ask(ctx, role="builder", task="task A"))
        b = asyncio.create_task(late_b())
        return await asyncio.gather(a, b)
    a, b = asyncio.run(go())
    assert plan.read_text() == "original"                    # never left hijacked, never "restored" to the hijacked content
    reports = [r for r in (a, b) if "Stray writes outside" in r.content]
    assert len(reports) == 1 and "plan.ldr (modified, restored)" in reports[0].content


def test_guard_shares_one_baseline(tmp_path):
    out = tmp_path / "output"
    out.mkdir()
    (out / "plan.ldr").write_text("original")
    guard = worker_isolation.Guard()
    guard.enter(out)
    (out / "plan.ldr").write_text("hijacked")
    guard.enter(out)                                         # a second worker joins: the baseline is not retaken
    assert guard.check() == ["plan.ldr (modified, restored)"]
    assert guard.check() == [] and (out / "plan.ldr").read_text() == "original"
    guard.leave()
    guard.leave()
    (out / "plan.ldr").write_text("planner edit")            # no worker running: nothing is guarded or restored
    assert guard.check() == [] and (out / "plan.ldr").read_text() == "planner edit"


# --- failure modes ---

def test_identical_calls_with_identical_results_are_stuck(monkeypatch, worker, ctx):
    fake = Fake(lambda wid, n, kw: tool_call_chunks(f"c{n}", "list_files", {}))
    monkeypatch.setattr(litellm, "acompletion", fake)
    result = delegate(ctx, role="researcher", task="look", max_steps=20)
    assert "status stuck" in result.content and "same list_files call" in result.content
    assert len(fake.requests) == 3 and result.record["_worker"]["status"] == "stuck"


def test_no_output_written_in_eight_steps_is_stuck(monkeypatch, worker, ctx):
    fake = Fake(lambda wid, n, kw: tool_call_chunks(f"c{n}", "run_python", {"code": f"print({n})"}))
    monkeypatch.setattr(litellm, "acompletion", fake)
    result = delegate(ctx, role="builder", task="build", outputs=["a.ldr"], max_steps=30)
    assert "status stuck" in result.content and "no output file in 8" in result.content
    assert len(fake.requests) == 8 and "Missing outputs: a.ldr" in result.content
    assert "Validation: not run" in result.content


def test_step_cap_keeps_partial_outputs(monkeypatch, worker, ctx):
    def script(wid, n, kw):
        return write_call(wid, GOOD_LDR, "a.ldr", f"c{n}") if n == 1 else tool_call_chunks(f"c{n}", "run_python", {"code": f"print({n})"})
    monkeypatch.setattr(litellm, "acompletion", Fake(script))
    result = delegate(ctx, role="builder", task="build", outputs=["a.ldr", "b.ldr"], max_steps=3)
    assert "status step_cap" in result.content and "a.ldr" in result.content.split("Files:")[1]


def test_empty_output_fails_without_repair(monkeypatch, worker, ctx):
    fake = Fake(lambda wid, n, kw: [chunk(content="I think it is fine.")])
    monkeypatch.setattr(litellm, "acompletion", fake)
    result = delegate(ctx, role="builder", task="build", outputs=["a.ldr"])
    assert "status failed" in result.content and "Files: none" in result.content and len(fake.requests) == 1


def test_invalid_ldraw_is_fixed_by_the_repair_round(monkeypatch, worker, ctx):
    def script(wid, n, kw):
        if n == 1:
            return write_call(wid, BAD_LDR)
        if n == 2:
            return [chunk(content="written")]
        return write_call(wid, GOOD_LDR) if n == 3 else [chunk(content="fixed")]
    fake = Fake(script)
    monkeypatch.setattr(litellm, "acompletion", fake)
    result = delegate(ctx, role="builder", task="build", outputs=["a.ldr"])
    assert "status ok" in result.content and "Validation: ok" in result.content and "after one automatic repair round" in result.content
    repair = [m for m in fake.requests[2][2]["messages"] if m["role"] == "user"][-1]["content"]
    assert "found problems" in repair and "a.ldr" in repair and "nosuchpart999" in repair      # the validator's text
    assert len(fake.requests) == 4


def test_invalid_ldraw_not_fixed_is_partial_with_errors(monkeypatch, worker, ctx):
    fake = Fake(lambda wid, n, kw: write_call(wid, BAD_LDR, call_id=f"c{n}") if n == 1 else [chunk(content="done")])
    monkeypatch.setattr(litellm, "acompletion", fake)
    result = delegate(ctx, role="builder", task="build", outputs=["a.ldr"])
    assert "status partial" in result.content and "Validation: NOT OK after one automatic repair round" in result.content
    assert "nosuchpart999" in result.content and "Do not place this submodel" in result.content
    assert len(fake.requests) == 3                                       # one repair round, no second one


def test_repair_round_is_capped_at_eight_steps(monkeypatch, worker, ctx):
    def script(wid, n, kw):
        if n == 1:
            return write_call(wid, BAD_LDR)
        return [chunk(content="written")] if n == 2 else tool_call_chunks(f"c{n}", "run_python", {"code": f"print({n})"})
    fake = Fake(script)
    monkeypatch.setattr(litellm, "acompletion", fake)
    result = delegate(ctx, role="builder", task="build", outputs=["a.ldr"], max_steps=20)
    assert len(fake.requests) == 2 + subagents.REPAIR_STEPS
    assert "status partial" in result.content and "Validation: NOT OK" in result.content


def test_disconnected_builder_output_is_a_failure_and_repairable(monkeypatch, worker, ctx):
    def script(wid, n, kw):
        if n == 1:
            return write_call(wid, SPLIT_LDR)
        if n == 2:
            return [chunk(content="done")]
        return write_call(wid, GOOD_LDR) if n == 3 else [chunk(content="joined")]
    fake = Fake(script)
    monkeypatch.setattr(litellm, "acompletion", fake)
    result = delegate(ctx, role="builder", task="build", outputs=["a.ldr"])
    repair = [m for m in fake.requests[2][2]["messages"] if m["role"] == "user"][-1]["content"]
    assert "2 disconnected groups" in repair
    assert "status ok" in result.content and "after one automatic repair round" in result.content


def test_disconnected_after_repair_is_partial(monkeypatch, worker, ctx):
    fake = Fake(lambda wid, n, kw: write_call(wid, SPLIT_LDR, call_id=f"c{n}") if n in (1, 3) else [chunk(content="done")])
    monkeypatch.setattr(litellm, "acompletion", fake)
    result = delegate(ctx, role="builder", task="build", outputs=["a.ldr"])
    assert "status partial" in result.content and "2 disconnected groups" in result.content


def test_researcher_output_is_not_held_to_connectivity(monkeypatch, worker, ctx):
    fake = Fake(lambda wid, n, kw: write_call(wid, SPLIT_LDR, call_id="c1") if n == 1 else [chunk(content="done")])
    monkeypatch.setattr(litellm, "acompletion", fake)
    result = delegate(ctx, role="researcher", task="notes", outputs=["a.ldr"])
    assert "status ok" in result.content and "disconnected" not in result.content


def test_bad_json_plan_output_fails_validation(monkeypatch, worker, ctx):
    fake = Fake(lambda wid, n, kw: write_call(wid, "{not json", "plan.json", "c1") if n == 1 else [chunk(content="done")])
    monkeypatch.setattr(litellm, "acompletion", fake)
    result = delegate(ctx, role="builder", task="plan", outputs=["plan.json"])
    assert "status partial" in result.content and "plan.json: not valid JSON" in result.content


def test_provider_exception_and_timeout_are_failed_with_class_only(monkeypatch, worker, ctx):
    async def timeout(**_kwargs):
        raise asyncio.TimeoutError("sk-secret-token took too long")
    monkeypatch.setattr(litellm, "acompletion", timeout)
    result = delegate(ctx, role="builder", task="build", outputs=["a.ldr"])
    assert "status failed" in result.content and "TimeoutError" in result.content and "sk-secret" not in result.content
    assert result.record["_worker"]["status"] == "failed"
