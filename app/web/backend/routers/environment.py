"""Environment override routes."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

import environment_config

router = APIRouter()


@router.get("/api/environment")
def environment_get():
    return {"variables": environment_config.public()}


@router.get("/api/environment/check")
def environment_check(name: str = "", exclude_id: str = ""):
    return environment_config.check_name(name, exclude_id)


@router.put("/api/environment")
async def environment_save(request: Request):
    # Validate without echoing secret input values in Pydantic error responses.
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(400, "Expected environment variables as JSON") from None
    if not isinstance(body, dict) or not isinstance(body.get("variables"), list):
        raise HTTPException(400, "Expected a list of environment variables")
    try:
        return {"variables": environment_config.save(body["variables"])}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
