"""The agent turn loop: stream a completion, save it, run the tool calls, repeat."""
from __future__ import annotations

import asyncio
import logging

import litellm

import inference
import llm_config
import model_catalog
import settings
import providers
import usage
from store import ChatStore
from history import llm_history
from run_state import Run
from system_prompt import outbound_messages, system_message
from tool_policy import available_tools
from tools import ToolContext
from usage import step_cost_usd, usage_record

log = logging.getLogger("agent")

async def run_turn(store: ChatStore, run: Run, entry: dict, execute_tool) -> None:
    chat_id = run.chat_id
    caps = llm_config.capabilities(entry)
    vision = caps["vision"] is True
    use_tools = caps["tools"] is not False and run.options.get("mode") != "chat"
    ctx = ToolContext(chat_id=chat_id, store=store, emit=run.emit, options=run.options)

    def save(message: dict) -> int:
        msg_id = store.add_message(chat_id, message)
        run.emit("saved", {"message_id": msg_id})
        return msg_id

    try:
        params = await inference.params_for(entry, run.options)
        pricing = model_catalog.entry_profile(entry)["pricing"]
        budget = run.options.get("budget_eur")
        max_steps = settings.max_steps()
        for _step in range(max_steps):
            stored = store.messages(chat_id)
            if budget:
                spent = usage.usage_totals(stored)["cost_eur"]
                if spent >= budget:
                    save({"role": "assistant", "_ui_only": True, "_notice": True,
                          "content": f"Budget reached: about €{spent:.2f} of €{budget:.2f} spent in this chat. "
                                     "Raise the budget in the chat box and send a message to continue."})
                    break
            messages = [system_message(store, chat_id, entry["litellm_params"]["model"], run.options),
                        *llm_history(stored, vision, lambda ref: store.resolve(chat_id, ref), entry["litellm_params"]["model"], mark_turns=True)]
            budget = run.options.get("context_tokens") or model_catalog.profile(entry["litellm_params"]["model"])["context_window"]
            messages, removed = inference.bounded_history(messages, params["model"], budget)
            if removed:
                run.emit("context", {"removed": removed})
            messages = outbound_messages(store, chat_id, messages, entry["litellm_params"]["model"], run.options)
            kwargs = {"tools": available_tools(run.options, chat_id)} if use_tools else {}
            run.emit("progress", {"summary": "Agent is reviewing the request and choosing the next step."})
            stream = await litellm.acompletion(**params, messages=messages, stream=True, num_retries=2, **kwargs)
            inference.preserve_openrouter_reasoning_chunks(stream, params["model"])

            run.draft = ""
            chunks = []
            async for chunk in stream:
                chunks.append(chunk)
                delta = chunk.choices[0].delta if chunk.choices else None
                if delta is not None and getattr(delta, "content", None):
                    run.draft += delta.content
                    run.emit("text", {"delta": delta.content})

            full = litellm.stream_chunk_builder(chunks, messages=messages)
            reply = full.choices[0].message
            tool_calls = [
                {"id": tc.id, "type": "function",
                 "function": {"name": tc.function.name, "arguments": tc.function.arguments or "{}"}}
                for tc in (reply.tool_calls or [])
            ]
            # null (not "") content next to tool calls: some providers reject empty text blocks.
            message: dict = {"role": "assistant", "content": reply.content or (None if tool_calls else "")}
            message["_llm_model"] = entry["litellm_params"]["model"]
            step_usage = usage_record(full)
            if step_usage:
                cost = step_cost_usd(step_usage, pricing, entry["litellm_params"]["model"], settings.CACHE_TTL)
                if cost is not None:
                    step_usage["cost_usd"] = round(cost, 6)
                message["_usage"] = step_usage
                run.emit("usage", step_usage)
            if tool_calls:
                message["tool_calls"] = tool_calls
            if getattr(reply, "thinking_blocks", None):          # must round-trip for Anthropic thinking
                message["thinking_blocks"] = reply.thinking_blocks
            if getattr(reply, "provider_specific_fields", None):
                message["provider_specific_fields"] = reply.provider_specific_fields
            if getattr(reply, "reasoning_content", None):
                message["_reasoning"] = reply.reasoning_content
            if providers.of(message["_llm_model"]).reasoning_details:
                # LiteLLM 1.102.1 retains delta.reasoning_details but loses them
                # in stream_chunk_builder. OpenRouter specifies concatenation in
                # received order; never reorder or merge across id/index (coalescing is opt-in).
                details = [detail for chunk in chunks if chunk.choices
                           for detail in (getattr(chunk.choices[0].delta, "reasoning_details", None) or [])]
                if settings.COALESCE_REASONING:
                    details = inference.coalesce_reasoning_details(details)
                if details:
                    message["_reasoning_details"] = details
            if not message["content"] and not tool_calls:
                break                                              # nothing to save or do
            save(message)
            run.draft = ""
            if not tool_calls:
                break

            images: list[str] = []                             # relative to the chat folder
            for call in tool_calls:
                name = call["function"]["name"]
                run.tools_running[call["id"]] = {"id": call["id"], "name": name,
                                                 "arguments": call["function"]["arguments"]}
                run.emit("tool_start", run.tools_running[call["id"]])
                result = await execute_tool(run, ctx, call["id"], name, call["function"]["arguments"])
                run.tools_running.pop(call["id"], None)
                refs = [store.ref(chat_id, png) for png in result.images]
                save({"role": "tool", "tool_call_id": call["id"], "name": name, "content": result.content,
                      "_models": [m["id"] for m in result.models], "_images": refs,
                      **({"_parts_check": result.parts_check} if result.parts_check else {})})
                run.emit("tool_end", {"id": call["id"], "name": name})
                images += refs
            if images and vision:
                save({"role": "user", "content": "Renders produced by the tool calls above:",
                      "_images_for_llm": images, "_hidden": True})
        else:
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
