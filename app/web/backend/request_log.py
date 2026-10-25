"""The request log: local, hashed telemetry about how requests were routed (docs/plans/done/phase2/nl-review.md, section 7).

One row per describe or estimate request: the route, who decided it (rules or the cheap model), the generator, timing,
and a short hash of the text. The sentence itself is stored only when the owner turns on `keep_request_text` (off by
default, kept in the app_setting table). Rows older than RETENTION_DAYS are pruned at start and daily. Nothing here
ever fails a request (db.best_effort), and nothing leaves the machine.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import os
from typing import Any, Optional

from sqlalchemy import delete, select

import db
from db.models import AppSetting, RequestLog

RETENTION_DAYS = 180
KEEP_TEXT_KEY = "keep_request_text"


def text_hash(text: str) -> str:
    """The first 12 hex characters of sha256: enough to count repeats, not to read the request back."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def keep_text(session) -> bool:
    """The opt-in to store the full sentence. The environment variable LDRAW_NOVA_KEEP_REQUEST_TEXT=1 also turns it on."""
    if os.environ.get("LDRAW_NOVA_KEEP_REQUEST_TEXT", "").strip() == "1":
        return True
    value = session.scalar(select(AppSetting.value).where(AppSetting.key == KEEP_TEXT_KEY))
    return value is True


def get_keep_text() -> Optional[bool]:
    """The stored setting (None when the database is unavailable)."""
    return db.best_effort(keep_text, "request log setting")


def set_keep_text(value: bool) -> bool:
    def write(session) -> bool:
        row = session.get(AppSetting, KEEP_TEXT_KEY)
        if row is None:
            session.add(AppSetting(key=KEEP_TEXT_KEY, value=bool(value)))
        else:
            row.value = bool(value)
            row.updated_at = dt.datetime.now(dt.timezone.utc)
        return True
    return bool(db.best_effort(write, "request log setting"))


def _subject(spec: Optional[dict]) -> tuple[Optional[str], Optional[str]]:
    subject = (spec or {}).get("subject") or {}
    family, kind = subject.get("family"), subject.get("kind")
    return (family if isinstance(family, str) else None), (kind if isinstance(kind, str) else None)


def record(surface: str, text: str, *, route: Optional[str], decided_by: Optional[str], generator: Optional[str] = None,
           spec: Optional[dict] = None, latency_ms: Optional[float] = None, build_kind: Optional[str] = None,
           usage_event_id: Optional[int] = None, fields: Optional[dict[str, Any]] = None) -> Optional[int]:
    """Store one row. Returns its id, or None when nothing was stored (no database, or the write failed)."""
    text = (text or "").strip()
    if not text:
        return None
    family, kind = _subject(spec)

    def write(session) -> int:
        row = RequestLog(surface=surface, build_kind=build_kind, route=route, decided_by=decided_by, family=family, kind=kind,
                         generator=generator, text_sha=text_hash(text), text_len=len(text),
                         latency_ms=None if latency_ms is None else round(latency_ms), usage_event_id=usage_event_id,
                         sentence=text if keep_text(session) else None, fields=fields or {})
        session.add(row)
        session.flush()
        return row.id

    return db.best_effort(write, "request log")


def record_describe(text: str, decision: dict, *, spec: Optional[dict], started: float, usage_event_id: Optional[int] = None,
                    surface: str = "describe", current: Optional[dict] = None) -> Optional[int]:
    """The describe path: `decision` is what fast_path.describe returns; `started` is its time.monotonic() start."""
    import time
    spent = decision.get("understanding_cost_eur")
    fields = {"confidence": decision.get("confidence"), "n_edits": len(decision.get("changes") or []),
              "understanding_cost_eur": spent, "understanding_model": decision.get("understanding_model"),
              "clarified": decision.get("route") == "clarify"}
    return record(surface, text, route=decision.get("route"), decided_by=decision.get("understood_by"),
                  generator=decision.get("generator") or (current or {}).get("generator"), spec=spec,
                  latency_ms=(time.monotonic() - started) * 1000, build_kind="generator" if current else None,
                  usage_event_id=usage_event_id, fields={k: v for k, v in fields.items() if v is not None})


def record_estimate(text: str, guess: dict, *, spec: Optional[dict], current: Optional[dict] = None) -> Optional[int]:
    """The composer's rules-only estimate (no model call): `guess` is what fast_path.rules_guess returns."""
    return record("estimate", text, route=guess.get("route"), decided_by="rules",
                  generator=guess.get("generator") or (current or {}).get("generator"), spec=spec,
                  latency_ms=guess.get("took_ms"), build_kind="generator" if current else None,
                  fields={"confidence": guess.get("confidence")})


def prune(days: int = RETENTION_DAYS) -> int:
    """Delete rows older than `days`. Returns how many went (0 when the database is unavailable)."""
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
    return db.best_effort(lambda session: session.execute(delete(RequestLog).where(RequestLog.ts < cutoff)).rowcount, "request log prune") or 0
