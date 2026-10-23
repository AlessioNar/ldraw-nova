"""Flag-gated `delegate` tool: the planner hands a bounded job to a cheap worker model (easy to delete).

The worker runs the same step loop as the planner (agent_loop.run_steps) on its own model, from a validated
contract (docs/plans/swarm-subagents/README.md section 2). Its transcript goes to chats/<chat>/workers/<id>.jsonl,
not to messages.jsonl, so the planner's history and prompt cache are untouched. Cost is shared through
budget.BudgetLedger: the worker reserves its max_eur, every step is added to the ledger, and when it ends one
aggregate `_usage` plus `_worker` lands on the delegate tool result (or on a hidden record if it was cancelled).
"""
from __future__ import annotations

import posixpath
import secrets
from dataclasses import dataclass, field

import inference
import llm_config
import model_catalog
import settings
import usage
from agent_loop import FileSink, run_steps
from history import llm_history
from system_prompt import cache_control, with_rolling_breakpoint
import providers
from tools import TOOLS, ToolContext, ToolError, ToolResult, dispatch

ALLOWED_TOOLS = ("read_file", "list_files", "my_parts", "view_image", "write_file", "run_toolkit", "run_python")
NEVER_TOOLS = ("publish_model", "delegate", "run_shell")      # named in the rejection message
ROLE_TOOLS = {
    "builder": ("read_file", "list_files", "write_file", "run_python", "run_toolkit"),
    "researcher": ("read_file", "list_files", "my_parts", "write_file", "run_toolkit"),
    "reviewer": ("read_file", "list_files", "view_image", "run_toolkit"),
}
ROLE_STEPS = {"builder": 20, "researcher": 10, "reviewer": 12}
MAX_STEPS, MAX_EUR, DEFAULT_EUR = 40, 1.0, 0.10
DEFAULT_SUMMARY, MAX_SUMMARY, MAX_TASK, RESULT_CHARS = 600, 1500, 1500, 8000

_RULES = ("You are a worker agent doing one bounded job for a planner. You cannot ask questions, publish models or "
          "delegate. Use only your tools, work efficiently and do not repeat a call that already failed. Write every "
          "file you create inside {dir}/ . Finish by writing the required output files, then reply with at most "
          "{chars} characters: what you made, the interface values you used, and known defects.")
ROLE_PROMPTS = {
    "builder": "Role: builder. Build the requested LDraw submodel or file from the task and its interface values. "
               "Read only the toolkit guides you need, validate the result with run_toolkit before you finish. ",
    "researcher": "Role: researcher. Find the requested parts or facts with my_parts and run_toolkit discover/search. "
                  "Write notes only if outputs are required. Reply with concrete ids, colours and counts. ",
    "reviewer": "Role: reviewer. Inspect the named files: run the toolkit validation and, if you can, render and view "
                "them. Reply with a short list of concrete defects, or say there are none. ",
}


def enabled() -> bool:
    return settings.subagents_enabled() and llm_config.get(settings.worker_llm_id()) is not None


@dataclass
class Contract:
    id: str
    role: str
    task: str
    inputs: list[str] = field(default_factory=list)
    tools: tuple[str, ...] = ()
    outputs: list[str] = field(default_factory=list)       # names relative to output/workers/<id>/
    max_steps: int = 20
    max_eur: float = DEFAULT_EUR
    summary_chars: int = DEFAULT_SUMMARY

    @property
    def dir(self) -> str:
        return f"output/workers/{self.id}"


def _clean(path: str) -> str | None:
    """A normalised relative path, or None if it is absolute, hidden or climbs out."""
    path = (path or "").strip().replace("\\", "/")
    norm = posixpath.normpath(path) if path else ""
    if not norm or norm == "." or norm.startswith("/") or norm == ".." or norm.startswith("../") or ".." in path.split("/"):
        return None
    return None if any(p.startswith(".") for p in norm.split("/")) else norm


def validate_contract(args: dict, entry: dict, worker_id: str) -> Contract:
    """Turn the planner's arguments into a Contract, or raise ToolError listing every problem."""
    problems: list[str] = []
    role = args.get("role")
    if role not in ROLE_TOOLS:
        problems.append(f"role must be one of {', '.join(ROLE_TOOLS)} (got {role!r})")
        role = "builder"
    task = args.get("task")
    if not isinstance(task, str) or not task.strip():
        problems.append("task is required")
        task = ""
    elif len(task) > MAX_TASK:
        problems.append(f"task is {len(task)} characters, at most {MAX_TASK}")

    def number(name: str, default, ceiling, kind):
        value = args.get(name)
        if value is None:
            return default
        try:
            value = kind(value)
        except (TypeError, ValueError):
            problems.append(f"{name} must be a number")
            return default
        if value <= 0 or value > ceiling:
            problems.append(f"{name} must be above 0 and at most {ceiling} (got {value})")
            return default
        return value

    steps = number("max_steps", ROLE_STEPS[role], MAX_STEPS, int)
    eur = number("max_eur", DEFAULT_EUR, MAX_EUR, float)
    chars = number("summary_chars", DEFAULT_SUMMARY, MAX_SUMMARY, int)

    tools = args.get("tools")
    if tools is None:
        tools = list(ROLE_TOOLS[role])
    elif not isinstance(tools, list) or not all(isinstance(t, str) for t in tools):
        problems.append("tools must be a list of tool names")
        tools = []
    bad = [t for t in tools if t not in ALLOWED_TOOLS]
    if bad:
        forbidden = [t for t in bad if t in NEVER_TOOLS]
        problems.append(f"tools not allowed for workers: {', '.join(bad)}"
                        + (f" ({', '.join(forbidden)} are never available to workers)" if forbidden else "")
                        + f". Allowed: {', '.join(ALLOWED_TOOLS)}")
    if "view_image" in tools and llm_config.capabilities(entry)["vision"] is not True:
        problems.append("view_image needs a worker model with vision; the configured worker has none")

    inputs = args.get("inputs") or []
    if not isinstance(inputs, list) or not all(isinstance(p, str) for p in inputs):
        problems.append("inputs must be a list of paths under output/")
        inputs = []
    clean_inputs = []
    for path in inputs:
        norm = _clean(path)
        if norm is None:
            problems.append(f"input {path!r} must be a path under output/")
        else:
            clean_inputs.append(norm)

    prefix = f"output/workers/{worker_id}/"
    outputs = args.get("outputs") or []
    if not isinstance(outputs, list) or not all(isinstance(p, str) for p in outputs):
        problems.append("outputs must be a list of file names")
        outputs = []
    clean_outputs = []
    for path in outputs:
        rel = path.strip().replace("\\", "/")
        rel = rel.removeprefix(prefix)
        norm = _clean(rel)
        if norm is None or norm.startswith("output/") or norm.startswith("workers/"):
            problems.append(f"output {path!r} must be a file inside {prefix} (give a plain file name)")
        else:
            clean_outputs.append(norm)
    if clean_outputs and not set(tools) & {"write_file", "run_python", "run_toolkit"}:
        problems.append("outputs are required but the tools cannot write files (add write_file)")

    if problems:
        raise ToolError("Contract rejected, nothing was run: " + "; ".join(problems) + ".")
    return Contract(worker_id, role, task.strip(), clean_inputs, tuple(tools), clean_outputs, steps, eur, chars)


def worker_prompt(contract: Contract) -> str:
    return ROLE_PROMPTS[contract.role] + _RULES.format(dir=contract.dir, chars=contract.summary_chars)


def _task_message(contract: Contract) -> str:
    lines = [f"Task: {contract.task}"]
    if contract.inputs:
        lines.append("Inputs you may read: " + ", ".join(contract.inputs))
    lines.append(f"Required output files in {contract.dir}/: " + (", ".join(contract.outputs) or "none (reply only)"))
    lines.append(f"Limits: at most {contract.max_steps} model calls, at most {contract.summary_chars} characters in your final reply.")
    return "\n".join(lines)


def _aggregate(worker_id: str, model: str, sink: FileSink, status: str) -> dict:
    """The worker's one chat record: summed tokens and cost as `_usage`, plus `_worker` for the totals and display."""
    totals = usage.usage_totals(sink.messages())
    summed = {k: totals[k] for k in ("prompt", "completion", "cache_read", "cache_write")}
    if not totals["unpriced_steps"]:
        summed["cost_usd"] = totals["cost_usd"]
    return {"_usage": summed, "_worker": {"id": worker_id, "model": model, "steps": totals["steps"], "status": status}}


def _files(ctx: ToolContext, contract: Contract) -> list[tuple[str, int]]:
    root = ctx.work_dir / "workers" / contract.id
    return sorted((f"{contract.dir}/{p.relative_to(root).as_posix()}", p.stat().st_size)
                  for p in root.rglob("*") if p.is_file()) if root.is_dir() else []


async def delegate(ctx: ToolContext, role: str | None = None, task: str | None = None, inputs: list[str] | None = None,
                   tools: list[str] | None = None, outputs: list[str] | None = None, max_steps: int | None = None,
                   max_eur: float | None = None, summary_chars: int | None = None) -> ToolResult:
    entry = llm_config.get(settings.worker_llm_id()) if settings.subagents_enabled() else None
    if entry is None:
        raise ToolError("delegate is not configured")
    if ctx.options.get("permissions") != "full":
        raise ToolError("delegate needs Full access: workers run tools without asking for each one")
    args = {"role": role, "task": task, "inputs": inputs, "tools": tools, "outputs": outputs, "max_steps": max_steps,
            "max_eur": max_eur, "summary_chars": summary_chars}
    contract = validate_contract(args, entry, secrets.token_hex(4))
    model = entry["litellm_params"]["model"]
    ledger = ctx.ledger
    ledger.limit_eur = ctx.options.get("budget_eur") or None
    if ledger.limit_eur and usage.step_cost_usd({"prompt": 1, "completion": 1, "cache_read": 0, "cache_write": 0},
                                                model_catalog.entry_profile(entry)["pricing"], model) is None:
        raise ToolError("delegate refused: the worker model has no known price, so it cannot run under a chat budget")
    cap_eur = ledger.reserve(contract.id, contract.max_eur, ctx.store.messages(ctx.chat_id))   # clamped to what is left
    (ctx.work_dir / "workers" / contract.id).mkdir(parents=True, exist_ok=True)
    sink = FileSink(ctx.chat_dir / "workers" / f"{contract.id}.jsonl")
    sink.add({"role": "user", "content": _task_message(contract)})
    system = worker_prompt(contract)
    schemas = [TOOLS[name][0] for name in contract.tools if name in TOOLS]
    vision = llm_config.capabilities(entry)["vision"] is True
    reason = ""

    def before_step(stored: list[dict]) -> bool:
        nonlocal reason
        if ledger.chat_exhausted(contract.id, ctx.store.messages(ctx.chat_id)):
            reason = "the chat budget was reached"
        elif ledger.spent_eur(contract.id) >= cap_eur:
            reason = f"the worker's budget of EUR {cap_eur:.2f} was reached"
        return bool(reason)

    def build_messages(stored: list[dict]) -> list[dict]:
        sys_content = [{"type": "text", "text": system, "cache_control": cache_control(model)}] \
            if providers.supports_prompt_caching(model) else system
        history = llm_history(stored, vision, lambda ref: ctx.store.resolve(ctx.chat_id, ref), model, mark_turns=True)
        return with_rolling_breakpoint([{"role": "system", "content": sys_content}, *history], model)

    def record_usage(message: dict) -> None:
        if message.get("_usage"):
            ledger.add(contract.id, message["_usage"].get("cost_usd"))

    async def run_tool(call: dict) -> ToolResult:
        name = call["function"]["name"]
        if name not in contract.tools:
            return ToolResult(f"Error: tool {name!r} is not available to this worker (allowed: {', '.join(contract.tools)}).")
        out = await dispatch(ctx, name, call["function"]["arguments"])
        out.content = out.content[:RESULT_CHARS]
        out.models = []                           # only the planner publishes
        return out

    ended, error, finished = "", "", False
    try:
        params = await inference.params_for(entry, {})
        ended = await run_steps(entry=entry, params=params, sink=sink, tools=schemas, max_steps=contract.max_steps,
                                build_messages=build_messages, run_tool=run_tool,
                                ref=lambda png: ctx.store.ref(ctx.chat_id, png), before_step=before_step,
                                after_message=record_usage)
        finished = True
    except Exception as exc:  # noqa: BLE001 - a worker failing must not end the planner's turn
        error, finished = exc.__class__.__name__, True
    finally:
        ledger.release(contract.id)
        if not finished:                                      # Stop or another abort: the worker's cost still counts
            ctx.store.add_message(ctx.chat_id, {"role": "assistant", "content": "", "_ui_only": True, "_hidden": True,
                                                **_aggregate(contract.id, model, sink, "cancelled")})
    final = next((m["content"] for m in reversed(sink.messages())
                  if m["role"] == "assistant" and m.get("content") and not m.get("tool_calls")), "")
    files = _files(ctx, contract)
    names = {path for path, _size in files}
    missing = [o for o in contract.outputs if f"{contract.dir}/{o}" not in names]
    if error:
        status = "failed"
    elif reason:
        status = "over_budget"
    elif ended == "cap":
        status = "step_cap"
    elif missing or (not files and contract.role == "builder"):
        status = "partial" if files else "failed"
    else:
        status = "ok"
    totals = usage.usage_totals(sink.messages())
    lines = [f"Worker {contract.id}: status {status}",
             f"Contract: role {contract.role}, model {model}, max {contract.max_steps} steps, max EUR {contract.max_eur:.2f}, "
             f"outputs {', '.join(contract.outputs) or 'none'}"]
    if error:
        lines.append(f"Error: {error}: the provider request failed.")
    if reason:
        lines.append(f"Stopped because {reason}.")
    if missing:
        lines.append(f"Missing outputs: {', '.join(missing)}")
    lines.append("Summary: " + (final[:contract.summary_chars] or "(none)"))
    lines.append("Files: " + ("; ".join(f"{path} ({size} bytes)" for path, size in files) or "none"))
    lines.append(f"Cost: {totals['steps']} model calls, about EUR {totals['cost_eur']:.4f}")
    lines.append("Validation: not run by the app; check the files before using them.")
    return ToolResult("\n".join(lines), record=_aggregate(contract.id, model, sink, status))
