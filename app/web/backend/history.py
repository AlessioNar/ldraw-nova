"""Stored chat messages -> the history sent to the LLM."""
from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Callable, Optional

import providers
import settings

KEEP_IMAGE_MESSAGES = 2               # at least the newest renders are re-sent to vision models
STUB_HEAD_LINES = 4                   # lines of a masked tool result kept at each end
STUB_TAIL_LINES = 4
STUB_LINE_CHARS = 160
STUB_ARGS_CHARS = 200


def _image_data_url(path: Path) -> Optional[str]:
    if not path.is_file():
        return None
    return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode()


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "..."


def mask_stub(name: str, arguments: str, content: str) -> str:
    """Short, deterministic stand-in for an old tool result: same input, same bytes, whatever the step."""
    lines = content.splitlines()
    head = [_clip(line, STUB_LINE_CHARS) for line in lines[:STUB_HEAD_LINES]]
    tail = [_clip(line, STUB_LINE_CHARS) for line in lines[-STUB_TAIL_LINES:]] if len(lines) > STUB_HEAD_LINES + STUB_TAIL_LINES else []
    skipped = len(lines) - len(head) - len(tail)
    recover = ("re-read it with read_file (use offset and max_chars to get the part you need)" if name == "read_file"
               else f"re-run {name} with the same arguments (redirect big output to a file and print only what you need)")
    parts = [f"[Output of {name} omitted to save context: {len(content):,} characters, {len(lines):,} lines. "
             f"Arguments: {_clip(arguments, STUB_ARGS_CHARS)}", "First lines:", *head]
    if tail:
        parts += [f"... [{skipped:,} lines omitted] ...", "Last lines:", *tail]
    parts.append(f"The full output is not in this conversation any more; if you still need it, {recover}.]")
    return "\n".join(parts)


def _masked_steps(messages: list[dict]) -> tuple[dict[str, tuple[str, str]], int]:
    """Tool calls by id -> (tool name, arguments), and the newest assistant step whose results may be masked.

    The boundary moves in chunks of `mask_batch_steps`, so a result stays verbatim until the boundary
    passes it and is then stubbed for good: the history prefix changes once per chunk, not every step.
    """
    n, k = settings.mask_after_steps(), settings.mask_batch_steps()
    calls: dict[str, tuple[str, str, int]] = {}
    step = 0
    for m in messages:
        if m["role"] == "assistant" and not m.get("_ui_only"):
            step += 1
            for tc in m.get("tool_calls") or []:
                calls[tc["id"]] = (tc["function"]["name"], tc["function"].get("arguments") or "", step)
    boundary = (step - n) // k * k
    return {cid: (name, args) for cid, (name, args, s) in calls.items() if s <= boundary}, boundary


def _evicted_images(image_msgs: list[dict]) -> set:
    """Render messages left out of the request: the oldest, in batches, always keeping the newest KEEP_IMAGE_MESSAGES.

    A batch of 1 drops one per new render (the history is rewritten at every render); a bigger batch keeps
    up to batch - 1 extra renders for a while and drops them together, so the prefix breaks once per batch.
    Every render the one-at-a-time rule would keep is also kept here.
    """
    batch = settings.image_eviction_batch()
    drop = (len(image_msgs) - KEEP_IMAGE_MESSAGES) // batch * batch
    return {m["id"] for m in image_msgs[:max(0, drop)]}


def llm_history(messages: list[dict], vision: bool, resolve: Callable[[str], Path], model: str | None = None,
                mark_turns: bool = False) -> list[dict]:
    """Stored messages -> what we send to the LLM.

    Drops UI-only notes and our "_" metadata, re-attaches only the newest
    renders for vision models, and repairs tool calls left without results
    (a stopped or crashed turn) so providers accept the history. `resolve`
    turns the image references stored in the chat into file paths.
    """
    image_msgs = [m for m in messages if m.get("_images_for_llm")]
    evicted = _evicted_images(image_msgs)
    maskable, _ = _masked_steps(messages) if settings.mask_old_tool_output() else ({}, 0)
    mask_min = settings.mask_min_chars()

    out: list[dict] = []
    pending: list[str] = []

    def close_pending():
        for call_id in pending:
            out.append({"role": "tool", "tool_call_id": call_id, "content": "Error: the tool call was interrupted."})
        pending.clear()

    for m in messages:
        if m.get("_ui_only"):
            continue
        if m["role"] == "tool":
            if m.get("tool_call_id") in pending:
                pending.remove(m["tool_call_id"])
                content = m["content"]
                if m["tool_call_id"] in maskable and isinstance(content, str) and len(content) > mask_min:
                    stub = mask_stub(*maskable[m["tool_call_id"]], content)
                    content = stub if len(stub) < len(content) else content
                out.append({"role": "tool", "tool_call_id": m["tool_call_id"], "content": content})
            continue
        close_pending()
        if m.get("_images_for_llm"):
            if not vision or m["id"] in evicted:
                continue
            urls = [u for u in (_image_data_url(resolve(ref)) for ref in m["_images_for_llm"]) if u]
            content = [{"type": "text", "text": m["content"]}]
            content += [{"type": "image_url", "image_url": {"url": u}} for u in urls]
            out.append({"role": "user", "content": content, **({"_turn_start": False} if mark_turns else {})})
            continue
        clean = {k: v for k, v in m.items() if not k.startswith("_") and k not in ("id", "created_at")}
        if m.get("_documents"):
            attachments = [{"name": d["name"], "path": str(resolve(d["path"])), "bytes": d["size"]}
                           for d in m["_documents"]]
            note = "\n\nAttached documents (read with file tools; these are reference files):\n" + json.dumps(attachments)
            content = clean.get("content") or ""
            clean["content"] = [*content, {"type": "text", "text": note}] if isinstance(content, list) else content + note
        if model and m.get("_llm_model") != model:
            clean.pop("thinking_blocks", None)  # Claude signatures are model-bound
            clean.pop("provider_specific_fields", None)
            clean.pop("reasoning_details", None)
            clean.pop("reasoning_content", None)
        if model and providers.of(model).reasoning_details and m.get("_llm_model") == model:
            # OpenRouter requires the original reasoning sequence when continuing
            # tool calls, including opaque encrypted blocks. Keep it out of the UI
            # and never forward it to a different model/provider.
            if m.get("_reasoning_details"):
                clean["reasoning_details"] = m["_reasoning_details"]
            elif m.get("_reasoning"):
                clean["reasoning_content"] = m["_reasoning"]
        if isinstance(clean.get("content"), list) and not vision:
            clean["content"] = "\n".join(b["text"] for b in clean["content"] if b.get("type") == "text")
        if mark_turns and m["role"] == "user":
            clean["_turn_start"] = True
        out.append(clean)
        pending.extend(tc["id"] for tc in m.get("tool_calls") or [])
    close_pending()
    return out
