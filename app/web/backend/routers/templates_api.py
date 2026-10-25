"""Make this a template: any good build becomes a parametric starting point (docs/plans/active/build-templates, section 6).

    POST /api/builds/{id}/template/check              quality gate + role-coloured preview (with the person's role corrections)
    GET  /api/builds/{id}/template/preview.mpd        the role-coloured model the canvas shows
    POST /api/builds/{id}/template/jobs               derive in the background (own toolkit process; minutes to most of an hour)
    GET  /api/builds/{id}/template/jobs/{job}         progress, then the knobs found (with a picture of each extreme value)
    POST /api/builds/{id}/template/jobs/{job}/cancel
    POST /api/builds/{id}/template/jobs/{job}/save    name, words, knobs switched off -> <data>/templates/<id>.json
    GET/DELETE /api/templates                         the person's templates

The derivation itself is the toolkit's (ldraw_tools/templates/make.py, job.py), reached through the worker protocol.
This module only owns the files: the job folders under the build's work folder and the templates folder, which the
toolkit processes read and never write. Provenance is deliberately plain (`source`: the build, its version, author, licence).
"""
from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

import build_worker
import builds
import generator_bridge
import sandbox
import settings
import workspace
from paths import safe_join
from store import get_store
from .builds_api import _check, forget_generators
from .common import not_found
from .editor import _session, _view
from .workspace_api import _ensure_draft, _record

router = APIRouter()
TIMEOUT = 600
ID = r"^[a-z0-9][a-z0-9-]{0,59}$"
JOB = r"^[0-9a-f]{12}$"
_jobs: dict[str, "Job"] = {}


class Correction(BaseModel):
    section: str = Field(max_length=200)
    id: str = Field(max_length=100)
    role: str = Field(max_length=30)


class CheckBody(BaseModel):
    corrections: list[Correction] = Field(default_factory=list, max_length=5000)
    gate: bool = True                 # false when only the corrections changed: the geometry did not


class JobBody(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    kinds: list[str] = Field(default_factory=list, max_length=12)
    description: str = Field(default="", max_length=500)
    corrections: list[Correction] = Field(default_factory=list, max_length=5000)


class SaveBody(BaseModel):
    title: Optional[str] = Field(default=None, max_length=120)
    kinds: Optional[list[str]] = Field(default=None, max_length=12)
    description: Optional[str] = Field(default=None, max_length=500)
    disabled: list[str] = Field(default_factory=list, max_length=100)     # knobs switched off; nothing can be switched on


@dataclass
class Job:
    id: str
    build: str
    folder: Path
    started: float = field(default_factory=time.time)
    state: str = "running"            # running | done | failed | cancelled
    error: Optional[str] = None
    task: Optional[asyncio.Task] = None


# --- the toolkit ------------------------------------------------------------------------------------------------

async def _call(worker, payload: dict, timeout: float) -> dict:
    try:
        reply = await worker.request({"op": "template", **payload}, timeout=timeout)
    except build_worker.WorkerBusy as exc:
        raise HTTPException(429, str(exc)) from None
    except build_worker.WorkerTimeout as exc:
        raise HTTPException(504, str(exc)) from None
    except build_worker.WorkerError as exc:
        raise HTTPException(502, str(exc)) from None
    if reply.get("error"):
        raise HTTPException(422, reply["error"])
    return reply


def _folder(store, build_id: str) -> Path:
    folder = store.work_dir(store.build_key(build_id)) / "template"
    folder.mkdir(parents=True, exist_ok=True)
    sandbox.give_to_agent(folder)
    return folder


async def _plan(store, build_id: str) -> tuple[dict, dict]:
    """(the draft's plan, a plain description of where it comes from)."""
    await _ensure_draft(store, build_id)
    key = store.build_key(build_id)
    session = _session(store, key)
    if session is None:
        raise HTTPException(409, "This build has nothing in it yet")
    plan = session[0]
    record = builds.get(store, build_id) or {}
    draft = workspace.draft_summary(_record(store, build_id) or {}, _view(store, key))
    source = {"name": record.get("title") or build_id, "build": build_id, "version": draft["base_version"]}
    if draft["changes"]:
        source["draft_changes"] = draft["changes"]
    if plan.get("author"):
        source["author"] = str(plan["author"])[:500]
    if plan.get("license"):
        source["license"] = str(plan["license"])[:500]
    return plan, source


def _kinds_for(store, build_id: str) -> list[str]:
    """Words the template should answer to, from the build's title and the generator that started it (if any)."""
    record = builds.get(store, build_id) or {}
    words = [" ".join((record.get("title") or "").casefold().split())]
    generator = (record.get("source") or {}).get("generator")
    registry = generator_bridge.cached() or {}
    for entry in registry.get("entries", []):
        if generator and entry.get("id") == generator:
            words += entry.get("kinds", [])[:3]
    return list(dict.fromkeys(w for w in words if w))[:6]


# --- check and preview ------------------------------------------------------------------------------------------

@router.post("/api/builds/{build_id}/template/check")
async def template_check(build_id: str, body: Optional[CheckBody] = None):
    """Is this build sound enough to vary, and what role does every part have? The role-coloured model is served at
    preview.mpd; `corrections` (a part's section, id and role) are applied before the classifier fills in the rest."""
    store = get_store()
    _check(store, build_id)
    body = body or CheckBody()
    plan, source = await _plan(store, build_id)
    reply = await _call(build_worker.get_editor_worker(),
                        {"action": "check", "plan": plan, "hints": source["name"], "gate": body.gate,
                         "corrections": [c.model_dump() for c in body.corrections]}, TIMEOUT)
    out: dict[str, Any] = {"gate": reply.get("gate"), "source": source, "suggested_kinds": _kinds_for(store, build_id)}
    shown = reply.get("preview")
    if shown:
        mpd = shown.pop("mpd")
        folder = _folder(store, build_id)
        target = folder / "preview.mpd"
        target.write_text(mpd, encoding="utf-8")
        sandbox.give_to_agent(target)
        shown["url"] = f"/api/builds/{build_id}/template/preview.mpd?v={time.time_ns()}"
        out["preview"] = shown
    return out


@router.get("/api/builds/{build_id}/template/preview.mpd")
def template_preview_file(build_id: str):
    store = get_store()
    _check(store, build_id)
    path = store.work_dir(store.build_key(build_id)) / "template" / "preview.mpd"
    if not path.is_file():
        not_found("no role preview yet")
    return FileResponse(path, media_type="text/plain; charset=utf-8", headers={"Cache-Control": "no-cache"})


# --- the derivation job -----------------------------------------------------------------------------------------

def _read(path: Path) -> Optional[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _job(store, build_id: str, job_id: str) -> Job:
    _check(store, build_id)
    if not re.fullmatch(JOB, job_id):
        not_found("no such job")
    job = _jobs.get(job_id)
    if job is not None and job.build == build_id:
        return job
    folder = store.work_dir(store.build_key(build_id)) / "template" / "jobs" / job_id
    if not folder.is_dir():
        not_found("no such job")
    # a job from before a restart: finished if it left a result, interrupted otherwise
    done = (folder / "result.json").is_file()
    return Job(id=job_id, build=build_id, folder=folder, state="done" if done else "failed",
               error=None if done else "The server restarted while this was running. Start it again.")


async def _run(job: Job, payload: dict) -> None:
    try:
        await _call(build_worker.get_template_worker(), payload, build_worker.TEMPLATE_TIMEOUT)
        job.state = "done" if (job.folder / "result.json").is_file() else "failed"
        job.error = None if job.state == "done" else "The derivation finished without a result."
    except HTTPException as exc:
        if job.state != "cancelled":
            job.state, job.error = "failed", str(exc.detail)
    except asyncio.CancelledError:
        job.state = "cancelled"
        raise
    except Exception as exc:  # noqa: BLE001 - a job must always end in a state the page can show
        job.state, job.error = "failed", str(exc)


@router.post("/api/builds/{build_id}/template/jobs")
async def template_start(build_id: str, body: JobBody):
    store = get_store()
    _check(store, build_id)
    if any(j.build == build_id and j.state == "running" for j in _jobs.values()):
        raise HTTPException(409, "A template is already being made from this build.")
    if sum(j.state == "running" for j in _jobs.values()) >= 1:
        raise HTTPException(429, "Another template is being made; one at a time. Try again when it finishes.")
    plan, source = await _plan(store, build_id)
    folder = _folder(store, build_id)
    job_id = uuid.uuid4().hex[:12]
    jobdir = folder / "jobs" / job_id
    jobdir.mkdir(parents=True)
    sandbox.give_to_agent(folder / "jobs")
    sandbox.give_to_agent(jobdir)
    job = Job(id=job_id, build=build_id, folder=jobdir)
    kinds = [" ".join(k.casefold().split()) for k in body.kinds if k.strip()] or _kinds_for(store, build_id)
    payload = {"action": "make", "plan": plan, "jobdir": str(jobdir), "title": body.title.strip(), "kinds": kinds,
               "description": body.description.strip() or body.title.strip(), "hints": source["name"], "source": source,
               "corrections": [c.model_dump() for c in body.corrections]}
    _jobs[job_id] = job
    job.task = asyncio.create_task(_run(job, payload))
    return {"job": job_id, "state": job.state}


def _thumb_url(build_id: str, job_id: str, rel: str) -> str:
    return f"/api/builds/{build_id}/template/jobs/{job_id}/files/{rel}"


@router.get("/api/builds/{build_id}/template/jobs/{job_id}")
async def template_job(build_id: str, job_id: str):
    store = get_store()
    job = _job(store, build_id, job_id)
    out: dict[str, Any] = {"job": job.id, "state": job.state, "error": job.error,
                           "elapsed": round(time.time() - job.started, 1) if job.state == "running" else None,
                           "progress": _read(job.folder / "progress.json")}
    if job.state == "done":
        result = _read(job.folder / "result.json") or {}
        for knob in result.get("knobs", []):
            for extreme in knob.get("extremes", []):
                if extreme.get("thumb"):
                    extreme["thumb"] = _thumb_url(build_id, job.id, extreme["thumb"])
        template = result.get("template") or {}
        out["result"] = {"knobs": result.get("knobs", []), "rejected": result.get("rejected", []), "roles": result.get("roles", []),
                         "coverage": result.get("coverage", []), "quality": result.get("quality", ""),
                         "title": template.get("title"), "kinds": template.get("kinds", []),
                         "description": template.get("description", ""), "source": template.get("source", {}),
                         "report": result.get("report", {})}
    return out


@router.get("/api/builds/{build_id}/template/jobs/{job_id}/files/{rel:path}")
def template_job_file(build_id: str, job_id: str, rel: str):
    store = get_store()
    job = _job(store, build_id, job_id)
    path = safe_join(job.folder / "thumbs", rel)
    if path is None or path.suffix != ".png" or not path.is_file() or path.is_symlink():
        not_found()
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "private, max-age=3600"})


@router.post("/api/builds/{build_id}/template/jobs/{job_id}/cancel")
async def template_cancel(build_id: str, job_id: str):
    store = get_store()
    job = _job(store, build_id, job_id)
    if job.state == "running":
        job.state = "cancelled"
        await build_worker.get_template_worker().close()      # the derivation holds the process; it restarts on the next job
        if job.task is not None:
            job.task.cancel()
    return {"job": job.id, "state": job.state}


# --- saving and the person's templates --------------------------------------------------------------------------

def _stored() -> list[Path]:
    return sorted(settings.TEMPLATES_DIR.glob("*.json")) if settings.TEMPLATES_DIR.is_dir() else []


@router.post("/api/builds/{build_id}/template/jobs/{job_id}/save")
async def template_save(build_id: str, job_id: str, body: SaveBody):
    """Store the template for this person: the knobs they switched off are removed, nothing else can change. From then on
    it is offered next to the generators (the registry is rebuilt) and the fast path can answer with it."""
    store = get_store()
    job = _job(store, build_id, job_id)
    if job.state != "done":
        raise HTTPException(409, "The template is not finished yet.")
    reply = await _call(build_worker.get_editor_worker(),
                        {"action": "finalize", "jobdir": str(job.folder), "taken": [p.stem for p in _stored()],
                         "disabled": body.disabled, "title": body.title, "kinds": body.kinds, "description": body.description},
                        TIMEOUT)
    data = reply["template"]
    settings.TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    settings.TEMPLATES_DIR.chmod(0o755)               # the toolkit processes run as another user and only read it
    target = settings.TEMPLATES_DIR / f"{data['id']}.json"
    temporary = target.with_name(target.name + ".tmp")
    temporary.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    temporary.chmod(0o644)
    temporary.replace(target)
    forget_generators()
    generator_bridge.set_registry(None)
    generator_bridge.warm()
    return {"id": data["id"], "generator": f"template:{data['id']}", "title": data["title"], "kinds": data["kinds"],
            "coverage": data["coverage"]}


def _summary(path: Path) -> Optional[dict]:
    data = _read(path)
    if not data or data.get("id") != path.stem:
        return None
    return {"id": data["id"], "title": data.get("title"), "kinds": data.get("kinds", []), "families": data.get("families", []),
            "coverage": data.get("coverage", []), "source": data.get("source", {}), "parts": len(data.get("leaves", [])),
            "created_at": int(path.stat().st_mtime)}


@router.get("/api/templates")
def templates_list():
    return {"templates": [s for s in map(_summary, _stored()) if s]}


@router.delete("/api/templates/{template_id}")
async def templates_delete(template_id: str):
    if not re.fullmatch(ID, template_id):
        not_found("no such template")
    path = settings.TEMPLATES_DIR / f"{template_id}.json"
    if not path.is_file() or path.is_symlink():
        not_found("no such template")
    path.unlink()
    forget_generators()
    generator_bridge.set_registry(None)
    generator_bridge.warm()
    return {"deleted": template_id}
