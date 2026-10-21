"""Chat routes: history, turns, approvals, the SSE stream and chat artifact downloads."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

import agent
import gallery
import parts_steering
import run_state
import usage
from attachments import validate_documents, validate_images
from paths import safe_join
from store import get_store
from .common import chat_models, file_url, not_found

router = APIRouter()


class NewChat(BaseModel):
    llm_model_id: Optional[str] = None


class ChatPatch(BaseModel):
    title: Optional[str] = None
    llm_model_id: Optional[str] = None


class NewMessage(BaseModel):
    text: str = Field(max_length=200_000)
    llm_model_id: Optional[str] = None
    options: dict[str, Any] = Field(default_factory=dict)
    images: list[str] = Field(default_factory=list, max_length=4)
    documents: list[dict[str, Any]] = Field(default_factory=list, max_length=4)


@router.get("/api/chats")
def chats_list():
    store = get_store()
    chats = store.list_chats()
    for chat in chats:
        chat["running"] = run_state.is_running(chat["id"])
        chat["models"] = list(chat_models(store, chat["id"]).values())
    return {"chats": chats}


@router.post("/api/chats")
def chats_create(body: NewChat):
    return get_store().create_chat(llm_model_id=body.llm_model_id)


@router.get("/api/chats/{chat_id}")
async def chats_get(chat_id: str):
    store = get_store()
    chat = store.get_chat(chat_id) or not_found("no such chat")
    messages = store.messages(chat_id)
    for m in messages:
        m.pop("_reasoning_details", None)  # opaque provider state belongs only in server-side history
        if m.get("_images"):
            m["_image_urls"] = [u for u in (file_url(store.resolve(chat_id, r), versioned=True) for r in m["_images"]) if u]
        if m.get("_documents"):
            for document in m["_documents"]:
                path = store.resolve(chat_id, document["path"])
                try:
                    relative = path.relative_to(store.work_dir(chat_id))
                    document["url"] = f"/api/chats/{chat_id}/artifacts/" + quote(relative.as_posix())
                except ValueError:
                    document["url"] = None
    parts_steering.attach_parts_checks(messages)
    models = chat_models(store, chat_id)
    gallery.ensure_artifacts(store.resolve(chat_id, ref["model"]) for ref in store.models(chat_id)
                             if store.resolve(chat_id, ref["model"]).is_file())
    return {"chat": {**chat, "running": run_state.is_running(chat_id)}, "messages": messages, "models": models,
            "usage": usage.usage_totals(messages)}


@router.patch("/api/chats/{chat_id}")
def chats_patch(chat_id: str, body: ChatPatch):
    store = get_store()
    store.get_chat(chat_id) or not_found("no such chat")
    store.update_chat(chat_id, **body.model_dump(exclude_none=True))
    return store.get_chat(chat_id)


@router.delete("/api/chats/{chat_id}")
async def chats_delete(chat_id: str):
    """Deletes data/chats/<id> and its work folder data/output/<id>. The models
    it produced stay in data/generated (they're part of the collection)."""
    store = get_store()
    store.get_chat(chat_id) or not_found("no such chat")
    await run_state.cancel(chat_id)
    store.delete_chat(chat_id)
    return {"deleted": True}


@router.post("/api/chats/{chat_id}/messages", status_code=202)
async def chats_send(chat_id: str, body: NewMessage):
    store = get_store()
    store.get_chat(chat_id) or not_found("no such chat")
    if not body.text.strip():
        raise HTTPException(400, "empty message")
    try:
        images = validate_images(body.images)
        documents = validate_documents(body.documents, images)
        await agent.start_turn(store, chat_id, body.text, body.llm_model_id, body.options, images, documents)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    return {"started": True}


class ApprovalDecision(BaseModel):
    approved: bool


@router.post("/api/chats/{chat_id}/approvals/{approval_id}")
async def chats_approve(chat_id: str, approval_id: str, body: ApprovalDecision):
    if not run_state.decide(chat_id, approval_id, body.approved):
        raise HTTPException(409, "This approval is no longer pending")
    return {"accepted": True}


@router.post("/api/chats/{chat_id}/cancel")
async def chats_cancel(chat_id: str):
    return {"cancelled": await run_state.cancel(chat_id)}


@router.get("/api/chats/{chat_id}/stream")
async def chats_stream(chat_id: str):
    async def events():
        async for event, data in run_state.subscribe(chat_id):
            yield f"event: {event}\ndata: {json.dumps(data)}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/api/chats/{chat_id}/artifacts/{path:path}")
def chat_artifact(chat_id: str, path: str):
    """Explicit downloads from one chat's output; never expose runtime/config.

    HTML catalogs are downloadable, but are not executed with app privileges.
    """
    store = get_store()
    store.get_chat(chat_id) or not_found("no such chat")
    if any(part.startswith(".") for part in Path(path).parts):
        not_found()
    file = safe_join(store.work_dir(chat_id), path)
    if not file or not file.is_file():
        not_found()
    return FileResponse(file, filename=file.name, headers={
        "Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "sandbox; default-src 'none'",
    })
