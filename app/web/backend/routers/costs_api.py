"""GET /api/costs: spend per day and kind from the cost history (for a future cost-history page), and the
request-log text switch. Both need the database; without it they answer 503."""
from __future__ import annotations

import asyncio
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError

import db
import request_log
import usage_log

router = APIRouter()
UNAVAILABLE = "The database is not available"


@router.get("/api/costs")
async def costs(date_from: Optional[str] = Query(default=None, alias="from", max_length=10),
                date_to: Optional[str] = Query(default=None, alias="to", max_length=10)):
    """Spend per UTC day and kind (calls, USD, EUR at today's rate, unpriced calls), inclusive dates, plus totals."""
    if db.get_engine() is None:
        raise HTTPException(503, UNAVAILABLE)
    try:
        return await asyncio.to_thread(usage_log.costs, date_from, date_to)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    except SQLAlchemyError:
        raise HTTPException(503, UNAVAILABLE) from None


class RequestLogSettings(BaseModel):
    keep_request_text: bool


@router.get("/api/request-log/settings")
async def request_log_settings():
    keep = await asyncio.to_thread(request_log.get_keep_text)
    if keep is None:
        raise HTTPException(503, UNAVAILABLE)
    return {"keep_request_text": keep, "retention_days": request_log.RETENTION_DAYS}


@router.put("/api/request-log/settings")
async def request_log_settings_save(body: RequestLogSettings):
    """keep_request_text: store the full sentence of each request (off by default; only a short hash is kept)."""
    if not await asyncio.to_thread(request_log.set_keep_text, body.keep_request_text):
        raise HTTPException(503, UNAVAILABLE)
    return {"keep_request_text": body.keep_request_text, "retention_days": request_log.RETENTION_DAYS}
