"""POST /api/describe and GET /api/describe/estimate: the fast-path decision. Builds nothing, creates no build.

Contract and examples: docs/plans/phase2/fast-path.md.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

import fast_path
import generator_bridge
import understand

router = APIRouter()


class Current(BaseModel):
    generator: str = Field(max_length=60)
    params: dict[str, Any] = Field(default_factory=dict)


class DescribeBody(BaseModel):
    text: str = Field(max_length=understand.MAX_REQUEST_CHARS * 2)
    build_id: Optional[str] = Field(default=None, max_length=80)
    previous_text: Optional[str] = Field(default=None, max_length=understand.MAX_REQUEST_CHARS * 2)   # makes "again" answerable for free
    current: Optional[Current] = None


def _current(current: Optional[Current]) -> Optional[dict]:
    return current.model_dump() if current else None


@router.post("/api/describe")
async def describe(body: DescribeBody):
    """Decide how to serve a request: generator, edit of the current build, needs_ai (ask first) or clarify.

    Spends at most one cheap model call (none when a rule answers) and reports it in understanding_cost_eur.
    """
    if not body.text.strip():
        raise HTTPException(422, "text is empty")
    registry = await generator_bridge.ensure()
    if registry and body.current and not any(e["id"] == body.current.generator for e in registry["entries"]):
        raise HTTPException(422, f"unknown generator {body.current.generator!r}")
    return await fast_path.describe(body.text, current=_current(body.current), build_id=body.build_id,
                                  previous_text=body.previous_text)


@router.get("/api/describe/estimate")
def describe_estimate(text: str = Query(default="", max_length=understand.MAX_REQUEST_CHARS * 2),
                      generator: Optional[str] = Query(default=None, max_length=60)):
    """Rules-only guess for the composer hint: no model call, no toolkit call, milliseconds. Same route names."""
    current = {"generator": generator, "params": {}} if generator else None
    return fast_path.rules_guess(text, current)
