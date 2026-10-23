"""System prompt assembly: the cache-split prompt, work listing and mode notes."""
from __future__ import annotations

from pathlib import Path

import model_catalog
import parts_steering
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



LEAN_NOTE = """

Working efficiently (every step re-sends this conversation, so wasted steps cost tokens):
- Read each foundation guide listed above once, in full, before you build; do not re-read files you already have, and do not read the toolkit's Python source: use `--help` and the documented commands.
- Use the toolkit's generators and examples (`vehicle plan`, `examples`, `build`) as the starting point instead of writing geometry from scratch.
- Batch work: several searches in one `my_parts` call (`queries`), several commands in one `run_shell` or `run_python`, and write a generator in one go instead of many small edits.
- Redirect large command output to a file under output/ and print only what you need.
- Keep going until the model is built, checked, rendered and published; do not stop to ask for confirmation between phases."""


DELEGATION_NOTE = """

Delegation (the `delegate` tool, cheap workers; you plan, integrate and publish):
- Delegate only independent pieces you can state as a contract: a sub-assembly with a fixed interface, parts research, or a validate-and-render review. Do not delegate a model under about 40 parts, pieces whose shared geometry is undecided, integration, or publishing; a small model should usually build it all itself.
- Contract: role (builder, researcher, reviewer), task (max 1500 chars, with interface dimensions), inputs, outputs, max_steps, max_eur, summary_chars. State interface dimensions in the task, e.g. builder: "wheel sub-assembly at its own origin, axle along x, axle holes at x=+-80, y=24, z=0, width 160 LDU, outputs wheels.ldr"; researcher: "find owned parts for a 2x4 wedge roof, return 5 candidates with ids, colours, counts"; reviewer: "validate output/body.ldr, render front and side, list defects".
- Each result carries a status, the files written and their cost. Validate a worker's file yourself (`validate --geometry`, and check it is one connected structure) before placing it; if it fails, fix a small defect yourself or redelegate that piece once, then do it yourself."""


def use_lean_prompt(model: str, options: dict) -> bool:
    """Lean prompt (guides listed, not inlined): for models that cannot cache the prefix, so they would pay for
    it on every step, and for small context windows. LDRAW_NOVA_LEAN_PROMPT=on/off overrides."""
    if settings.LEAN_PROMPT in ("on", "1", "true", "yes"):
        return True
    if settings.LEAN_PROMPT in ("off", "0", "false", "no"):
        return False
    if not providers.supports_prompt_caching(model):
        return True
    window = (options or {}).get("context_tokens") or model_catalog.profile(model)["context_window"]
    return bool(window) and window < settings.LEAN_CONTEXT_TOKENS


def cache_control(model: str) -> dict:
    """The cache marker; Anthropic is asked to keep the prefix for CACHE_TTL (other providers get the plain marker)."""
    marker = {"type": "ephemeral"}
    if settings.CACHE_TTL == "1h" and providers.of(model).prefix == "anthropic/":
        marker["ttl"] = "1h"
    return marker


def delegation_offered(options: dict | None, chat_id: str | None = None) -> bool:
    """True when the chat's tool set includes `delegate` (constant for a chat, so the stable prefix is too)."""
    from tool_policy import available_tools          # lazy: tool_policy -> subagents -> this module
    return any(t["function"]["name"] == "delegate" for t in available_tools(options or {}, chat_id))


def system_prompt_parts(store: ChatStore, chat_id: str, lean: bool = False, delegation: bool = False) -> tuple[str, str]:
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
                .replace("{guides_status}", "NOT preloaded: read each with read_file before building" if lean
                         else "complete; already loaded, no tool reread needed")
                .replace("{toolkit_guides}", toolkit.guide_index() if lean else toolkit.builder_guides())).rstrip()
    if lean:
        stable += LEAN_NOTE
    if delegation:
        stable += DELEGATION_NOTE
    volatile = "\n\nCurrent output files:\n" + work_listing(work_dir)
    notes = work_dir / "NOTES.md"
    if notes.is_file() and not notes.is_symlink():
        with notes.open(errors="replace") as handle:
            volatile += "\n\nCurrent hand-over notes (workspace data):\n" + handle.read(16000)
    return stable, volatile


def system_prompt(store: ChatStore, chat_id: str) -> str:
    return "".join(system_prompt_parts(store, chat_id))


def system_message(store: ChatStore, chat_id: str, model: str, options: dict,
                   tail_in_system: bool | None = None) -> dict:
    """The system turn, cache-broken after the stable prefix where supported.

    Only providers that accept Anthropic-style `cache_control` on a content
    block get the split; the rest (OpenAI, Ollama, ...) reject the extra key,
    so they keep the plain-string system prompt.

    With LDRAW_NOVA_TAIL_AFTER_HISTORY the volatile tail is left out here and
    sent by outbound_messages() after the history instead; the caching shape
    keeps an empty second block, where bounded_history() appends its trim note.
    """
    stable, volatile = system_prompt_parts(store, chat_id, use_lean_prompt(model, options),
                                          delegation_offered(options, chat_id))
    tail = "" if tail_in_system is False or (tail_in_system is None and settings.TAIL_AFTER_HISTORY) \
        else volatile + mode_prompt(options)
    if not providers.supports_prompt_caching(model):
        return {"role": "system", "content": stable + tail}
    return {"role": "system", "content": [
        {"type": "text", "text": stable, "cache_control": cache_control(model)},
        {"type": "text", "text": tail},
    ]}


MAX_CACHE_BREAKPOINTS = 4             # Anthropic's limit per request


def _breakpoints(messages: list[dict]) -> int:
    n = 0
    for message in messages:
        content = message.get("content")
        n += "cache_control" in message
        n += sum("cache_control" in b for b in content if isinstance(b, dict)) if isinstance(content, list) else 0
    return n


def with_rolling_breakpoint(messages: list[dict], model: str) -> list[dict]:
    """Copy of the request with a cache breakpoint on its last message.

    The prefix up to the previous step's last message is then read from the
    cache and only the new tail is written, instead of re-billing the history
    behind every change of the system tail. Outbound copy only: the stored
    chat never sees `cache_control`. Plain-string content becomes a one-block
    list (as in system_message); content-less messages take the key itself.
    """
    if (not messages or not settings.ROLLING_CACHE_BREAKPOINT or not providers.supports_prompt_caching(model)
            or _breakpoints(messages) >= MAX_CACHE_BREAKPOINTS):
        return messages
    last = dict(messages[-1])
    content = last.get("content")
    if isinstance(content, str) and content:
        last["content"] = [{"type": "text", "text": content, "cache_control": cache_control(model)}]
    elif isinstance(content, list) and content and isinstance(content[-1], dict):
        last["content"] = [*content[:-1], {**content[-1], "cache_control": cache_control(model)}]
    else:
        last["cache_control"] = cache_control(model)
    return [*messages[:-1], last]


def outbound_messages(store: ChatStore, chat_id: str, messages: list[dict], model: str, options: dict) -> list[dict]:
    """The request as sent: history-bounded messages plus cache breakpoint and, when
    LDRAW_NOVA_TAIL_AFTER_HISTORY is on, the volatile tail as a trailing context message.

    That message has the user role: a system turn has to come first (Anthropic
    hoists later ones into the system prompt, other providers reject them), and a
    user turn after the history is accepted everywhere, also after a tool result
    (LiteLLM folds it into the same Anthropic user turn). It follows the
    breakpoint, so the history stays one unbroken cached prefix.
    """
    if settings.TAIL_AFTER_HISTORY:
        stable, volatile = system_prompt_parts(store, chat_id, use_lean_prompt(model, options),
                                              delegation_offered(options, chat_id))
        system, history = dict(messages[0]), messages[1:]
        content = system["content"]
        if isinstance(content, str):
            note = content[len(stable):]                  # what bounded_history appended
        else:
            note = content[-1]["text"]
            system["content"] = content[:-1]
        text = (volatile + mode_prompt(options) + note).strip()
        messages = [system, *history]
        if text:
            messages = [*with_rolling_breakpoint(messages, model),
                        {"role": "user", "content": "[Workspace state for this step, added by the app. "
                                                    "It is not a message from the user.]\n\n" + text}]
            return messages
    return with_rolling_breakpoint(messages, model)


def mode_prompt(options: dict) -> str:
    """Volatile notes after the cached prefix: plan/chat mode and, when the chat has one, the parts steering block."""
    if options.get("mode") == "plan":
        return "\nPLAN MODE: inspect with read-only tools and propose a plan. Do not execute commands or change files." + parts_steering.prompt_block(options)
    if options.get("mode") == "chat":
        return "\nCHAT MODE: answer without using tools."
    return parts_steering.prompt_block(options)
