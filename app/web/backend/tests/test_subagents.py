import asyncio
import json
import os
import re

import litellm
import pytest

import agent
import llm_config
import model_catalog
import run_state
import settings
import subagents
import tool_policy
import usage
from store import ChatStore
from test_agent import chunk, scripted, tool_call_chunks


@pytest.fixture
def worker(monkeypatch):
    entry = llm_config.create({"model_name": "worker", "litellm_params": {"model": "openai/gpt-6-luna", "api_key": "sk-x"},
                               "capabilities": {"tools": True, "vision": False}})
    monkeypatch.setenv("LDRAW_NOVA_SUBAGENTS", "1")
    monkeypatch.setenv("LDRAW_NOVA_WORKER_LLM_ID", entry["id"])
    real = model_catalog.entry_profile
    monkeypatch.setattr(model_catalog, "entry_profile", lambda e: {**real(e), "pricing": {"input": 10.0, "output": 10.0, "currency": "USD"}})
    return entry


def names(options=None):
    return {t["function"]["name"] for t in tool_policy.available_tools({"permissions": "full", **(options or {})})}


def save_result(ctx, result):
    """What the step loop does with a delegate result: save it as a tool message carrying the worker's record."""
    ctx.store.add_message(ctx.chat_id, {"role": "tool", "tool_call_id": "x", "name": "delegate", "content": result.content,
                                        **result.record})


def delegate(ctx, **args):
    ctx.options = {"permissions": "full", **(ctx.options or {})}
    return asyncio.run(subagents.delegate(ctx, **args))


def test_only_offered_and_allowed_with_full_access(worker, ctx):
    for permissions in ("ask", "read_only"):
        assert "delegate" not in {t["function"]["name"] for t in tool_policy.available_tools({"permissions": permissions})}
    ctx.options = {"permissions": "ask"}
    with pytest.raises(subagents.ToolError, match="Full access"):
        asyncio.run(subagents.delegate(ctx, role="builder", task="anything"))


def test_tool_absent_unless_flag_and_worker_configured(monkeypatch, worker):
    assert "delegate" in names()
    monkeypatch.setenv("LDRAW_NOVA_SUBAGENTS", "0")
    assert "delegate" not in names()
    monkeypatch.setenv("LDRAW_NOVA_SUBAGENTS", "1")
    monkeypatch.setenv("LDRAW_NOVA_WORKER_LLM_ID", "")
    assert "delegate" not in names()
    monkeypatch.setenv("LDRAW_NOVA_WORKER_LLM_ID", worker["id"])
    assert "delegate" not in names({"mode": "plan"})        # read-only modes never delegate


def test_flag_off_leaves_tools_unchanged(monkeypatch, worker):
    on = names()
    monkeypatch.setenv("LDRAW_NOVA_SUBAGENTS", "0")
    assert names() == on - {"delegate"}


def test_worker_runs_and_cost_lands_in_totals(monkeypatch, worker, ctx):
    fake, calls = scripted([[*tool_call_chunks("w1", "list_files", {})], [chunk(content="Listed.")]])
    monkeypatch.setattr(litellm, "acompletion", fake)
    ctx.options = {"parts_mode": "prefer", "inventory_ids": []}
    result = delegate(ctx, role="researcher", task="look around", inputs=["a.txt"])
    assert "status ok" in result.content.splitlines()[0] and "Summary: Listed." in result.content
    assert "a.txt" in calls[0]["messages"][1]["content"]
    assert {t["function"]["name"] for t in calls[0]["tools"]} == set(subagents.ROLE_TOOLS["researcher"])
    assert not [m for m in ctx.store.messages(ctx.chat_id) if m.get("_subagent")]       # nothing hidden any more
    assert result.record["_worker"]["steps"] == 2 and result.record["_worker"]["status"] == "ok"
    save_result(ctx, result)
    totals = usage.usage_totals(ctx.store.messages(ctx.chat_id))
    assert totals["steps"] == 1 and totals["cost_eur"] > 0 and totals["worker_cost_eur"] == totals["cost_eur"]
    assert "about EUR" in result.content


GOOD_LDR = "0 good\n1 4 0 0 0 1 0 0 0 1 0 0 0 1 3001.dat\n1 15 0 -24 0 1 0 0 0 1 0 0 0 1 3003.dat\n"


def test_worker_writes_outputs_and_result_format(monkeypatch, worker, ctx):
    state = {}

    async def fake(**kwargs):
        state.setdefault("calls", []).append(kwargs)
        wid = re.search(r"output/workers/(\w+)/", kwargs["messages"][0]["content"]).group(1)
        chunks = (tool_call_chunks("w1", "write_file", {"path": f"output/workers/{wid}/part.ldr", "content": GOOD_LDR})
                  if len(state["calls"]) == 1 else [chunk(content="Made part.ldr with width 160. No defects.")])

        async def stream():
            for c in chunks:
                yield c
        return stream()
    monkeypatch.setattr(litellm, "acompletion", fake)
    result = delegate(ctx, role="builder", task="build", outputs=["part.ldr"], summary_chars=20)
    lines = result.content.splitlines()
    assert re.fullmatch(r"Worker \w+: status ok", lines[0])
    assert any(line == "Summary: Made part.ldr with w" for line in lines)      # capped at 20 chars
    assert any(re.search(r"Files: output/workers/\w+/part\.ldr \(\d+ bytes\)", line) for line in lines)
    assert any(line.startswith("Cost: 2 model calls, about EUR") for line in lines)
    assert any(line.startswith("Validation: ok") for line in lines)
    # transcript: side file only
    wid = lines[0].split()[1].rstrip(":")
    records = ctx.store.worker_messages(ctx.chat_id, wid)
    assert [r["role"] for r in records] == ["user", "assistant", "tool", "assistant"]
    assert not [m for m in ctx.store.messages(ctx.chat_id) if m["role"] == "tool" or m.get("_worker") != wid and m.get("_subagent")]


@pytest.mark.parametrize("args, message", [
    ({"role": "boss", "task": "x"}, "role must be one of"),
    ({"role": "builder", "task": "x" * 1501}, "at most 1500"),
    ({"role": "builder", "task": "x", "tools": ["run_shell"]}, "never available to workers"),
    ({"role": "builder", "task": "x", "tools": ["publish_model", "delegate"]}, "publish_model, delegate"),
    ({"role": "builder", "task": "x", "tools": ["nope"]}, "tools not allowed"),
    ({"role": "builder", "task": "x", "max_steps": 41}, "max_steps must be"),
    ({"role": "builder", "task": "x", "max_eur": 5}, "max_eur must be"),
    ({"role": "builder", "task": "x", "summary_chars": 1501}, "summary_chars must be"),
    ({"role": "builder", "task": "x", "outputs": ["../x.ldr"]}, "must be a file inside output/workers/"),
    ({"role": "builder", "task": "x", "outputs": ["output/other/x.ldr"]}, "must be a file inside"),
    ({"role": "builder", "task": "x", "outputs": ["/etc/passwd"]}, "must be a file inside"),
    ({"role": "builder", "task": "x", "inputs": ["../secret"]}, "under output/"),
    ({"role": "reviewer", "task": "x", "tools": ["view_image"]}, "needs a worker model with vision"),
])
def test_invalid_contract_is_rejected_without_running(monkeypatch, worker, ctx, args, message):
    real = llm_config.capabilities
    monkeypatch.setattr(llm_config, "capabilities", lambda e: {**real(e), "vision": False})
    fake, calls = scripted([[chunk(content="must not run")]])
    monkeypatch.setattr(litellm, "acompletion", fake)
    with pytest.raises(subagents.ToolError, match="Contract rejected") as info:
        delegate(ctx, **args)
    assert message in str(info.value) and not calls
    assert not (ctx.chat_dir / "workers").exists()
    assert not [m for m in ctx.store.messages(ctx.chat_id) if m.get("_subagent")]


def test_contract_defaults_and_role_prompts(worker):
    entry = llm_config.get(settings.worker_llm_id())
    c = subagents.validate_contract({"role": "researcher", "task": "t", "outputs": ["output/workers/abc/n.md"]}, entry, "abc")
    assert (c.max_steps, c.max_eur, c.summary_chars, c.outputs) == (10, 0.10, 600, ["n.md"])
    assert not set(c.tools) & set(subagents.NEVER_TOOLS)
    for role in subagents.ROLE_TOOLS:
        assert not set(subagents.ROLE_TOOLS[role]) & set(subagents.NEVER_TOOLS)
        prompt = subagents.worker_prompt(subagents.validate_contract({"role": role, "task": "t"}, entry, "abc"))
        assert f"Role: {role}" in prompt and "output/workers/abc/" in prompt and len(prompt) < 1200


def test_step_cap_and_forbidden_tools(monkeypatch, worker, ctx):
    fake, calls = scripted([
        [*tool_call_chunks("w1", "publish_model", {"path": "x.mpd", "name": "x"})],
        [*tool_call_chunks("w2", "delegate", {"role": "builder", "task": "again"})],
        [*tool_call_chunks("w3", "run_shell", {"command": "ls"})],
        [chunk(content="never reached")],
    ])
    monkeypatch.setattr(litellm, "acompletion", fake)
    result = delegate(ctx, role="builder", task="loop", max_steps=3)
    assert "status step_cap" in result.content and len(calls) == 3
    assert "run_shell" not in json.dumps(calls[0]["tools"]) and "delegate" not in json.dumps(calls[0]["tools"])
    tool_msgs = [m["content"] for m in calls[2]["messages"] if m["role"] == "tool"]
    assert len(tool_msgs) == 2 and all("not available to this worker" in t for t in tool_msgs)
    assert not [m for m in ctx.store.models(ctx.chat_id)]


def test_chat_budget_stops_worker(monkeypatch, worker, ctx):
    ctx.options = {"budget_eur": 0.0001}
    ctx.store.add_message(ctx.chat_id, {"role": "assistant", "content": "", "_ui_only": True, "_usage": {
        "prompt": 1, "completion": 1, "cache_read": 0, "cache_write": 0, "cost_usd": 5.0}})
    fake, calls = scripted([[chunk(content="should not run")]])
    monkeypatch.setattr(litellm, "acompletion", fake)
    result = delegate(ctx, role="builder", task="anything")
    assert "status over_budget" in result.content and "chat budget" in result.content and not calls


def test_chat_budget_stops_worker_between_steps(monkeypatch, worker, ctx):
    ctx.options = {"budget_eur": 0.0001}                    # a step costs far more with the test price
    fake, calls = scripted([[*tool_call_chunks("w1", "list_files", {})], [chunk(content="never")]])
    monkeypatch.setattr(litellm, "acompletion", fake)
    result = delegate(ctx, role="builder", task="anything")
    assert len(calls) == 1 and "status over_budget" in result.content
    assert ctx.ledger.live_eur > 0.0001 and result.record["_usage"]["cost_usd"] > 0


def test_worker_max_eur_stops_worker(monkeypatch, worker, ctx):
    fake, calls = scripted([[*tool_call_chunks("w1", "list_files", {})], [chunk(content="never")]])
    monkeypatch.setattr(litellm, "acompletion", fake)
    result = delegate(ctx, role="builder", task="anything", max_eur=0.000001)
    assert len(calls) == 1 and "status over_budget" in result.content and "worker's budget" in result.content


def test_provider_error_fails_the_worker_not_the_turn(monkeypatch, worker, ctx):
    async def broken(**_kwargs):
        raise RuntimeError("401 invalid api key sk-secret")
    monkeypatch.setattr(litellm, "acompletion", broken)
    result = delegate(ctx, role="researcher", task="find")
    assert "status failed" in result.content and "RuntimeError" in result.content and "sk-secret" not in result.content


# --- through the planner loop ---

def _planner_entry():
    return llm_config.create({"model_name": "planner", "litellm_params": {"model": "anthropic/claude-opus-5", "api_key": "sk-y"},
                              "capabilities": {"tools": True, "vision": True}})


def _run_planner(store: ChatStore, entry, text="build a thing"):
    os.chmod(settings.DATA_DIR.parent, 0o755)
    chat = store.create_chat()

    async def go():
        await agent.start_turn(store, chat["id"], text, entry["id"], {"permissions": "full"})
        await run_state._runs[chat["id"]].task
    asyncio.run(go())
    return chat["id"]


def _strip_marks(messages):
    """The request without its cache markers: a marked message is a one-block list, an unmarked one a string."""
    def plain(m):
        c = m.get("content")
        if isinstance(c, list) and len(c) == 1 and m["role"] != "system":
            c = c[0]["text"]
        return {k: v for k, v in {**m, "content": c}.items() if k != "cache_control"}
    return [plain(m) if m["role"] != "system" else m for m in messages]


def test_planner_delegates_and_prefix_stays_identical(monkeypatch, worker, store):
    planner = _planner_entry()
    fake, calls = scripted([
        [*tool_call_chunks("p1", "delegate", {"role": "builder", "task": "write a note", "max_steps": 2})],
        [*tool_call_chunks("w1", "list_files", {})],
        [*tool_call_chunks("w2", "list_files", {})],
        [chunk(content="Planner done.")],
    ])
    monkeypatch.setattr(litellm, "acompletion", fake)
    chat_id = _run_planner(store, planner)
    messages = store.messages(chat_id)
    assert messages[-1]["content"] == "Planner done."
    planner_calls = [c for c in calls if c["model"] == "claude-opus-5" or "anthropic" in c["model"]]
    worker_calls = [c for c in calls if c not in planner_calls]
    assert len(planner_calls) == 2 and len(worker_calls) == 2                       # at most max_steps worker calls
    [result] = [m for m in messages if m["role"] == "tool"]
    assert result["name"] == "delegate" and "status step_cap" in result["content"] and "Files:" in result["content"]
    # worker messages are in the side file, not in messages.jsonl
    assert len(store.worker_messages(chat_id, result["_worker"]["id"])) == 1 + 2 + 2      # task, 2 assistant, 2 tool results
    assert [m for m in messages if m["role"] == "tool"] == [result]
    assert not [m for m in messages if m.get("_subagent")]
    assert result["_worker"]["id"] and result["_worker"]["steps"] == 2 and result["_usage"]["cost_usd"] > 0
    totals = usage.usage_totals(messages)
    assert totals["steps"] == 3 and totals["worker_cost_eur"] > 0           # two planner steps plus one worker record
    # the planner's request prefix is byte-identical: only messages are appended
    first, second = (_strip_marks(c["messages"]) for c in planner_calls)
    assert json.dumps(first[0]) == json.dumps(second[0])          # system block with its marker, byte for byte
    assert json.dumps(first[1:]) == json.dumps(second[1:len(first)])
    assert [m["role"] for m in second[len(first):]] == ["assistant", "tool"]
    assert "delegate" in json.dumps(planner_calls[0]["tools"])


def test_planner_without_flag_has_no_delegate_and_no_side_files(monkeypatch, store):
    monkeypatch.setenv("LDRAW_NOVA_SUBAGENTS", "0")
    fake, calls = scripted([[chunk(content="Hello.")]])
    monkeypatch.setattr(litellm, "acompletion", fake)
    chat_id = _run_planner(store, _planner_entry())
    assert "delegate" not in json.dumps(calls[0]["tools"])
    assert [m["role"] for m in store.messages(chat_id)] == ["user", "assistant"]
    assert not (store.chat_dir(chat_id) / "workers").exists()


def test_settings_default_off(monkeypatch):
    monkeypatch.delenv("LDRAW_NOVA_SUBAGENTS", raising=False)
    assert settings.subagents_enabled() is False
