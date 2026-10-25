#!/usr/bin/env python
"""One-command check of prompt-cache behaviour against a REAL provider, through a running app.

Usage (stdlib only; the app must be running with a working API key for MODEL):
  python docs/plans/done/loop-perf/live_check.py --model anthropic/claude-sonnet-5
  python docs/plans/done/loop-perf/live_check.py --model openrouter/anthropic/claude-sonnet-5 --steps 6 \
      --base-url http://localhost:8000

MODEL is matched against the configured agents (Settings > Agents) by id, display name or
`litellm_params.model`. Run it once per model to compare, and with the app started with
LDRAW_NOVA_ROLLING_CACHE_BREAKPOINT=0 / =1 or LDRAW_NOVA_TAIL_AFTER_HISTORY=1 to compare layouts.

What it does: creates a chat through the HTTP API, sends a scripted prompt that makes the model
call the harmless `report_progress` tool --steps times (one LLM request per step, plus a final
answer), follows the app's SSE stream for the per-step `usage` events (the same records the app saves on
each assistant message, which fill in anything the stream attached too late to see), and prints a table:

  step    input  output  cache_read  cache_write  hit%
  input is the whole prompt as the provider counted it (cached tokens included, as LiteLLM
  reports it); hit% = cache_read / input.

Healthy output with the rolling breakpoint: step 1 shows a large cache_write (the ~18k-token
system prompt), step 2 onward shows cache_read close to the previous step's input and only a
small cache_write for the new tail. Reads stuck at 0 mean the provider ignores the breakpoints.
Exit status: 0 caching seen, 1 the turn failed or no usage arrived, 2 usage but no cache reads.

The chat stays in the app (delete it from the UI if unwanted). It costs a few cents of tokens.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

PROMPT = ("This is a prompt-caching check, not a build. Make exactly {n} separate calls to the report_progress "
          "tool, one per step: wait for each result before the next call and never call tools in parallel, "
          "with the summary 'check 1', 'check 2', and so on. Then answer with the single word: done. "
          "Use no other tool and do not read or write any file.")
FIELDS = ("prompt", "completion", "cache_read", "cache_write")


def call(base: str, method: str, path: str, body: dict | None = None, timeout: float = 30):
    request = urllib.request.Request(base.rstrip("/") + path, method=method,
                                     data=None if body is None else json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read() or b"null")
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"{method} {path} -> HTTP {exc.code}: {exc.read().decode(errors='replace')[:300]}") from None
    except urllib.error.URLError as exc:
        raise SystemExit(f"cannot reach {base}: {exc.reason}") from None


def find_model(base: str, wanted: str) -> dict:
    models = call(base, "GET", "/api/llm-models")["models"]
    for key in (lambda m: m["id"], lambda m: m["model_name"], lambda m: m["litellm_params"]["model"]):
        found = [m for m in models if key(m) == wanted]
        if found:
            return found[0]
    names = ", ".join(sorted(m["litellm_params"]["model"] for m in models)) or "none configured"
    raise SystemExit(f"no configured agent matches {wanted!r}. Configured: {names}")


def sse(base: str, path: str, deadline: float):
    """Yield (event, data) from a server-sent-events stream until it closes or the deadline passes."""
    with urllib.request.urlopen(base.rstrip("/") + path, timeout=60) as response:
        event, data = "message", []
        for raw in response:
            line = raw.decode().rstrip("\n")
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].strip())
            elif not line and data:
                yield event, json.loads("\n".join(data))
                event, data = "message", []
            if time.time() > deadline:
                raise SystemExit("timed out waiting for the turn to finish (raise --timeout)")


def collect(events) -> tuple[list[dict], str | None]:
    """Per-step usage dicts and the turn error, if any, from an iterable of (event, data)."""
    steps, error = [], None
    for event, data in events:
        if event == "usage":
            steps.append({field: int(data.get(field) or 0) for field in FIELDS})
        elif event == "turn_error":
            error = data.get("message", "turn failed")
        elif event == "snapshot" and not data.get("running"):
            break                                   # the turn was already over when we attached
        elif event in ("done", "reconnect"):
            break
    return steps, error


def stored_usage(base: str, chat_id: str) -> tuple[list[dict], str | None]:
    """The same records as saved on the assistant messages (used when the stream was missed)."""
    messages = call(base, "GET", f"/api/chats/{chat_id}")["messages"]
    steps = [{field: int((m["_usage"] or {}).get(field) or 0) for field in FIELDS}
             for m in messages if m.get("role") == "assistant" and m.get("_usage")]
    error = next((m["content"] for m in messages if m.get("_error")), None)
    return steps, error


def table(steps: list[dict]) -> str:
    head = f"{'step':>4} {'input':>9} {'output':>8} {'cache_read':>11} {'cache_write':>12} {'hit%':>6}"
    rows = [head, "-" * len(head)]
    for i, s in enumerate(steps, 1):
        hit = f"{100 * s['cache_read'] / s['prompt']:.0f}" if s["prompt"] else "-"
        rows.append(f"{i:>4} {s['prompt']:>9} {s['completion']:>8} {s['cache_read']:>11} {s['cache_write']:>12} {hit:>6}")
    total = {f: sum(s[f] for s in steps) for f in FIELDS}
    hit = f"{100 * total['cache_read'] / total['prompt']:.0f}" if total["prompt"] else "-"
    rows += ["-" * len(head), f"{'sum':>4} {total['prompt']:>9} {total['completion']:>8} {total['cache_read']:>11} "
                              f"{total['cache_write']:>12} {hit:>6}"]
    return "\n".join(rows)


def verdict(steps: list[dict]) -> tuple[str, int]:
    if not steps:
        return "No usage reported: the provider returned no token counts, or the turn never reached the model.", 1
    later = steps[1:]
    if any(s["cache_read"] for s in later):
        return "Caching works: later steps read the prefix from the cache.", 0
    if any(s["cache_write"] for s in steps):
        return "Cache writes but no reads on later steps: the breakpoints are accepted but nothing is reused (prefix changing between steps, or below the provider's minimum cacheable size).", 2
    return "No cache activity at all: the provider or model does not cache, or ignores cache_control.", 2


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Per-step token and prompt-cache table for one model.")
    parser.add_argument("--model", required=True, help="configured agent: id, name or litellm model string")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--steps", type=int, default=4, help="tool-call steps before the final answer (default 4)")
    parser.add_argument("--timeout", type=float, default=300, help="seconds to wait for the turn")
    parser.add_argument("--prompt", help="replace the scripted prompt")
    args = parser.parse_args(argv)

    entry = find_model(args.base_url, args.model)
    chat = call(args.base_url, "POST", "/api/chats", {"llm_model_id": entry["id"]})
    print(f"model {entry['litellm_params']['model']} ({entry['model_name']}), chat {chat['id']}")
    call(args.base_url, "POST", f"/api/chats/{chat['id']}/messages", {
        "text": args.prompt or PROMPT.format(n=args.steps), "llm_model_id": entry["id"],
        "options": {"permissions": "full"}})
    steps, error = collect(sse(args.base_url, f"/api/chats/{chat['id']}/stream", time.time() + args.timeout))
    saved, saved_error = stored_usage(args.base_url, chat["id"])
    if len(saved) >= len(steps):                    # the stream may attach late and miss the first steps
        steps, error = saved, error or saved_error
    if error:
        print(f"turn failed: {error}")
        return 1
    print(table(steps) if steps else "(no usage events)")
    message, code = verdict(steps)
    print(message)
    return code


if __name__ == "__main__":
    sys.exit(main())
