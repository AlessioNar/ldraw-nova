"""One owned set with its parts list and what is on hand or missing (the set detail page)."""
from fastapi import APIRouter, HTTPException

import set_detail
from inventories import InventoryError

router = APIRouter()


@router.get("/api/sets/{source_id}")
def set_get(source_id: str):
    try:
        return set_detail.detail(source_id)
    except InventoryError as exc:
        raise HTTPException(exc.status, str(exc)) from None
