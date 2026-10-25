#!/usr/bin/env python
"""Cache-cost replay of the FIX-cache change (companion to replay.py).

Usage (from app/web/backend):
  LDRAW_NOVA_TOOLKIT_DIR=<toolkit> python ../../../docs/plans/done/loop-perf/cache_scenarios.py \
      --cache <dir with one folder per chat> [--out DIR] [--chats ID,ID]

For every recorded LLM step it builds the request three ways with the REAL code
(system_prompt.system_message / outbound_messages, history.llm_history), on a caching
model (--caching-model, default anthropic/claude-opus-5; the recorded model only picks
llm_history's reasoning stripping, as in replay.py):
  baseline  breakpoint after the stable system block only (master before the fix)
  a         + rolling breakpoint on the last message
  a+b       + LDRAW_NOVA_TAIL_AFTER_HISTORY (tail as trailing user message after the breakpoint)
and bills each with a simulated Anthropic prefix cache.

Assumptions (all idealised, same spirit as REPORT.md):
  - tokens are chars/4 (images by dimensions), as in replay.py; bounded_history is not applied (never fired in these chats)
  - billing: cache read 0.1x, cache write 1.25x, uncached 1x. Per request the longest prefix, ending at a block
    boundary, that an earlier request wrote at one of its breakpoints is read; the rest up to this request's last
    breakpoint is written; the remainder is uncached. Every identical byte is assumed to hit, TTL (5 min) and the
    20-block lookback are ignored, as is the minimum cacheable length.
  - cache_control marks do not take part in the prefix identity (the provider ignores them for hashing).
  - the work folder is rebuilt from write_file/run_python calls only (same lower bound as replay.py).
"""
from __future__ import annotations

import argparse, hashlib, json, os, sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import replay  # noqa: E402  (also puts cwd and app/ on sys.path)
import agent, settings, system_prompt  # noqa: E402

READ, WRITE = 0.1, 1.25


def strip_marks(obj):
    if isinstance(obj, dict):
        return {k: strip_marks(v) for k, v in obj.items() if k != "cache_control"}
    if isinstance(obj, list):
        return [strip_marks(v) for v in obj]
    return obj


def blocks(messages):
    """[(json without marks, tokens, has_breakpoint)] in request order (system split into its blocks)."""
    plain = strip_marks(messages)
    # the provider sees string content as one text block: hash both shapes alike
    plain = [m if not isinstance(m.get("content"), str) or not m["content"] or m["role"] == "system"
             else {**m, "content": [{"type": "text", "text": m["content"]}]} for m in plain]
    ser = replay.serialize(plain)
    marks = []
    sc = messages[0]["content"]
    marks += [isinstance(b, dict) and "cache_control" in b for b in (sc if isinstance(sc, list) else [sc])]
    for m in messages[1:]:
        c = m.get("content")
        marks.append("cache_control" in m or (isinstance(c, list) and any("cache_control" in b for b in c)))
    assert len(marks) == len(ser)
    return [(t, n, k) for (t, n), k in zip(ser, marks)]


class Cache:
    def __init__(self):
        self.store = set()

    def bill(self, req):
        keys, h = [], hashlib.sha1()
        for t, _, _ in req:
            h.update(t.encode()); keys.append(h.hexdigest())
        last_bp = max((i for i, (_, _, k) in enumerate(req) if k), default=-1)
        read_i = max((i for i, k in enumerate(keys) if k in self.store), default=-1)
        tok = [n for _, n, _ in req]
        read = sum(tok[:read_i + 1])
        write = sum(tok[read_i + 1:last_bp + 1]) if last_bp > read_i else 0
        plain = sum(tok) - read - write
        for i, (_, _, k) in enumerate(req):
            if k:
                self.store.add(keys[i])
        return read, write, plain


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", required=True)
    ap.add_argument("--out", default=str(Path(tempfile.gettempdir()) / "cache-scenarios"))
    ap.add_argument("--chats", default="")
    ap.add_argument("--keep-images", type=int, default=None,
                    help="override history.KEEP_IMAGE_MESSAGES (e.g. 1000 = no eviction) to see the ceiling once finding 5 is fixed")
    ap.add_argument("--caching-model", default="anthropic/claude-opus-5")
    a = ap.parse_args()
    cache = Path(a.cache)
    if a.keep_images is not None:
        import history
        history.KEEP_IMAGE_MESSAGES = a.keep_images
    ids = [d.name for d in sorted(cache.iterdir()) if (d / "messages.jsonl").exists()]
    ids = [c for c in ids if not a.chats or c in a.chats.split(",")]
    result = {}
    for cid in ids:
        msgs = [json.loads(l) for l in (cache / cid / "messages.jsonl").read_text().splitlines() if l.strip()]
        work = Path(tempfile.mkdtemp(prefix="work-"))
        store = replay.FakeStore(cache, cid, work)
        resolve = lambda ref: store.resolve(cid, ref)
        call_info = {}
        for m in msgs:
            for tc in m.get("tool_calls") or []:
                try:
                    call_info[tc["id"]] = (tc["function"]["name"], json.loads(tc["function"]["arguments"] or "{}"))
                except Exception:
                    call_info[tc["id"]] = (tc["function"]["name"], {})
        caches = {k: Cache() for k in ("baseline", "a", "a+b")}
        tot = {k: dict(read=0, write=0, plain=0) for k in caches}
        steps = 0
        for i, m in enumerate(msgs):
            if m["role"] == "assistant" and not m.get("_ui_only"):
                model = m.get("_llm_model") or next((x.get("_llm_model") for x in msgs if x.get("_llm_model")), "")
                body = agent.llm_history(msgs[:i], True, resolve, model, mark_turns=True)
                for x in body:
                    x.pop("_turn_start", None)
                reqs = {}
                for name, rolling, tail_after in (("baseline", False, False), ("a", True, False), ("a+b", True, True)):
                    settings.ROLLING_CACHE_BREAKPOINT, settings.TAIL_AFTER_HISTORY = rolling, tail_after
                    sysm = system_prompt.system_message(store, cid, a.caching_model, {})
                    reqs[name] = system_prompt.outbound_messages(store, cid, [sysm, *body], a.caching_model, {})
                for name, req in reqs.items():
                    r, w, p = caches[name].bill(blocks(req))
                    tot[name]["read"] += r; tot[name]["write"] += w; tot[name]["plain"] += p
                steps += 1
            if m["role"] == "assistant":                       # effects become visible to the NEXT request
                for tc in m.get("tool_calls") or []:
                    n, args = call_info[tc["id"]]
                    if n == "run_python" and isinstance(args.get("code"), str):
                        t = work / "generators" / f"run-{m['id']:05d}.py"; t.parent.mkdir(parents=True, exist_ok=True)
                        t.write_text(args["code"])
                    if n == "write_file" and isinstance(args.get("path"), str):
                        p = args["path"].removeprefix("output/")
                        if not p.startswith("/") and ".." not in p:
                            t = work / p; t.parent.mkdir(parents=True, exist_ok=True)
                            with t.open("a" if args.get("append") else "w") as f:
                                f.write(args.get("content", ""))
        for k, v in tot.items():
            v["cost"] = v["read"] * READ + v["write"] * WRITE + v["plain"]
        result[cid] = dict(steps=steps, **tot)
        print(cid, steps, {k: round(v["cost"] / 1e6, 2) for k, v in tot.items()}, file=sys.stderr)
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    (out / "cache_scenarios.json").write_text(json.dumps(result, indent=1))
    print(f"{'chat':24}{'steps':>6}{'baseline':>10}{'a':>9}{'a vs base':>10}{'a+b':>9}{'a+b vs base':>12}   (M cache-cost-equivalent tokens)")
    for cid, r in result.items():
        b, x, y = (r[k]["cost"] for k in ("baseline", "a", "a+b"))
        print(f"{cid:24}{r['steps']:>6}{b/1e6:>10.2f}{x/1e6:>9.2f}{(x/b-1)*100:>9.0f}%{y/1e6:>9.2f}{(y/b-1)*100:>11.0f}%")


if __name__ == "__main__":
    main()
