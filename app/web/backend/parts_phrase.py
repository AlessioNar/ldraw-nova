"""Reads "use the parts from my fire station" on the free fast path and offers it as a parts scope (never applied silently)."""
from __future__ import annotations

import re
from typing import Optional

import inventories

_PHRASE = re.compile(
    r"\b(?:(only)\s+)?(?:use|using|build\s+(?:it\s+)?(?:with|from)|make\s+(?:it\s+)?(?:with|from))\s+(?:only\s+)?(?:the\s+)?"
    r"(?:parts|pieces|bricks)\s+(?:from|of|in)\s+(?:my\s+|the\s+)?(.+?)\s*[.!?]*$", re.I)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def offer(text: str) -> Optional[dict]:
    """A clarify decision carrying `parts_offer` ({id, name, mode}) when the message names one of the person's inventories."""
    m = _PHRASE.search(text.strip())
    if not m:
        return None
    wanted = _norm(m.group(2))
    if not wanted:
        return None
    mode = "only" if m.group(1) or re.search(r"\bonly\b", text, re.I) else "prefer"
    found = [i for i in inventories.list_inventories() if wanted in (_norm(i["name"]), _norm(i["id"]))] or \
            [i for i in inventories.list_inventories() if wanted in _norm(i["name"]) or _norm(i["name"]) in wanted]
    if len(found) != 1:
        return None
    inv = found[0]
    verb = "Only use" if mode == "only" else "Prefer"
    return {"route": "clarify", "question": f"{verb} the parts from {inv['name']} for this build?", "choices": [],
            "parts_offer": {"id": inv["id"], "name": inv["name"], "mode": mode}}
