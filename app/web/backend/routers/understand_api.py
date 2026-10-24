"""Internal test endpoint for the understanding step. Not used by the chat turn yet."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

import understand

router = APIRouter()


class UnderstandBody(BaseModel):
    text: str = Field(max_length=understand.MAX_REQUEST_CHARS * 2)
    current_spec: Optional[dict] = None
    previous_text: Optional[str] = None
    llm_id: Optional[str] = None
    use_rules: bool = True
    parts_mode: Optional[str] = None
    max_parts: Optional[int] = None
    budget_eur: Optional[float] = None


@router.post("/api/understand")
async def understand_endpoint(body: UnderstandBody):
    """spec.v1 for one message. Spends one cheap model call unless a rule answers; reports tokens and cost."""
    params = await understand.model_params(body.llm_id)
    options = {"parts_mode": body.parts_mode, "max_parts": body.max_parts, "budget_eur": body.budget_eur}
    result = await understand.understand(body.text, current_spec=body.current_spec, previous_text=body.previous_text,
                                         options=options, params=params, use_rules=body.use_rules)
    return result.as_dict()
