"""The cost history: one `usage_event` row per LLM call, kept forever, independent of chats.

Written from the places that call a model (agent_loop.run_steps for the planner and the workers, understand.py for the
understanding call). A chat's own `_usage` records in its messages are unchanged: they remain the per-chat view and
`usage.usage_totals` still reads them. Recording never fails a request (db.best_effort).
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import text

import db
import settings
from db.models import UsageEvent

KINDS = ("agent", "worker", "understand", "other")


def _build_of(chat_id: Optional[str]) -> Optional[str]:
    """A build's workspace chat is keyed b-<slug> (the chat machinery is reused for builds)."""
    return chat_id[2:] if chat_id and chat_id.startswith("b-") and len(chat_id) > 2 else None


def record(kind: str, usage: Optional[dict], model: Optional[str], *, chat_id: Optional[str] = None,
           message_id: Optional[int] = None, worker_id: Optional[str] = None) -> Optional[int]:
    """Store one call's token counts and cost (usage is a usage.usage_record() dict, optionally with `cost_usd`).

    `cost_usd` missing or None means the model has no known price and is stored as NULL ("unpriced"), the same rule
    as usage.usage_totals. Returns the new row's id, or None when nothing was stored.
    """
    if not usage or kind not in KINDS:
        return None
    cost = usage.get("cost_usd")
    priced = isinstance(cost, (int, float)) and not isinstance(cost, bool)
    event = UsageEvent(kind=kind, chat_id=chat_id, build_id=_build_of(chat_id), message_id=message_id, worker_id=worker_id,
                       model=model, prompt=int(usage.get("prompt") or 0), completion=int(usage.get("completion") or 0),
                       cache_read=int(usage.get("cache_read") or 0), cache_write=int(usage.get("cache_write") or 0),
                       cost_usd=Decimal(str(round(cost, 6))) if priced else None,
                       eur_per_usd=Decimal(str(settings.eur_per_usd())))

    def write(session) -> int:
        session.add(event)
        session.flush()
        return event.id

    return db.best_effort(write, "usage event")


def _day(value: str, name: str) -> dt.date:
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{name} must be a date like 2026-10-24") from None


def costs(date_from: Optional[str] = None, date_to: Optional[str] = None) -> dict:
    """Spend per UTC day and kind (the cost_daily view), plus totals, for a cost-history page. Dates are inclusive."""
    start = _day(date_from, "from") if date_from else None
    end = _day(date_to, "to") if date_to else None
    if start and end and start > end:
        raise ValueError("from must not be after to")
    where = " AND ".join(c for c in ("day >= :start" if start else "", "day <= :end" if end else "") if c)
    sql = text("SELECT day, kind, calls, cost_usd, unpriced FROM cost_daily"
               + (f" WHERE {where}" if where else "") + " ORDER BY day, kind")
    params = {k: v for k, v in (("start", start), ("end", end)) if v}
    with db.session_scope() as session:
        rows = session.execute(sql, params).all()
    rate = settings.eur_per_usd()
    days: list[dict[str, Any]] = []
    for day, kind, calls, cost_usd, unpriced in rows:
        usd = float(cost_usd or 0)
        days.append({"day": day.isoformat(), "kind": kind, "calls": calls, "cost_usd": round(usd, 6),
                     "cost_eur": round(usd * rate, 4), "unpriced_calls": unpriced})
    usd_total = sum(d["cost_usd"] for d in days)
    return {"from": start.isoformat() if start else None, "to": end.isoformat() if end else None, "eur_per_usd": rate,
            "days": days,
            "totals": {"calls": sum(d["calls"] for d in days), "cost_usd": round(usd_total, 6),
                       "cost_eur": round(usd_total * rate, 4), "unpriced_calls": sum(d["unpriced_calls"] for d in days)}}
