"""Stored chat messages -> the history sent to the LLM."""
from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Callable, Optional

import providers

KEEP_IMAGE_MESSAGES = 2               # only the newest renders are re-sent to vision models


def _image_data_url(path: Path) -> Optional[str]:
    if not path.is_file():
        return None
    return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode()


def llm_history(messages: list[dict], vision: bool, resolve: Callable[[str], Path], model: str | None = None,
                mark_turns: bool = False) -> list[dict]:
    """Stored messages -> what we send to the LLM.

    Drops UI-only notes and our "_" metadata, re-attaches only the newest
    renders for vision models, and repairs tool calls left without results
    (a stopped or crashed turn) so providers accept the history. `resolve`
    turns the image references stored in the chat into file paths.
    """
    image_msgs = [m for m in messages if m.get("_images_for_llm")]
    keep_images = {m["id"] for m in image_msgs[-KEEP_IMAGE_MESSAGES:]} if vision else set()

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
                out.append({"role": "tool", "tool_call_id": m["tool_call_id"], "content": m["content"]})
            continue
        close_pending()
        if m.get("_images_for_llm"):
            if m["id"] not in keep_images:
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
