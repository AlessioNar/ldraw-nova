"""The user's parts inventories (Rebrickable CSV uploads): list, upload, inspect, rename, delete."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import buildable
import inventories
from inventories import InventoryError

router = APIRouter()
MAX_PAGE = 200


class NewInventory(BaseModel):
    name: str
    csv: str
    include_spares: Optional[bool] = False
    family_colours: Optional[dict] = None    # Pileometer: {"Grays": 72}


class FamilyColours(BaseModel):
    family_colours: dict


class InventoryPatch(BaseModel):
    name: str


def failure(exc: InventoryError) -> HTTPException:
    return HTTPException(exc.status, str(exc))


@router.get("/api/inventories")
def inventories_list():
    return {"inventories": inventories.list_inventories()}


@router.post("/api/inventories")
async def inventories_create(body: NewInventory):
    try:
        return await inventories.import_csv(body.name, body.csv, bool(body.include_spares), body.family_colours)
    except InventoryError as exc:
        raise failure(exc) from None


# Declared before /api/inventories/{inventory_id}, which would otherwise take "buildable" as an id.
@router.get("/api/inventories/buildable/status")
def buildable_status():
    return buildable.status()


@router.post("/api/inventories/buildable/warm")
async def buildable_warm():
    return buildable.warm()


@router.get("/api/inventories/buildable")
async def buildable_list(ids: str = "", limit: int = buildable.DEFAULT_LIMIT, min_parts: int = Query(buildable.DEFAULT_MIN_PARTS, ge=0),
                         max_missing_share: Optional[float] = Query(None, ge=0, le=1), query: str = "", substitute: bool = False):
    try:
        report = await buildable.ranked(buildable.clean_ids(ids), limit=limit, min_parts=min_parts,
                                        max_missing_share=max_missing_share, query=query, substitute=substitute)
    except InventoryError as exc:
        raise failure(exc) from None
    if report is None:
        return JSONResponse(buildable.status(), status_code=202)
    return {**report, "status": buildable.status()}


@router.get("/api/inventories/{inventory_id}")
def inventories_get(inventory_id: str, query: str = "", colour: Optional[int] = None, limit: int = 50, offset: int = 0):
    try:
        return inventories.detail(inventory_id, query, colour, max(1, min(limit, MAX_PAGE)), max(0, offset))
    except InventoryError as exc:
        raise failure(exc) from None


@router.patch("/api/inventories/{inventory_id}")
def inventories_rename(inventory_id: str, body: InventoryPatch):
    try:
        return {"inventory": inventories.rename(inventory_id, body.name)}
    except InventoryError as exc:
        raise failure(exc) from None


@router.put("/api/inventories/{inventory_id}/family-colours")
async def inventories_family_colours(inventory_id: str, body: FamilyColours):
    """Maps a Pileometer import's colour families again from the stored CSV, replacing the inventory."""
    try:
        name, csv_text = inventories.remap_families(inventory_id)
        return await inventories.import_csv(name, csv_text, False, body.family_colours, replace=inventory_id)
    except InventoryError as exc:
        raise failure(exc) from None


@router.delete("/api/inventories/{inventory_id}")
def inventories_delete(inventory_id: str):
    try:
        inventories.delete(inventory_id)
    except InventoryError as exc:
        raise failure(exc) from None
    return {"ok": True}
