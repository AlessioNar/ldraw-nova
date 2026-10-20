"""System prompt assembly: the cache-split prompt, work listing and mode notes."""
from __future__ import annotations

from pathlib import Path

import providers
import settings
import toolkit
from store import ChatStore

WORK_LISTING_LIMIT = 60               # files of the work folder listed in the system prompt


def work_listing(work_dir: Path) -> str:
    """What's in the work folder right now, for the system prompt: whoever
    continues the chat (possibly another model) sees what earlier turns left."""
    if not work_dir.is_dir():
        return "(empty)"
    files = sorted(p for p in work_dir.rglob("*") if p.is_file() and ".scripts" not in p.relative_to(work_dir).parts)
    scripts = sum(1 for _ in (work_dir / ".scripts").glob("*")) if (work_dir / ".scripts").is_dir() else 0
    lines = [f"- {p.relative_to(work_dir)} ({p.stat().st_size} bytes)" for p in files[:WORK_LISTING_LIMIT]]
    if len(files) > WORK_LISTING_LIMIT:
        lines.append(f"- ... and {len(files) - WORK_LISTING_LIMIT} more")
    if scripts:
        lines.append(f"- .scripts/ ({scripts} scripts run in earlier turns)")
    return "\n".join(lines) or "(empty)"



def system_prompt_parts(store: ChatStore, chat_id: str) -> tuple[str, str]:
    """The system prompt split at its stability boundary, for prompt caching.

    The first part is byte-identical on every turn of a chat (instructions.md
    plus the foundation guides, ~18k tokens), so it can sit behind a cache
    breakpoint. The second grows as the agent writes files, and has to stay
    after it or the whole prefix is re-billed on every step.
    """
    work_dir = store.work_dir(chat_id)
    text = (settings.PROMPTS_DIR / "system.md").read_text()
    stable = (text.replace("{work_dir}", str(work_dir))
                .replace("{generated_dir}", str(settings.GENERATED_DIR))
                .replace("{ldraw_dir}", str(settings.LDRAW_DIR))
                .replace("{artifact_base}", f"/api/chats/{chat_id}/artifacts")
                .replace("{toolkit_instructions}", toolkit.instructions())
                .replace("{toolkit_guides}", toolkit.builder_guides())).rstrip()
    volatile = "\n\nCurrent output files:\n" + work_listing(work_dir)
    notes = work_dir / "NOTES.md"
    if notes.is_file() and not notes.is_symlink():
        with notes.open(errors="replace") as handle:
            volatile += "\n\nCurrent hand-over notes (workspace data):\n" + handle.read(16000)
    return stable, volatile


def system_prompt(store: ChatStore, chat_id: str) -> str:
    return "".join(system_prompt_parts(store, chat_id))


def system_message(store: ChatStore, chat_id: str, model: str, options: dict) -> dict:
    """The system turn, cache-broken after the stable prefix where supported.

    Only providers that accept Anthropic-style `cache_control` on a content
    block get the split; the rest (OpenAI, Ollama, ...) reject the extra key,
    so they keep the plain-string system prompt.
    """
    stable, volatile = system_prompt_parts(store, chat_id)
    tail = volatile + mode_prompt(options)
    if not providers.supports_prompt_caching(model):
        return {"role": "system", "content": stable + tail}
    return {"role": "system", "content": [
        {"type": "text", "text": stable, "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": tail},
    ]}



def mode_prompt(options: dict) -> str:
    if options.get("mode") == "plan":
        return "\nPLAN MODE: inspect with read-only tools and propose a plan. Do not execute commands or change files."
    if options.get("mode") == "chat":
        return "\nCHAT MODE: answer without using tools."
    return ""

