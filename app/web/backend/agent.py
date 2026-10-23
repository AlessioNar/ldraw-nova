"""Provider-agnostic agent loop on top of LiteLLM.

A turn runs as a background asyncio task per chat (so reloading the page
doesn't kill it). The chat's files (store.py) are the source of truth; live
events only carry what's in flight (streaming text, running tools) plus
"saved" pings telling the UI to refetch.

This module starts a turn and gates tool execution; the pieces live in
run_state.py (live run state), turn.py (the loop), history.py,
system_prompt.py, tool_policy.py and usage.py.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import replace
from typing import Optional

import litellm

import llm_config
import model_catalog
from run_state import Run, _runs, is_running
from store import ChatStore
from tool_policy import READ_TOOLS, available_tools
from tools import ToolContext, ToolResult, dispatch
from turn import run_turn

litellm.drop_params = False           # unsupported settings must fail visibly
litellm.suppress_debug_info = True


async def start_turn(store: ChatStore, chat_id: str, text: str, llm_model_id: Optional[str],
                     options: dict | None = None, images: list[str] | None = None, documents: list | None = None) -> None:
    if is_running(chat_id):
        raise RuntimeError("this chat is already running a turn")
    entry = llm_config.get(llm_model_id) if llm_model_id else None
    if entry is None:
        _entries, default_id = llm_config.builder_entries()
        entry = llm_config.get(default_id) if default_id else None
    if entry is None:
        raise ValueError("no LLM model configured — add one in Settings")
    if llm_config.unsupported(entry):
        raise ValueError(llm_config.UNSUPPORTED_MESSAGE)
    if (not model_catalog.builder_supported(entry["litellm_params"]["model"])
            or any(value is False for value in llm_config.capabilities(entry).values())):
        raise ValueError("Choose a model with tool calling and image input support in Settings")

    options = model_catalog.validate_options(entry, options)
    if images and llm_config.capabilities(entry)["vision"] is not True:
        raise ValueError("Select a model with image input support")

    chat = store.get_chat(chat_id)
    if chat["title"] == "New chat":
        title = " ".join(text.split())
        store.update_chat(chat_id, title=title[:60] + ("…" if len(title) > 60 else ""))
    store.update_chat(chat_id, llm_model_id=entry["id"], options=options)
    content = ([{"type": "text", "text": text}, *[{"type": "image_url", "image_url": {"url": u}} for u in images]]
               if images else text)
    from attachments import save_documents
    attached = save_documents(store, chat_id, documents or [])
    store.add_message(chat_id, {"role": "user", "content": content, **({"_documents": attached} if attached else {})})

    run = _runs.get(chat_id) or Run(chat_id)
    _runs[chat_id] = run
    run.draft, run.tools_running = "", {}
    run.options, run.approvals = options, {}
    run.started_at = run.last_event_at = time.time()
    run.phase = "Preparing the build workspace"
    run.task = asyncio.create_task(_run_turn(store, run, entry))


async def _run_turn(store: ChatStore, run: Run, entry: dict) -> None:
    await run_turn(store, run, entry, execute_tool)


async def execute_tool(run: Run, ctx: ToolContext, call_id: str, name: str, arguments: str) -> ToolResult:
    allowed = {t["function"]["name"] for t in available_tools(run.options, run.chat_id)}
    if name not in allowed:
        return ToolResult("Tool denied by the current mode or permissions.")
    if name not in READ_TOOLS and run.options.get("permissions", "ask") == "ask":
        # Independent approval IDs prevent replay between turns or duplicate provider call IDs.
        approval_id = uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        info = {"id": approval_id, "call_id": call_id, "name": name, "arguments": arguments}
        run.approvals[approval_id] = (info, future)
        run.emit("approval", info)
        try:
            approved = await asyncio.wait_for(future, 600)
        except asyncio.TimeoutError:
            approved = False
        finally:
            run.approvals.pop(approval_id, None)
            run.emit("approval_resolved", {"id": approval_id})
        if not approved:
            return ToolResult("Tool denied by the user or approval timed out. Do not retry without a new user instruction.")
    scoped = replace(ctx, emit=lambda event, data: run.emit(event, {**data, "id": call_id} if event == "tool_output" else data))
    return await dispatch(scoped, name, arguments)

