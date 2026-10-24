"""Who answers a message in a build's chat: the free fast path ("quick") or the AI agent ("ai").

The mode is decided here, from the saved chat, and shown in the UI. The agent is *engaged* when its turn is the latest
real turn of the conversation and the task is not finished: it asked the person something, or it has not delivered a
proposal since the person last wrote. While engaged every message goes to the agent, whatever words it contains
("dark grey", "step 4"): only an explicit per-message "Quick change instead" reaches the fast path. See
docs/system/request-lifecycle.md.
"""
from __future__ import annotations

import re
from typing import Optional

import understand

_FILLER = r"(?:(?:please|kindly|can you|could you|would you|will you|just|now|then)\s+)*"
_CONTINUE = re.compile(
    r"^(?:yes|yep|yeah|yup|ok|okay|sure|please|thanks|thank you|go on|go ahead|continue|proceed|next|do it|carry on|keep going|"
    r"sounds good|perfect|great|fine|no|nope|why|how come|what about|how about|and what|step \d+|stop|wait|hold on|however|but)\b")
_ASKS = re.compile(r"\?\s*\W*$|\b(?:should i|shall i|do you want|would you like|want me to|let me know|please confirm|"
                   r"go ahead with|ready for step|before i (?:continue|proceed))\b", re.I)
_STOP = {"this", "that", "these", "those", "same", "other", "rest", "whole", "last", "first", "next", "model", "build", "draft"}


def key(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _visible(messages: list[dict]) -> list[dict]:
    return [m for m in messages if not m.get("_ui_only") and not m.get("_hidden") and m.get("role") in ("user", "assistant", "tool")]


def _text(m: dict) -> str:
    c = m.get("content")
    return c if isinstance(c, str) else ""


def agent_messages(messages: list[dict]) -> list[dict]:
    return [m for m in _visible(messages) if m["role"] == "assistant" and not m.get("_fast_path")]


def state(messages: list[dict], running: bool = False) -> dict:
    """{mode: ai|quick, has_agent, agent_text, asks}: has_agent says an agent conversation exists at all."""
    real = _visible(messages)
    agent = agent_messages(messages)
    out = {"mode": "quick", "has_agent": bool(agent), "agent_text": next((_text(m) for m in reversed(agent) if _text(m)), ""),
           "asks": False}
    turns = [m for m in real if m["role"] in ("user", "assistant")]
    if not turns:
        return out
    last = turns[-1]
    if running or (last["role"] == "user" and not (last.get("_fast_path"))):     # a turn is in flight (or died): the agent's
        out["mode"] = "ai" if (agent or running) else "quick"
        return out
    if last["role"] != "assistant" or last.get("_fast_path"):
        return out
    out["asks"] = bool(_ASKS.search(_text(last)[-300:]))
    last_user = max((i for i, m in enumerate(real) if m["role"] == "user"), default=-1)
    proposed = any(m["role"] == "tool" and '"proposal"' in _text(m) for m in real[last_user + 1:])
    if out["asks"] or not proposed:
        out["mode"] = "ai"
    return out


def is_continuation(text: str, agent_text: str = "") -> bool:
    """A short reply or follow-up to what the agent said, not a self-contained edit command."""
    k = key(text)
    if not k:
        return False
    if _CONTINUE.match(k) or k.endswith("?"):
        return True
    if agent_text:
        low = agent_text.lower()
        for word in re.findall(r"\bthe ([a-z]{4,})\b", k):
            if word not in understand.ROLES and word not in _STOP and re.search(rf"\b{word}s?\b", low):
                return True
    return False


_EDIT_WORDS = re.compile(r"\b(?:make|paint|colou?r|turn|change|set|add|remove|undo|revert|longer|shorter|wider|narrower|taller|bigger|"
                         r"smaller|studs?|more|fewer|less)\b|\b(?:\d+\s*(?:x|studs?))")
_EXTRA = re.compile(r"\b(?:go on|however|but|this time|let'?s|consider|agnostic|step \d+|and then|also)\b")


def whole_edit_command(text: str) -> bool:
    """The whole message is one short edit command ("please make the roof blue", "walls dark grey", "2 studs longer"),
    not an edit buried in a longer instruction with other clauses."""
    k = re.sub(rf"^{_FILLER}", "", key(text)).rstrip(" .!,")
    if not k or len(k.split()) > 9 or re.search(r"[.;!?]\s*\S", k) or "," in k or _EXTRA.search(k):
        return False
    return bool(_EDIT_WORDS.search(k)) or bool(re.search(rf"\b(?:{understand._ROLE_RE})\b.*\b(?:{understand._COLOUR_RE})\b", k))


def role_chips(entry: Optional[dict], params: dict) -> tuple[list[str], list[str]]:
    """(things the person can change, example commands) from the generator's own parameters."""
    if not entry:
        return [], []
    props = entry["params"]["properties"]
    things, chips = [], []
    for name, spec in props.items():
        if spec.get("x-colour"):
            role = re.sub(r"[_ ]?(?:colou?r)$", "", name).replace("_", " ")
            if role not in things and (role in understand.ROLES):
                things.append(role)
                have = str(params.get(name, "")).lower()
                shade = next(c for c in ("blue", "red", "green", "yellow") if c != have)
                chips.append(f"make the {role if role != 'wall' else 'walls'} {shade}")
    sizes = [n for n in ("width", "length", "height", "depth", "wingspan") if n in props]
    things += [f"{sizes[0]}" if sizes else "size"] + [n for n in ("windows", "chimney", "porch", "engines") if n in props]
    return [f"{t} colour" if t in understand.ROLES else t for t in things], chips[:2]


def contextual_clarify(*, title: str, entry: Optional[dict], params: Optional[dict], has_agent: bool, empty: bool,
                       reason: str = "") -> dict:
    """A question about THIS build (never "what would you like to build?" on an existing one)."""
    if empty and not entry:
        out = {"question": f"{title} is empty. What would you like to build?",
               "choices": ["a small house", "a small red car", "a tree"]}
    else:
        things, chips = role_chips(entry, params or {})
        what = f" ({', '.join(things[:4])}, …)" if things else ""
        name = (entry or {}).get("title") or title
        out = {"question": f"What would you like to change on {'the ' + name if entry else name}?{what}", "choices": chips}
    if reason:
        out["question"] = f"{reason} {out['question']}"
    if has_agent:
        out["choices"] = [*out["choices"], "Ask the AI"]
    return {"route": "clarify", **out}
