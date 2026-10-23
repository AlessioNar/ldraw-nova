"""The agent turn loop: stream a completion, save it, run the tool calls, repeat."""
from __future__ import annotations

import asyncio
import logging

import inference
import llm_config
import model_catalog
import settings
from store import ChatStore
from history import llm_history
from run_state import Run
from system_prompt import outbound_messages, system_message
from tool_policy import available_tools
from tools import ToolContext
from agent_loop import ChatSink, run_steps

log = logging.getLogger("agent")

async def run_turn(store: ChatStore, run: Run, entry: dict, execute_tool) -> None:
    chat_id = run.chat_id
    caps = llm_config.capabilities(entry)
    vision = caps["vision"] is True
    use_tools = caps["tools"] is not False and run.options.get("mode") != "chat"
    ctx = ToolContext(chat_id=chat_id, store=store, emit=run.emit, options=run.options, ledger=run.ledger)

    sink = ChatSink(store, chat_id, run)

    def save(message: dict) -> int:
        return sink.add(message)

    def before_step(stored: list[dict]) -> bool:
        budget = run.options.get("budget_eur")
        if not budget:
            return False
        spent = run.ledger.planner_spent_eur(stored)          # stored cost plus worker cost not yet saved
        if spent < budget:
            return False
        save({"role": "assistant", "_ui_only": True, "_notice": True,
              "content": f"Budget reached: about €{spent:.2f} of €{budget:.2f} spent in this chat. "
                         "Raise the budget in the chat box and send a message to continue."})
        return True

    def build_messages(stored: list[dict]) -> list[dict]:
        model = entry["litellm_params"]["model"]
        messages = [system_message(store, chat_id, model, run.options),
                    *llm_history(stored, vision, lambda ref: store.resolve(chat_id, ref), model, mark_turns=True)]
        context = run.options.get("context_tokens") or model_catalog.profile(model)["context_window"]
        messages, removed = inference.bounded_history(messages, params["model"], context)
        if removed:
            run.emit("context", {"removed": removed})
        return outbound_messages(store, chat_id, messages, model, run.options)

    async def run_tool(call: dict):
        return await execute_tool(run, ctx, call["id"], call["function"]["name"], call["function"]["arguments"])

    try:
        params = await inference.params_for(entry, run.options)
        max_steps = settings.max_steps()
        ended = await run_steps(
            entry=entry, params=params, sink=sink, tools=available_tools(run.options, chat_id) if use_tools else None,
            max_steps=max_steps, build_messages=build_messages, run_tool=run_tool,
            ref=lambda png: store.ref(chat_id, png), before_step=before_step, run=run)
        if ended == "cap":
            save({"role": "assistant", "_ui_only": True, "_notice": True,
                  "content": f"Stopped after {max_steps} steps. Send a message to continue."})
    except asyncio.CancelledError:
        if run.draft:
            save({"role": "assistant", "content": run.draft})
        save({"role": "assistant", "_ui_only": True, "_notice": True, "content": "Stopped."})
        raise
    except Exception as exc:  # noqa: BLE001 - provider/auth/network errors end the turn, not the chat
        log.warning("turn failed for chat %s: %s", chat_id, exc.__class__.__name__)
        message = str(exc) if isinstance(exc, ValueError) else f"{exc.__class__.__name__}: Provider request failed. Check login, model access and settings."
        save({"role": "assistant", "_ui_only": True, "_error": True, "content": message})
        run.emit("turn_error", {"message": message})   # not "error": EventSource reserves it
    finally:
        run.draft, run.tools_running = "", {}
        for _, future in run.approvals.values():
            if not future.done():
                future.cancel()
        run.approvals.clear()
        run.emit("done", {})
