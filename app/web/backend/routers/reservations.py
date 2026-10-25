"""Reservations ("I built this") and shortage lists for a model in My Models."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

import builds
import reservations
import settings
import workspace
from inventories import InventoryError
from leocad_render import MODEL_SUFFIXES

router = APIRouter()


class NewReservation(BaseModel):
    model: str
    chat: Optional[str] = None


class SetState(BaseModel):
    state: str


def failure(exc: InventoryError) -> HTTPException:
    return HTTPException(exc.status, str(exc))


def _model(filename: str) -> Path:
    """A direct, regular model file in data/generated; never a path, link or hidden file."""
    path = settings.GENERATED_DIR / filename
    if (Path(filename).name != filename or filename.startswith(".") or path.suffix.lower() not in MODEL_SUFFIXES
            or path.is_symlink() or not path.is_file()):
        raise HTTPException(404, "Model not found")
    return path


@router.get("/api/collection/reservations")
def reservations_list():
    return reservations.listing()


@router.post("/api/collection/reservations")
def reservations_create(body: NewReservation):
    """Marks a model as built: its parts are held back from the collection until released. The answer carries the
    shortage (what the model needs that was not free), so the button can show it."""
    path = _model(body.model)
    usage = workspace.file_usage(path)
    slug = builds.slug_of_file(path.name)
    try:
        record = reservations.reserve(path.name, usage, build=slug, chat=body.chat)
    except InventoryError as exc:
        raise failure(exc) from None
    return {"reservation": {k: v for k, v in record.items() if k != "rows"},
            "shortage": reservations.shortage(usage, exclude_build=slug)}


@router.delete("/api/collection/reservations/{reservation_id}")
def reservations_release(reservation_id: str):
    try:
        gone = reservations.release(reservation_id)
    except InventoryError as exc:
        raise failure(exc) from None
    return {"released": gone["id"], "model": gone.get("model")}


@router.put("/api/collection/sources/{source_id}/set-state")
def set_state(source_id: str, body: SetState):
    try:
        return reservations.set_state(source_id, body.state)
    except InventoryError as exc:
        raise failure(exc) from None


def _report(model: str, mode: str) -> dict:
    path = _model(model)
    try:
        return reservations.shortage(workspace.file_usage(path), mode, exclude_build=builds.slug_of_file(path.name))
    except InventoryError as exc:
        raise failure(exc) from None


@router.get("/api/collection/shortage")
def shortage(model: str, mode: str = "exact"):
    return {"model": model, **_report(model, mode)}


@router.get("/api/collection/shortage/export")
def shortage_export(model: str, format: str = "csv", mode: str = "exact"):
    try:
        body, media, ext = reservations.export_shortage(_report(model, mode), format)
    except InventoryError as exc:
        raise failure(exc) from None
    return Response(body, media_type=media, headers={"Content-Disposition": f'attachment; filename="{Path(model).stem}-wanted.{ext}"'})
