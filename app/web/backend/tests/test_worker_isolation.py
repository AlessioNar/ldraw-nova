"""Brief 05: a worker writes only in its own folder, never exceeds the chat's mode, never asks the user."""
import asyncio
import re

import litellm
import pytest

import agent
import sandbox
import subagents
import tool_policy
import tools
import worker_isolation
from run_state import Run
from test_agent import chunk, scripted, tool_call_chunks
from test_subagents import delegate, worker  # noqa: F401 - fixture


def wctx(ctx):
    root = ctx.work_dir / "workers" / "w1"
    root.mkdir(parents=True)
    return subagents.worker_context(ctx, root)


def write(ctx, path, content="x"):
    return asyncio.run(tools.dispatch(ctx, "write_file", {"path": path, "content": content}))


def test_worker_write_file_confined_to_its_folder(ctx):
    w = wctx(ctx)
    assert w.write_root == ctx.work_dir / "workers" / "w1" and ctx.write_root is None
    assert "Wrote" in write(w, "output/workers/w1/a.ldr").content
    assert "Wrote" in write(w, "b.ldr").content                       # relative = inside its own folder
    assert (w.write_root / "b.ldr").exists()
    for bad in ("../x", "output/x.txt", "output/workers/w2/a.ldr", "output/workers/x", "../../x", "/etc/x",
                str(ctx.work_dir / "planner.txt"), "output/workers/w1/../../y"):
        assert write(w, bad).content.startswith("Error:"), bad
    assert not (ctx.work_dir / "x.txt").exists() and not (ctx.work_dir / "planner.txt").exists()


def test_worker_symlink_cannot_escape(ctx):
    w = wctx(ctx)
    (w.write_root / "link").symlink_to(ctx.work_dir)
    assert write(w, "link/evil.txt").content.startswith("Error:")
    assert not (ctx.work_dir / "evil.txt").exists()


def test_planner_tools_unaffected(ctx):
    assert write(ctx, "output/anywhere.txt").content.startswith("Wrote")
    assert write(ctx, "notes/deep.txt").content.startswith("Wrote")
    assert (ctx.work_dir / "anywhere.txt").exists() and (ctx.work_dir / "notes/deep.txt").exists()
    assert tools.resolve_path(ctx, "plan.ldr", write=True) == ctx.work_dir.resolve() / "plan.ldr"


def test_worker_run_python_cwd_is_its_folder(ctx):
    w = wctx(ctx)
    out = asyncio.run(tools.dispatch(w, "run_python", {"code": "import os\nprint(os.getcwd())\nopen('made.txt','w').write('hi')"}))
    assert str(w.write_root) in out.content
    assert (w.write_root / "made.txt").read_text() == "hi"


def test_stray_write_by_run_python_is_reported_and_undone(monkeypatch, worker, ctx):  # noqa: F811
    ctx.work_dir.mkdir(parents=True, exist_ok=True)
    plan, other = ctx.work_dir / "plan.ldr", ctx.work_dir / "keep.ldr"
    plan.write_text("original")
    other.write_text("keep")
    sandbox.give_to_agent(plan)
    sandbox.give_to_agent(other)
    code = (f"open({str(plan)!r},'w').write('hijacked')\nopen({str(ctx.work_dir / 'new.txt')!r},'w').write('n')\n"
            f"import os\nos.remove({str(other)!r})\nopen('mine.txt','w').write('ok')")
    fake, _calls = scripted([tool_call_chunks("w1", "run_python", {"code": code}), [chunk(content="done")]])
    monkeypatch.setattr(litellm, "acompletion", fake)
    result = delegate(ctx, role="builder", task="build", outputs=["mine.txt"])
    assert plan.read_text() == "original" and other.read_text() == "keep" and not (ctx.work_dir / "new.txt").exists()
    assert "Stray writes outside" in result.content
    for name in ("plan.ldr (modified, restored)", "keep.ldr (deleted, restored)", "new.txt (new, deleted)"):
        assert name in result.content
    assert re.search(r"output/workers/\w+/mine\.txt", result.content)           # its own file survives


def test_clean_worker_has_no_stray_line(monkeypatch, worker, ctx):  # noqa: F811
    fake, _ = scripted([tool_call_chunks("w1", "run_python", {"code": "open('a.txt','w').write('1')"}), [chunk(content="ok")]])
    monkeypatch.setattr(litellm, "acompletion", fake)
    assert "Stray" not in delegate(ctx, role="builder", task="build").content


def test_snapshot_ignores_workers_and_reports_large_files(ctx):
    ctx.work_dir.mkdir(parents=True, exist_ok=True)
    (ctx.work_dir / "workers" / "w2").mkdir(parents=True)
    big = ctx.work_dir / "big.bin"
    big.write_bytes(b"a" * (worker_isolation.MAX_KEEP + 1))
    snap = worker_isolation.snapshot(ctx.work_dir)
    (ctx.work_dir / "workers" / "w2" / "f").write_text("other worker")
    big.write_bytes(b"b")
    assert worker_isolation.restore(snap) == ["big.bin (modified, too large to restore)"]
    assert (ctx.work_dir / "workers" / "w2" / "f").exists()


def test_read_only_refuses_builder_and_limits_researcher(monkeypatch, worker, ctx):  # noqa: F811
    ctx.options = {"permissions": "full", "mode": "plan"}
    with pytest.raises(subagents.ToolError, match="builder delegation refused"):
        asyncio.run(subagents.delegate(ctx, role="builder", task="x"))
    refusal = tool_policy.worker_tools(("write_file",), subagents.ROLE_TOOLS["builder"], "builder", {"permissions": "read_only"})[1]
    assert "read-only" in refusal                      # (delegate itself is already refused outside Full access)
    fake, calls = scripted([[chunk(content="found")]])
    monkeypatch.setattr(litellm, "acompletion", fake)
    ctx.options = {"permissions": "full", "mode": "plan", "parts_mode": "prefer", "inventory_ids": []}
    result = asyncio.run(subagents.delegate(ctx, role="researcher", task="look"))
    assert "Worker" in result.content
    assert {t["function"]["name"] for t in calls[0]["tools"]} == {"read_file", "list_files", "my_parts"}


def test_worker_tools_rules():
    roles = subagents.ROLE_TOOLS
    full = {"permissions": "full", "parts_mode": "prefer", "inventory_ids": []}
    assert tool_policy.worker_tools(("read_file", "write_file", "run_shell", "my_parts"), roles["researcher"], "researcher", full)[0] \
        == ["read_file", "write_file", "my_parts"]                           # run_shell is in no role default
    assert "my_parts" not in tool_policy.worker_tools(("my_parts", "read_file"), roles["researcher"], "researcher", {})[0]
    assert tool_policy.worker_tools(("run_python",), roles["reviewer"], "reviewer", full)[0] == []     # outside the role default


def test_approval_once_with_contract_and_workers_never_ask(monkeypatch, worker, ctx):  # noqa: F811
    run = Run(ctx.chat_id)
    run.options = ctx.options = {"permissions": "ask"}
    events = []
    run.emit = lambda event, data: events.append((event, data))
    monkeypatch.setattr(agent, "available_tools", lambda options, chat_id=None: [{"function": {"name": "delegate"}}])
    fake, calls = scripted([tool_call_chunks("w1", "write_file", {"path": "a.txt", "content": "1"}),
                            tool_call_chunks("w2", "run_toolkit", {"arguments": ["--help"]}), [chunk(content="done")]])
    monkeypatch.setattr(litellm, "acompletion", fake)

    async def go():
        call = asyncio.create_task(agent.execute_tool(run, ctx, "c1", "delegate",
                                                      '{"role": "builder", "task": "build a wheel", "tools": ["write_file"]}'))
        for _ in range(100):
            if run.approvals:
                break
            await asyncio.sleep(0.01)
        (info, future), = run.approvals.values()
        assert info["contract"]["role"] == "builder" and info["contract"]["task"] == "build a wheel"
        assert info["contract"]["tools"] == ["write_file"] and info["contract"]["max_steps"] == 20
        future.set_result(True)
        return await call
    result = asyncio.run(go())
    assert "Worker" in result.content and "a.txt" in result.content
    assert [e for e, _ in events].count("approval") == 1                       # the delegation only
    denied = [m["content"] for m in calls[2]["messages"] if m["role"] == "tool"]
    assert len(denied) == 2 and "not available to this worker" in denied[1]     # outside the contract: denial text


def test_denied_delegation_never_runs(monkeypatch, worker, ctx):  # noqa: F811
    run = Run(ctx.chat_id)
    run.options = ctx.options = {"permissions": "ask"}
    run.emit = lambda *_: None
    monkeypatch.setattr(agent, "available_tools", lambda options, chat_id=None: [{"function": {"name": "delegate"}}])

    async def go():
        call = asyncio.create_task(agent.execute_tool(run, ctx, "c1", "delegate", '{"role": "builder", "task": "x"}'))
        while not run.approvals:
            await asyncio.sleep(0.01)
        next(iter(run.approvals.values()))[1].set_result(False)
        return await call
    assert "denied" in asyncio.run(go()).content
    assert not (ctx.work_dir / "workers").exists()


def test_ask_without_approval_still_refused(worker, ctx):  # noqa: F811
    ctx.options = {"permissions": "ask"}
    with pytest.raises(subagents.ToolError, match="approval"):
        asyncio.run(subagents.delegate(ctx, role="builder", task="x"))


def test_cancel_restores_strays(monkeypatch, worker, ctx):  # noqa: F811
    ctx.work_dir.mkdir(parents=True, exist_ok=True)
    plan = ctx.work_dir / "plan.ldr"
    plan.write_text("original")
    sandbox.give_to_agent(plan)

    async def fake(**kwargs):
        plan.write_text("hijacked")
        raise asyncio.CancelledError
    monkeypatch.setattr(litellm, "acompletion", fake)
    ctx.options = {"permissions": "full"}
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(subagents.delegate(ctx, role="builder", task="x"))
    assert plan.read_text() == "original"
