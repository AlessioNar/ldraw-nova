"""Prototype: delegate_task hands a bounded job to a cheap worker model (flag-gated, easy to delete).

The worker runs its own small loop in memory with a few tools. Only its per-step `_usage` records are saved in
the chat (hidden, `_subagent: true`), so usage_totals and the chat budget cover planner and workers together.
"""
from __future__ import annotations

import json

import litellm

import inference
import llm_config
import model_catalog
import settings
import usage
from tools import TOOLS, ToolContext, ToolError, ToolResult, dispatch

WORKER_TOOLS = ("list_files", "read_file", "write_file", "run_python", "run_shell", "run_toolkit")
DEFAULT_STEPS, MAX_STEPS, SUMMARY_CHARS, RESULT_CHARS = 12, 25, 1500, 8000
SYSTEM = ("You are a worker agent doing one bounded job for a planner. Use the tools, write files only under output/, "
          "and work efficiently. When done, reply with a short plain summary: what you did and which files you wrote. "
          "You cannot publish models or delegate further.")


def enabled() -> bool:
    return settings.subagents_enabled() and llm_config.get(settings.worker_llm_id()) is not None


def _budget_reached(ctx: ToolContext) -> bool:
    budget = ctx.options.get("budget_eur")
    return bool(budget) and usage.usage_totals(ctx.store.messages(ctx.chat_id))["cost_eur"] >= budget


def _written_path(call: dict) -> str | None:
    try:
        return json.loads(call["function"]["arguments"]).get("path")
    except (ValueError, AttributeError):
        return None


async def delegate_task(ctx: ToolContext, task: str, files: list[str] | None = None, max_steps: int = DEFAULT_STEPS) -> ToolResult:
    entry = llm_config.get(settings.worker_llm_id()) if settings.subagents_enabled() else None
    if entry is None:
        raise ToolError("delegate_task is not configured")
    max_steps = max(1, min(int(max_steps or DEFAULT_STEPS), MAX_STEPS))
    model = entry["litellm_params"]["model"]
    params = await inference.params_for(entry, {})
    pricing = model_catalog.entry_profile(entry)["pricing"]
    schemas = [TOOLS[name][0] for name in WORKER_TOOLS if name in TOOLS]
    prompt = task + (f"\n\nFiles to start from: {', '.join(files)}" if files else "")
    messages: list[dict] = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}]
    written: list[str] = []
    final, stopped = "", ""
    for _step in range(max_steps):
        if _budget_reached(ctx):
            stopped = "stopped because the chat budget was reached"
            break
        stream = await litellm.acompletion(**params, messages=messages, tools=schemas, stream=True, num_retries=2)
        chunks = [c async for c in stream]
        full = litellm.stream_chunk_builder(chunks, messages=messages)
        reply = full.choices[0].message
        calls = [{"id": tc.id, "type": "function",
                  "function": {"name": tc.function.name, "arguments": tc.function.arguments or "{}"}}
                 for tc in (reply.tool_calls or [])]
        record = usage.usage_record(full)
        if record:
            cost = usage.step_cost_usd(record, pricing, model, settings.CACHE_TTL)
            if cost is not None:
                record["cost_usd"] = round(cost, 6)
            ctx.store.add_message(ctx.chat_id, {"role": "assistant", "content": "", "_ui_only": True, "_hidden": True,
                                                "_subagent": True, "_usage": record})
        messages.append({"role": "assistant", "content": reply.content or (None if calls else ""),
                         **({"tool_calls": calls} if calls else {})})
        if not calls:
            final = reply.content or ""
            break
        for call in calls:
            name = call["function"]["name"]
            if name not in WORKER_TOOLS:
                out = f"Error: tool {name!r} is not available to workers."
            else:
                out = (await dispatch(ctx, name, call["function"]["arguments"])).content
                if name == "write_file" and not out.startswith("Error") and (path := _written_path(call)) and path not in written:
                    written.append(path)
                out = out[:RESULT_CHARS]
            messages.append({"role": "tool", "tool_call_id": call["id"], "content": out})
    else:
        stopped = f"stopped at the step cap ({max_steps} steps)"
    status = f"Worker {stopped}." if stopped else "Worker finished."
    summary = f"{status} Files written: {', '.join(written) or 'none'}. {final}"
    return ToolResult(summary[:SUMMARY_CHARS])
