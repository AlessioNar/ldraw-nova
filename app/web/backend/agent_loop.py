"""The per-step agent loop shared by the planner (turn.py) and delegated workers (subagents.py).

One step: build the request, stream the completion, store the assistant message, run its tool calls and
store their results. The caller chooses where messages go (a sink), which tools are offered, the step cap,
how the request is assembled and what runs before each step and after each model reply.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Awaitable, Callable

import litellm

import inference
import llm_config
import model_catalog
import providers
import settings
from usage import step_cost_usd, usage_record


class ChatSink:
    """The planner's message sink: the chat store (messages.jsonl), announcing each save to the UI."""

    def __init__(self, store, chat_id: str, run=None):
        self.store, self.chat_id, self.run = store, chat_id, run

    def messages(self) -> list[dict]:
        return self.store.messages(self.chat_id)

    def add(self, message: dict) -> int:
        msg_id = self.store.add_message(self.chat_id, message)
        if self.run is not None:
            self.run.emit("saved", {"message_id": msg_id})
        return msg_id


class FileSink:
    """A worker's message sink: a jsonl side file that is never part of the chat's own history."""

    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._items: list[dict] = []

    def messages(self) -> list[dict]:
        return list(self._items)

    def add(self, message: dict) -> int:
        record = {"id": len(self._items) + 1, **message}
        self._items.append(record)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        return record["id"]


async def run_steps(*, entry: dict, params: dict, sink, tools: list[dict] | None, max_steps: int,
                    build_messages: Callable[[list[dict]], list[dict]],
                    run_tool: Callable[[dict], Awaitable],
                    ref: Callable[[Path], str],
                    before_step: Callable[[list[dict]], bool] | None = None,
                    after_message: Callable[[dict], None] | None = None,
                    parallel_tools: frozenset[str] = frozenset(),
                    run=None) -> str:
    """Run up to `max_steps` model calls. Returns why it ended: "done" (the model answered without tool calls,
    or produced nothing), "stopped" (before_step asked to stop) or "cap" (the step cap was reached).

    `tools` None sends no tool list. Consecutive calls to a tool named in `parallel_tools` run concurrently, every
    other call runs alone and in order, and results are saved in call order. `run` (the planner's Run) receives live events, the streaming draft and
    running-tool state; workers pass None and stay silent.
    """
    model = entry["litellm_params"]["model"]
    vision = llm_config.capabilities(entry)["vision"] is True
    pricing = model_catalog.entry_profile(entry)["pricing"]
    emit = run.emit if run is not None else (lambda *_: None)
    for _step in range(max_steps):
        stored = sink.messages()
        if before_step is not None and before_step(stored):
            return "stopped"
        messages = build_messages(stored)
        kwargs = {"tools": tools} if tools is not None else {}
        stream = await litellm.acompletion(**params, messages=messages, stream=True, num_retries=2, **kwargs)
        inference.preserve_openrouter_reasoning_chunks(stream, params["model"])

        if run is not None:
            run.draft = ""
        chunks = []
        async for chunk in stream:
            chunks.append(chunk)
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta is not None and getattr(delta, "content", None):
                if run is not None:
                    run.draft += delta.content
                emit("text", {"delta": delta.content})

        full = litellm.stream_chunk_builder(chunks, messages=messages)
        reply = full.choices[0].message
        tool_calls = [
            {"id": tc.id, "type": "function",
             "function": {"name": tc.function.name, "arguments": tc.function.arguments or "{}"}}
            for tc in (reply.tool_calls or [])
        ]
        # null (not "") content next to tool calls: some providers reject empty text blocks.
        message: dict = {"role": "assistant", "content": reply.content or (None if tool_calls else "")}
        message["_llm_model"] = model
        step_usage = usage_record(full)
        if step_usage:
            cost = step_cost_usd(step_usage, pricing, model, settings.CACHE_TTL)
            if cost is not None:
                step_usage["cost_usd"] = round(cost, 6)
            message["_usage"] = step_usage
            emit("usage", step_usage)
        if tool_calls:
            message["tool_calls"] = tool_calls
        if getattr(reply, "thinking_blocks", None):          # must round-trip for Anthropic thinking
            message["thinking_blocks"] = reply.thinking_blocks
        if getattr(reply, "provider_specific_fields", None):
            message["provider_specific_fields"] = reply.provider_specific_fields
        if getattr(reply, "reasoning_content", None):
            message["_reasoning"] = reply.reasoning_content
        if providers.of(model).reasoning_details:
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
            return "done"                                          # nothing to save or do
        if after_message is not None:
            after_message(message)
        sink.add(message)
        if run is not None:
            run.draft = ""
        if not tool_calls:
            return "done"

        images: list[str] = []                             # relative to the chat folder

        async def one(call: dict):
            if run is not None:
                run.tools_running[call["id"]] = {"id": call["id"], "name": call["function"]["name"],
                                                 "arguments": call["function"]["arguments"]}
                emit("tool_start", run.tools_running[call["id"]])
            try:
                return await run_tool(call)
            finally:
                if run is not None:
                    run.tools_running.pop(call["id"], None)

        def save(call: dict, result) -> list[str]:
            refs = [ref(png) for png in result.images]
            sink.add({"role": "tool", "tool_call_id": call["id"], "name": call["function"]["name"],
                      "content": result.content, "_models": [m["id"] for m in result.models], "_images": refs,
                      **({"_parts_check": result.parts_check} if result.parts_check else {}), **result.record})
            emit("tool_end", {"id": call["id"], "name": call["function"]["name"]})
            return refs

        i = 0
        while i < len(tool_calls):
            j = i + 1
            if tool_calls[i]["function"]["name"] in parallel_tools:
                while j < len(tool_calls) and tool_calls[j]["function"]["name"] in parallel_tools:
                    j += 1
            batch = tool_calls[i:j]
            if len(batch) == 1:
                results = [await one(batch[0])]
            else:
                tasks = [asyncio.ensure_future(one(call)) for call in batch]
                try:
                    results = await asyncio.gather(*tasks)
                except BaseException:                   # Stop (or a crash): cancel every sibling, let each finish its cleanup
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                    raise
            for call, result in zip(batch, results):    # saved in call order, whatever order they finished in
                images += save(call, result)
            i = j
        if images and vision:
            sink.add({"role": "user", "content": "Renders produced by the tool calls above:",
                      "_images_for_llm": images, "_hidden": True})
    return "cap"
