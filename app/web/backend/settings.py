"""Filesystem locations used by the web app.

Everything defaults to the container layout (see Dockerfile); the env vars
exist so tests and local development can point them elsewhere.
"""
from __future__ import annotations

import os
from pathlib import Path

from leocad_render import DATA_DIR, GENERATED_DIR, OUTPUT_DIR

__all__ = [
    "DATA_DIR", "GENERATED_DIR", "CHATS_DIR", "OUTPUT_DIR", "INVENTORIES_DIR", "WEB_DIRS",
    "CONFIG_DIR", "LDRAW_DIR", "INDEX_DIR", "STATIC_DIR",
    "VIEWER_DIR", "VIEWER_VENDOR_DIR", "PLAYER_VENDOR_DIR", "PROMPTS_DIR",
    "COALESCE_REASONING",
]

# Shared with the host (bind mount):
#   generated/          the model collection (flat): <name>.mpd|.ldr|.dat + <name>.png snapshot
#   chats/<chat-id>/    one folder per chat: chat.json, messages.jsonl, models.jsonl, renders/
#   output/<chat-id>/   each chat's agent work folder (notes, plans, scratch files) — never served
#   inventories/        the user's parts inventories: <id>.json + the uploaded <id>.csv — never served
CHATS_DIR = DATA_DIR / "chats"
INVENTORIES_DIR = Path(os.environ.get("LDRAW_NOVA_INVENTORIES_DIR", DATA_DIR / "inventories"))

# Top-level folders of /data the web UI may serve. output/ is agent-only.
WEB_DIRS = ("generated", "chats")

# LLM entries + API keys. A named volume, deliberately NOT under /data: agent
# code runs as another user and could otherwise read them.
CONFIG_DIR = Path(os.environ.get("LDRAW_NOVA_WEB_CONFIG_DIR", "/config"))

# Baked into the image.
LDRAW_DIR = Path(os.environ.get("LEOCAD_LIB", "/opt/ldraw/ldraw"))
INDEX_DIR = Path(os.environ.get("LDRAW_NOVA_INDEX_DIR", "/opt/index"))
STATIC_DIR = Path(os.environ.get("LDRAW_NOVA_WEB_STATIC_DIR", "/opt/web/static"))
VIEWER_DIR = Path(os.environ.get("LDRAW_NOVA_WEB_VIEWER_DIR", "/opt/web/viewer"))                   # viewer.html
VIEWER_VENDOR_DIR = Path(os.environ.get("LDRAW_NOVA_WEB_VIEWER_VENDOR_DIR", "/opt/web/viewer-vendor"))  # ldbi etc.
PLAYER_VENDOR_DIR = Path(os.environ.get("LDRAW_NOVA_WEB_PLAYER_VENDOR_DIR", "/opt/web/player-vendor"))  # ldraw-player

# The user's parts inventories: <id>.json (parsed) + <id>.csv (as uploaded). Under /data so it survives upgrades.

PROMPTS_DIR = Path(__file__).parent / "prompts"
TOOLKIT_DIR = Path(os.environ.get("LDRAW_NOVA_TOOLKIT_DIR", "/opt/ldraw-nova"))
# The interpreter of the toolkit's own uv environment (the backend's Python lacks pyldraw3 and numpy).
TOOLKIT_PYTHON = Path(os.environ.get("LDRAW_NOVA_TOOLKIT_PYTHON") or TOOLKIT_DIR / ".venv/bin/python")


# --- agent history tuning (history.py, tools.py) ---------------------------

def _env_int(name: str, default: int, minimum: int = 0) -> int:
    try:
        return max(minimum, int(os.environ.get(name, "")))
    except ValueError:
        return default


def _flag(name: str, default: bool) -> bool:
    value = os.environ.get(name, "").strip().lower()
    return default if not value else value not in ("0", "false", "no", "off")


def mask_old_tool_output() -> bool:
    """Replace old bulky tool results with a stub in the history sent to the LLM. On by default
    (LDRAW_NOVA_MASK_OLD_TOOL_OUTPUT=0 turns it off): old output is the bulk of a long build's input tokens."""
    return _flag("LDRAW_NOVA_MASK_OLD_TOOL_OUTPUT", True)


def mask_after_steps() -> int:
    """A tool result is masked once at least this many assistant steps came after it."""
    return _env_int("LDRAW_NOVA_MASK_AFTER_STEPS", 10, 1)


def mask_min_chars() -> int:
    """Only results longer than this are masked."""
    return _env_int("LDRAW_NOVA_MASK_MIN_CHARS", 2000, 200)


def mask_batch_steps() -> int:
    """The masking boundary advances in chunks of this many steps, so the cached prefix changes once per chunk."""
    return _env_int("LDRAW_NOVA_MASK_BATCH_STEPS", 10, 1)


def image_eviction_batch() -> int:
    """Older renders are dropped this many at a time (always keeping the newest KEEP_IMAGE_MESSAGES), so the
    middle of the history is rewritten once per batch instead of at every render. 1 = evict one per render."""
    return _env_int("LDRAW_NOVA_IMAGE_EVICTION_BATCH", 4, 1)


def read_file_max_chars() -> int:
    """Default `max_chars` of the read_file tool."""
    return _env_int("LDRAW_NOVA_READ_FILE_MAX_CHARS", 40_000, 1000)


# Merge streamed OpenRouter reasoning_details deltas before they are stored and
# re-sent every step. Off by default: providers may require the original blocks
# on continuation, which only a live run can confirm.
COALESCE_REASONING = os.environ.get("LDRAW_NOVA_COALESCE_REASONING", "").strip().lower() in ("1", "true", "yes", "on")


# Prompt-cache layout of the agent loop (see docs/plans/loop-perf/FIX-cache.md).
# A cache breakpoint on the last message of the history, for providers that cache.
ROLLING_CACHE_BREAKPOINT = os.environ.get("LDRAW_NOVA_ROLLING_CACHE_BREAKPOINT", "1").lower() not in ("0", "false", "no", "off")
# Send the work listing, NOTES.md and mode note after the history instead of in the system turn. On by default
# (LDRAW_NOVA_TAIL_AFTER_HISTORY=0 restores the old layout): a changing listing in the system turn invalidated the
# cached history on every file the agent wrote.
TAIL_AFTER_HISTORY = _flag("LDRAW_NOVA_TAIL_AFTER_HISTORY", True)
# How long Anthropic keeps the cached prefix: "5m" (their default) or "1h". An approval prompt or a long tool run
# easily exceeds five minutes, and the whole prompt is then re-billed as a cache write.
CACHE_TTL = os.environ.get("LDRAW_NOVA_CACHE_TTL", "1h").strip().lower()


def eur_per_usd() -> float:
    """Exchange rate for the cost shown and budgeted in euros. A fixed, editable estimate: prices come in dollars."""
    try:
        rate = float(os.environ.get("LDRAW_NOVA_EUR_PER_USD", ""))
    except ValueError:
        return 0.92
    return rate if rate > 0 else 0.92


def max_steps() -> int:
    """Model calls in one turn before it stops and asks the user to continue."""
    return _env_int("LDRAW_NOVA_MAX_STEPS", 80, 1)


def max_workers() -> int:
    """Workers running at once in one turn (the rest queue). Default 3, ceiling 6."""
    return min(_env_int("LDRAW_NOVA_MAX_WORKERS", 3, 1), 6)


def max_delegations() -> int:
    """Delegations the planner may start in one turn."""
    return _env_int("LDRAW_NOVA_MAX_DELEGATIONS", 8, 1)


def subagents_enabled() -> bool:
    """Prototype: offer the planner a delegate_task tool that runs jobs on a cheap worker model. Off by default."""
    return _flag("LDRAW_NOVA_SUBAGENTS", False)


def worker_llm_id() -> str:
    """Id of the configured LLM entry the delegated worker steps run on."""
    return (os.environ.get("LDRAW_NOVA_WORKER_LLM_ID") or os.environ.get("LDRAW_NOVA_WORKER_MODEL_ID") or "").strip()


LEAN_CONTEXT_TOKENS = 200_000        # models with a smaller context get the lean prompt
LEAN_PROMPT = os.environ.get("LDRAW_NOVA_LEAN_PROMPT", "auto").strip().lower()   # auto | on | off
