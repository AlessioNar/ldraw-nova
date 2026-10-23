"""Flag-gated `delegate` tool: the planner hands a bounded job to a cheap worker model (easy to delete).

The worker runs the same step loop as the planner (agent_loop.run_steps) on its own model, from a validated
contract (docs/plans/swarm-subagents/README.md section 2). Its transcript goes to chats/<chat>/workers/<id>.jsonl,
not to messages.jsonl, so the planner's history and prompt cache are untouched. Cost is shared through
budget.BudgetLedger: the worker reserves its max_eur, every step is added to the ledger, and when it ends one
aggregate `_usage` plus `_worker` lands on the delegate tool result (or on a hidden record if it was cancelled).
"""
from __future__ import annotations

import asyncio
import json
import posixpath
import secrets
from collections import deque
from dataclasses import dataclass, field, replace
from pathlib import Path

import inference
import llm_config
import model_catalog
import settings
import usage
import worker_isolation
from agent_loop import FileSink, run_steps
from history import llm_history
from system_prompt import cache_control, with_rolling_breakpoint
import providers
from tools import TOOLS, ToolContext, ToolError, ToolResult, dispatch, parts_check, run_command

ALLOWED_TOOLS = ("read_file", "list_files", "my_parts", "view_image", "write_file", "run_toolkit", "run_python")
NEVER_TOOLS = ("publish_model", "delegate", "run_shell")      # named in the rejection message
ROLE_TOOLS = {
    "builder": ("read_file", "list_files", "write_file", "run_python", "run_toolkit"),
    "researcher": ("read_file", "list_files", "my_parts", "write_file", "run_toolkit"),
    "reviewer": ("read_file", "list_files", "view_image", "run_toolkit"),
}
ROLE_STEPS = {"builder": 20, "researcher": 10, "reviewer": 12}
MAX_STEPS, MAX_EUR, DEFAULT_EUR = 40, 1.0, 0.10
GATED = (".ldr", ".mpd", ".dat")
NO_WRITE_STEPS, REPEAT_CALLS, REPEAT_WINDOW, REPAIR_STEPS, MAX_ERRORS_SHOWN = 8, 3, 8, 8, 6
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


def worker_entry(options: dict | None) -> dict | None:
    """The worker model entry for a chat: its own worker_model_id option, else the Settings / env default."""
    options = options or {}
    wid = (options.get("worker_model_id") or "") if "worker_model_id" in options else llm_config.default_worker_id()
    return llm_config.get(wid) if wid else None


def enabled(options: dict | None = None) -> bool:
    return worker_entry(options) is not None


def worker_effort(entry: dict) -> str | None:
    """The lowest reasoning effort the worker model offers (hidden reasoning is most of a worker's output)."""
    efforts = model_catalog.entry_profile(entry)["efforts"]
    order = ["none", *model_catalog.EFFORTS]
    return min(efforts, key=lambda e: order.index(e) if e in order else len(order)) if efforts else None


def check_worker(options: dict) -> None:
    """Refuse a worker the chat cannot use: missing, no tool calling, or unpriced while a budget is set."""
    wid = options.get("worker_model_id")
    if not wid:
        return
    entry = llm_config.get(wid)
    if entry is None:
        raise ValueError("The worker model no longer exists. Choose another one or No workers")
    if llm_config.unsupported(entry) or llm_config.capabilities(entry)["tools"] is False:
        raise ValueError("The worker model must support tool calling. Choose another one or No workers")
    prices = model_catalog.entry_profile(entry).get("pricing") or {}
    if options.get("budget_eur") and (prices.get("input") is None or prices.get("output") is None or prices.get("currency") != "USD"):
        raise ValueError("The worker model has no known prices, so a budget cannot cover it. Choose a priced worker or No workers")


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


def worker_context(ctx: ToolContext, root: Path) -> ToolContext:
    """The worker's tool context: writes confined to its folder, no events into the planner's stream, no approvals.

    The options are the chat's (mode, parts steering) minus any approval flag; workers never ask the user:
    a tool outside the contract is denied in run_tool below, and dispatch itself never asks.
    """
    options = {k: v for k, v in ctx.options.items() if k != "delegation_approved"}
    return replace(ctx, write_root=root, emit=lambda *_: None, options=options)


def approval_contract(arguments: str | dict, options: dict) -> dict:
    """What the user sees once when approving a delegation under `ask`: the whole validated contract."""
    entry = llm_config.get(settings.worker_llm_id()) if settings.subagents_enabled() else None
    if entry is None:
        return {"error": "delegate is not configured"}
    try:
        args = json.loads(arguments or "{}") if isinstance(arguments, str) else dict(arguments or {})
        c = validate_contract(args, entry, "<id>")
        import tool_policy
        tools, refusal = tool_policy.worker_tools(c.tools, ROLE_TOOLS[c.role], c.role, options)
    except (ToolError, ValueError, TypeError) as exc:
        return {"error": str(exc)}
    return {"error": refusal} if refusal else {
        "role": c.role, "task": c.task, "tools": tools, "inputs": c.inputs, "outputs": c.outputs, "max_steps": c.max_steps,
        "max_eur": c.max_eur, "model": entry["litellm_params"]["model"], "write_folder": "output/workers/<assigned at start>/"}


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
    entry = worker_entry(ctx.options)
    if entry is None:
        raise ToolError("delegate is not configured")
    if ctx.options.get("permissions") != "full" and not ctx.options.get("delegation_approved"):
        raise ToolError("delegate needs Full access, or the user's approval of the whole contract: workers never ask")
    args = {"role": role, "task": task, "inputs": inputs, "tools": tools, "outputs": outputs, "max_steps": max_steps,
            "max_eur": max_eur, "summary_chars": summary_chars}
    contract = validate_contract(args, entry, secrets.token_hex(4))
    model = entry["litellm_params"]["model"]
    import tool_policy                            # local: tool_policy imports this module
    allowed, refusal = tool_policy.worker_tools(contract.tools, ROLE_TOOLS[contract.role], contract.role, ctx.options, ctx.chat_id)
    if refusal:
        raise ToolError(refusal)
    contract.tools = tuple(allowed)
    ledger = ctx.ledger
    ledger.limit_eur = ctx.options.get("budget_eur") or None
    if ledger.limit_eur and usage.step_cost_usd({"prompt": 1, "completion": 1, "cache_read": 0, "cache_write": 0},
                                                model_catalog.entry_profile(entry)["pricing"], model) is None:
        raise ToolError("delegate refused: the worker model has no known price, so it cannot run under a chat budget")
    limits = ctx.limits
    if limits.delegations >= settings.max_delegations():
        raise ToolError(f"delegation cap reached: at most {settings.max_delegations()} delegations per turn. "
                        "Do the remaining work yourself or finish.")
    limits.delegations += 1
    async with limits.semaphore():                # extra workers wait here until one of the running ones ends
        return await _run_worker(ctx, contract, entry, model)


@dataclass
class Verdict:
    """The app's own validation of a worker's declared LDraw outputs."""
    checked: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    skipped: str = ""                                     # why nothing was checked, if it was not


def _signature(root: Path) -> tuple:
    """Output files of a worker (generator scripts do not count) with size and mtime: changes when it writes one."""
    return tuple(sorted((p.relative_to(root).as_posix(), p.stat().st_size, p.stat().st_mtime_ns)
                        for p in root.rglob("*") if p.is_file() and p.relative_to(root).parts[0] != "generators"))


def _diagnostic_text(d: dict) -> str:
    line = f" (line {d['line_number']})" if d.get("line_number") else ""
    return f"{d.get('code', 'error')}{line}: {str(d.get('message', ''))[:200]}"


async def _gate(wctx: ToolContext, contract: Contract, root: Path) -> Verdict:
    """Validation run by the app, never trusted from the worker: toolkit `validate --geometry` and the parts check
    on every declared .ldr/.mpd/.dat output (the assembly profile, which demands a main `0 FILE` block, only for
    .mpd: a worker's .ldr is a submodel), JSON parsing for plan outputs. More than one connected group in a
    builder's output is a failure (the validator itself only reports it as a warning)."""
    verdict = Verdict()
    names = [o for o in contract.outputs if (root / o).is_file()]
    if not contract.outputs and contract.role == "builder":
        names = sorted(p.name for p in root.glob("*") if p.is_file())
    names = [n for n in names if n.lower().endswith(GATED + (".json",))]
    if not names:
        verdict.skipped = "no LDraw or plan output to check"
        return verdict
    for name in names:
        path = root / name
        verdict.checked.append(name)
        if name.lower().endswith(".json"):
            try:
                json.loads(path.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                verdict.problems.append(f"{name}: not valid JSON")
            continue
        try:
            run = await run_command(wctx, ["./ldraw-agent", "validate", str(path), "--profile", "assembly" if name.lower().endswith(".mpd") else "syntax",
                                           "--geometry", "--detail", "summary", "--contacts", "all"], 300)
        except Exception as exc:  # noqa: BLE001 - a broken validator is reported, not fatal
            verdict.problems.append(f"{name}: validation could not run ({exc.__class__.__name__})")
            continue
        if run.timed_out:
            verdict.problems.append(f"{name}: validation timed out (the model may be too large or too complex)")
            continue
        try:
            report = json.loads(run.stdout)
        except ValueError:
            report = None
        if not isinstance(report, dict):
            verdict.problems.append(f"{name}: validate produced no report (exit code {run.exit_code}): {run.stderr.strip()[:200]}")
            continue
        errors = [_diagnostic_text(d) for d in report.get("diagnostics") or [] if d.get("severity") == "error"]
        if report.get("error"):
            errors.append(str(report["error"])[:200])
        shown = errors[:MAX_ERRORS_SHOWN]
        if shown:
            more = f" (and {len(errors) - len(shown)} more)" if len(errors) > len(shown) else ""
            verdict.problems.append(f"{name}: " + "; ".join(shown) + more)
            continue
        groups = (report.get("geometry") or {}).get("optimistic_component_count")
        if contract.role == "builder" and isinstance(groups, int) and not isinstance(groups, bool) and groups > 1:
            verdict.problems.append(f"{name}: {groups} disconnected groups of parts; a submodel must be one connected "
                                    "structure (connect the groups or fix the offsets between them)")
        check, _note = await parts_check(wctx, path)
        if check and not check["complete"]:
            if check["mode"] == "only":
                verdict.problems.append(f"{name}: parts not owned: {check['summary']}"[:300])
            else:
                verdict.notes.append(f"{name}: parts the user does not own: {check['summary']}"[:300])
    return verdict


async def _run_worker(ctx: ToolContext, contract: Contract, entry: dict, model: str) -> ToolResult:
    ledger = ctx.ledger
    # Refusals have left no folder behind. Reserve last, so nothing between the reservation and the try below can raise.
    root = ctx.work_dir / "workers" / contract.id
    root.mkdir(parents=True, exist_ok=True)
    wctx = worker_context(ctx, root)
    if ctx.limits.guard is None:
        ctx.limits.guard = worker_isolation.Guard()
    guard = ctx.limits.guard
    cap_eur = ledger.reserve(contract.id, contract.max_eur, ctx.store.messages(ctx.chat_id))   # clamped to what is left
    guard.enter(ctx.work_dir)
    sink = FileSink(ctx.chat_dir / "workers" / f"{contract.id}.jsonl")
    sink.add({"role": "user", "content": _task_message(contract)})
    system = worker_prompt(contract)
    schemas = [TOOLS[name][0] for name in contract.tools if name in TOOLS]
    vision = llm_config.capabilities(entry)["vision"] is True
    reason, stuck = "", ""
    recent: deque = deque(maxlen=REPEAT_WINDOW)
    watch = {"sig": None, "idle": 0}

    def before_step(stored: list[dict]) -> bool:
        nonlocal reason, stuck
        if ledger.chat_exhausted(contract.id, ctx.store.messages(ctx.chat_id)):
            reason = "the chat budget was reached"
        elif ledger.spent_eur(contract.id) >= cap_eur:
            reason = f"the worker's budget of EUR {cap_eur:.2f} was reached"
        elif not stuck and contract.outputs and not all((root / o).is_file() for o in contract.outputs):
            sig = _signature(root)
            watch["idle"] = watch["idle"] + 1 if sig == watch["sig"] and watch["sig"] is not None else 0
            watch["sig"] = sig
            if watch["idle"] >= NO_WRITE_STEPS:
                stuck = f"it wrote no output file in {NO_WRITE_STEPS} model calls"
        return bool(reason or stuck)

    def build_messages(stored: list[dict]) -> list[dict]:
        sys_content = [{"type": "text", "text": system, "cache_control": cache_control(model)}] \
            if providers.supports_prompt_caching(model) else system
        history = llm_history(stored, vision, lambda ref: ctx.store.resolve(ctx.chat_id, ref), model, mark_turns=True)
        return with_rolling_breakpoint([{"role": "system", "content": sys_content}, *history], model)

    def record_usage(message: dict) -> None:
        if message.get("_usage"):
            ledger.add(contract.id, message["_usage"].get("cost_usd"))

    async def run_tool(call: dict) -> ToolResult:
        nonlocal stuck
        name = call["function"]["name"]
        if name not in contract.tools:
            return ToolResult(f"Error: tool {name!r} is not available to this worker (allowed: {', '.join(contract.tools)}).")
        out = await dispatch(wctx, name, call["function"]["arguments"])
        out.content = out.content[:RESULT_CHARS]
        out.models = []                           # only the planner publishes
        # The same call with the same arguments giving the same result again and again is a loop.
        recent.append((name, call["function"]["arguments"], hash(out.content)))
        if not stuck and recent.count(recent[-1]) >= REPEAT_CALLS:
            stuck = f"it repeated the same {name} call with the same result {REPEAT_CALLS} times"
        return out

    async def drive(steps: int) -> str:
        return await run_steps(entry=entry, params=params, sink=sink, tools=schemas, max_steps=steps,
                               build_messages=build_messages, run_tool=run_tool,
                               ref=lambda png: ctx.store.ref(ctx.chat_id, png), before_step=before_step,
                               after_message=record_usage)

    ended, error, finished, repaired = "", "", False, False
    strays: list[str] = []
    verdict = Verdict(skipped="the worker did not finish")
    try:
        params = await inference.params_for(entry, {"effort": worker_effort(entry)})
        ended = await drive(contract.max_steps)
        strays += guard.check()                   # before validating, so a stray write cannot pass for an output
        if ended == "done" and not reason and not stuck:
            verdict = await _gate(wctx, contract, root)
            if verdict.problems and not verdict.skipped:
                repaired = True
                recent.clear()
                watch.update(sig=None, idle=0)
                sink.add({"role": "user", "content": "The app validated your files and found problems. Fix them now "
                          "(edit the same files in your folder), run run_toolkit validate again, then reply. "
                          "Problems:\n" + "\n".join(verdict.problems)})
                ended = await drive(REPAIR_STEPS)
                strays += guard.check()
                if not reason and not stuck:
                    verdict = await _gate(wctx, contract, root)
                else:
                    verdict = Verdict(problems=verdict.problems + ["the repair round did not finish"])
        finished = True
    except asyncio.CancelledError:                # Stop cancels the planner task, and with it this worker
        guard.check()
        raise
    except Exception as exc:  # noqa: BLE001 - a worker failing must not end the planner's turn
        error, finished = exc.__class__.__name__, True
    finally:
        ledger.release(contract.id)
        if finished:
            strays += guard.check()
        guard.leave()
        if not finished:                                      # Stop or another abort: the worker's cost still counts
            ctx.store.add_message(ctx.chat_id, {"role": "assistant", "content": "", "_ui_only": True, "_hidden": True,
                                                **_aggregate(contract.id, model, sink, "cancelled")})
    final = next((m["content"] for m in reversed(sink.messages())
                  if m["role"] == "assistant" and m.get("content") and not m.get("tool_calls")), "")
    stray = worker_isolation.stray_line(strays, contract.dir)
    files = _files(ctx, contract)
    names = {path for path, _size in files}
    missing = [o for o in contract.outputs if f"{contract.dir}/{o}" not in names]
    if error:
        status = "failed"
    elif reason:
        status = "over_budget"
    elif stuck:
        status = "stuck"
    elif ended == "cap" and not repaired:
        status = "step_cap"
    elif missing or (not files and contract.role == "builder"):
        status = "partial" if files else "failed"
    elif verdict.problems:
        status = "partial"
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
    if stuck:
        lines.append(f"Stopped as stuck: {stuck}. Partial files are kept and listed below.")
    if missing:
        lines.append(f"Missing outputs: {', '.join(missing)}")
    if stray:
        lines.append(stray)
    lines.append("Summary: " + (final[:contract.summary_chars] or "(none)"))
    lines.append("Files: " + ("; ".join(f"{path} ({size} bytes)" for path, size in files) or "none"))
    lines.append(f"Cost: {totals['steps']} model calls, about EUR {totals['cost_eur']:.4f}")
    if verdict.problems:
        lines.append("Validation: NOT OK" + (" after one automatic repair round" if repaired else "") + ". Do not place this "
                     "submodel as it is. Problems: " + " | ".join(verdict.problems))
    elif verdict.skipped:
        lines.append(f"Validation: not run ({verdict.skipped}); check the files before using them.")
    else:
        lines.append("Validation: ok (run by the app: toolkit validate with geometry and one-connected-structure check on "
                     + ", ".join(verdict.checked) + ")" + (", after one automatic repair round" if repaired else ""))
    lines.extend(f"Note: {n}" for n in verdict.notes)
    return ToolResult("\n".join(lines), record=_aggregate(contract.id, model, sink, status))
