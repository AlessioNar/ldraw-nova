"""The user's parts inventories (Rebrickable CSV uploads): list, upload, inspect, rename, delete."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import inventories
from inventories import InventoryError

router = APIRouter()
MAX_PAGE = 200


class NewInventory(BaseModel):
    name: str
    csv: str
    include_spares: Optional[bool] = False


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
        return await inventories.import_csv(body.name, body.csv, bool(body.include_spares))
    except InventoryError as exc:
        raise failure(exc) from None


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


@router.delete("/api/inventories/{inventory_id}")
def inventories_delete(inventory_id: str):
    try:
        inventories.delete(inventory_id)
    except InventoryError as exc:
        raise failure(exc) from None
    return {"ok": True}
