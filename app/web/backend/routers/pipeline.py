"""POST /api/chats/{id}/build: plan (or a named vehicle) to MPD, checks and GLB preview, through the resident worker.

Outputs land in the chat's work folder (data/output/<chat-id>/, agent-only like every agent file); the content-hash
cache sits beside them in .pipeline-cache/. The reply lists paths relative to that folder.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import build_worker
import sandbox
from routers.common import not_found
from store import get_store

router = APIRouter()
VIEWS = ("home", "front", "back", "left", "right", "top", "bottom")


class BuildRequest(BaseModel):
    plan: Optional[dict[str, Any]] = None
    vehicle: Optional[str] = Field(default=None, max_length=60)
    palette: Optional[str] = Field(default=None, max_length=60)
    name: str = Field(default="model", pattern=r"^[A-Za-z0-9_-]{1,60}$")
    geometry: bool = True
    glb: bool = True
    renders: list[str] = Field(default_factory=list, max_length=len(VIEWS))
    force: bool = False


@router.post("/api/chats/{chat_id}/build")
async def build(chat_id: str, body: BuildRequest):
    store = get_store()
    if not store.valid_id(chat_id) or not store.chat_dir(chat_id).is_dir():
        not_found("chat not found")
    if (body.plan is None) == (body.vehicle is None):
        raise HTTPException(status_code=422, detail="give exactly one of plan or vehicle")
    if any(view not in VIEWS for view in body.renders):
        raise HTTPException(status_code=422, detail=f"views must be among {', '.join(VIEWS)}")
    outdir = store.work_dir(chat_id)
    outdir.mkdir(parents=True, exist_ok=True)
    sandbox.give_to_agent(outdir)
    payload = {"op": "build", "outdir": str(outdir), **body.model_dump(exclude_none=True)}
    try:
        reply = await build_worker.get_worker().request(payload)
    except build_worker.WorkerBusy as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from None
    except build_worker.WorkerTimeout as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from None
    except build_worker.WorkerError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None
    if reply.get("error"):
        raise HTTPException(status_code=422, detail=reply["error"])
    root = str(outdir) + "/"
    reply["paths"] = {k: v.removeprefix(root) for k, v in reply.get("paths", {}).items()}
    return reply
