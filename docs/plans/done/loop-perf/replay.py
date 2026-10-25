#!/usr/bin/env python
"""Offline replay of recorded chats through the real history-building code.

Usage (from app/web/backend so imports resolve):
  LDRAW_NOVA_TOOLKIT_DIR=<toolkit checkout> python ../../../docs/plans/done/loop-perf/replay.py \
      [--rev b24c492c] [--cache DIR] [--out DIR] [--chats ID,ID] [--no-bound]

What it does, per recorded assistant step (= one LLM request in the live loop):
  messages = [agents.system_message(...), *agent.llm_history(history_before_step, ...)]
  messages, removed = inference.bounded_history(messages, model, budget)
then measures the payload. All token figures are chars/4 (images: w*h/750 capped, the
Anthropic rule of thumb) - an ESTIMATE, not a tokenizer count. Latency figures come
from the recorded created_at stamps (LLM wait = assistant.created_at - previous message).

Nothing under app/ is modified. The work folder (work_listing / NOTES.md in the system
prompt) is rebuilt in a temp dir from the write_file calls in the log, so the volatile
system tail is approximate (run_python/run_toolkit outputs that created files are unknown).
Outputs JSON (per step + summary) into --out.
"""
from __future__ import annotations

import argparse, collections, json, os, struct, subprocess, sys, tempfile, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path[:0] = [os.getcwd(), str(REPO / "app")]

import agent, inference, providers, model_catalog, settings  # noqa: E402  (real app modules)

CPT = 4  # chars per token


def sh(*a, binary=False):
    r = subprocess.run(["git", "-C", str(REPO), *a], capture_output=True, check=True)
    return r.stdout if binary else r.stdout.decode()


def extract(rev: str, cache: Path, only: set[str] | None) -> list[str]:
    try:
        ids = sorted({p.split("/")[3] for p in sh("ls-tree", "-r", "--name-only", rev, "app/data/chats").split("\n")
                      if p.endswith("messages.jsonl")})
    except subprocess.CalledProcessError:
        # The chats were purged from git history; use a pre-populated --cache
        # (one folder per chat id holding messages.jsonl and its images).
        ids = sorted(d.name for d in cache.iterdir() if (d / "messages.jsonl").exists())
    for cid in ids:
        if only and cid not in only:
            continue
        d = cache / cid
        if (d / "messages.jsonl").exists():
            continue
        d.mkdir(parents=True, exist_ok=True)
        for p in sh("ls-tree", "-r", "--name-only", rev, f"app/data/chats/{cid}").split("\n"):
            if p:
                t = d / p.split(f"{cid}/", 1)[1]
                t.parent.mkdir(parents=True, exist_ok=True)
                t.write_bytes(sh("show", f"{rev}:{p}", binary=True))
    return [c for c in ids if not only or c in only]


def png_size(path: Path):
    try:
        with path.open("rb") as f:
            h = f.read(24)
        return struct.unpack(">II", h[16:24])
    except Exception:
        return None


def image_tokens(w, h):
    s = min(1.0, 1568 / max(w, h))
    return int(w * s * h * s / 750) + 1


def url_tokens(url: str) -> int:
    """Image tokens from the data URL header (PNG IHDR); 1500 for anything else (user JPEG photos)."""
    import base64
    try:
        if url.startswith("data:image/png"):
            raw = base64.b64decode(url.split(",", 1)[1][:48] + "==")
            w, h = struct.unpack(">II", raw[16:24])
            return image_tokens(w, h)
    except Exception:
        pass
    return 1500


def block_chars(c):
    return len(c) if isinstance(c, str) else sum(len(b.get("text", "")) for b in c if b.get("type") == "text")


class FakeStore:
    def __init__(self, root: Path, chat_id: str, work: Path):
        self.root, self.cid, self.work = root, chat_id, work
    def work_dir(self, _): return self.work
    def chat_dir(self, _): return self.root / self.cid
    def resolve(self, _cid, ref): return Path(os.path.normpath(self.root / self.cid / ref))


REASONING_KEYS = ("reasoning_details", "reasoning_content", "thinking_blocks", "provider_specific_fields")


def reasoning_text_chars(m) -> int:
    """Characters of actual reasoning content in a message (what a provider would tokenise),
    as opposed to the JSON wire size of the stored blocks (which includes per-delta framing)."""
    n = 0
    rd = m.get("reasoning_details")
    if isinstance(rd, list):
        for d in rd:
            n += len(d.get("text") or "") + len(d.get("summary") or "") + len(d.get("data") or "")
    if m.get("reasoning_content"):
        n += len(m["reasoning_content"])
    for tb in m.get("thinking_blocks") or []:
        n += len(tb.get("thinking") or "") + len(tb.get("signature") or "")
    return n


def reasoning_wire_chars(m) -> int:
    return sum(len(json.dumps(m[k])) for k in REASONING_KEYS if m.get(k))


def classify(msgs, img_tok):
    """Chars of the outgoing payload by role (text only; images counted separately)."""
    c = collections.Counter(); img = 0
    for m in msgs[1:]:
        cont = m.get("content")
        if isinstance(cont, list):
            for b in cont:
                if b.get("type") == "image_url":
                    img += 1
                    img_tok[0] += url_tokens(b["image_url"]["url"])
                elif b.get("type") == "text":
                    c["user_text" if m["role"] == "user" else "assistant_text"] += len(b["text"])
        elif cont:
            key = {"tool": "tool_output", "user": "user_text"}.get(m["role"], "assistant_text")
            c[key] += len(cont)
        for tc in m.get("tool_calls") or []:
            c["tool_call_args"] += len(tc["function"]["arguments"]) + len(tc["function"]["name"])
        c["reasoning_resent"] += reasoning_text_chars(m)      # tokenisable content
        c["reasoning_wire_overhead"] += max(0, reasoning_wire_chars(m) - reasoning_text_chars(m))  # JSON framing, NOT tokens
    return c, img


def lcp_tokens(a, b) -> int:
    n = 0
    for x, y in zip(a, b):
        if x[0] != y[0]:
            break
        n += x[1]
    return n


def _strip(m):
    c = m.get("content")
    if isinstance(c, list):
        m = {**m, "content": [({"type": "image_url", "image_url": {"url": f"IMG{len(b['image_url']['url'])}:{hash(b['image_url']['url'])}"}}
                               if b.get("type") == "image_url" else b) for b in c]}
    return m


def _tok(m_json_text: str, m) -> int:
    t = (len(m_json_text) - max(0, reasoning_wire_chars(m) - reasoning_text_chars(m))) // CPT
    c = m.get("content") if isinstance(m, dict) else None
    if isinstance(c, list):
        for b in c:
            if b.get("type") == "image_url":
                t += url_tokens(b["image_url"]["url"]) - len(f"IMG") // CPT
    return t


def serialize(msgs):
    """[(block_json, est_tokens)] in request order; system split into its cache blocks.
    Images are replaced by a stable placeholder in the text and costed by dimensions."""
    out = []
    s = msgs[0]["content"]
    for b in (s if isinstance(s, list) else [s]):
        t = json.dumps(b, sort_keys=True)
        out.append((t, len(t) // CPT))
    for m in msgs[1:]:
        t = json.dumps(_strip(m), sort_keys=True)
        out.append((t, _tok(t, m)))
    return out


def lc(msgs, model):
    import litellm
    try:
        return litellm.token_counter(model=model, messages=msgs)
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rev", default="b24c492c")
    ap.add_argument("--cache", default=str(Path(tempfile.gettempdir()) / "loop-perf-chats"))
    ap.add_argument("--out", default=str(HERE / "out"))
    ap.add_argument("--chats", default="")
    ap.add_argument("--no-bound", action="store_true", help="skip bounded_history (token_counter is slow)")
    ap.add_argument("--caching-model", default="anthropic/claude-opus-5",
                    help="model string used to build system_message for the cache counterfactual")
    a = ap.parse_args()
    cache, out = Path(a.cache), Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    ids = extract(a.rev, cache, set(filter(None, a.chats.split(","))) or None)

    summary = {}
    for cid in ids:
        msgs = [json.loads(l) for l in (cache / cid / "messages.jsonl").read_text().splitlines() if l.strip()]
        work = Path(tempfile.mkdtemp(prefix="work-"))
        store = FakeStore(cache, cid, work)
        resolve = lambda ref: store.resolve(cid, ref)
        # tool name per call id, for output attribution
        call_info = {}
        for m in msgs:
            for tc in m.get("tool_calls") or []:
                try:
                    args = json.loads(tc["function"]["arguments"] or "{}")
                except Exception:
                    args = {}
                call_info[tc["id"]] = (tc["function"]["name"], args)

        steps = []; prev_cached = prev_plain = None
        written = 0
        def effects(m):
            # mirror the live loop: tool side effects on the work folder, so the volatile system tail evolves
            if m["role"] == "assistant":
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

        for i, m in enumerate(msgs):
            if m["role"] != "assistant" or m.get("_ui_only"):
                continue
            model = m.get("_llm_model") or next((x.get("_llm_model") for x in msgs if x.get("_llm_model")), "")
            hist = msgs[:i]
            # state of the work dir is as of *after* this step's own writes; undo is not worth it: use pre-step
            # by re-running write effects only for earlier messages (done incrementally above, so apply lag by 1)
            t0 = time.time()
            sysm = agent.system_message(store, cid, a.caching_model, {})
            sysm_plain = agent.system_message(store, cid, model, {})
            body = agent.llm_history(hist, True, resolve, model, mark_turns=True)
            full_c = [sysm, *body]
            full_p = [sysm_plain, *body]
            removed = 0; budget = None
            if not a.no_bound:
                budget = model_catalog.profile(model)["context_window"] or 200_000
                full_p, removed = inference.bounded_history(full_p, model, budget)
                full_c, _ = inference.bounded_history(full_c, a.caching_model, budget)
            else:
                for x in full_p + full_c:
                    x.pop("_turn_start", None)
            img_tok = [0]
            cls, nimg = classify(full_p, img_tok)
            itok = img_tok[0]
            sys_total = block_chars(sysm_plain["content"])
            text_chars = sum(v for k, v in cls.items() if k != 'reasoning_wire_overhead') + sys_total
            ser_c, ser_p = serialize(full_c), serialize(full_p)
            # cache counterfactuals, measured on the *request as built by the real code*
            # (1) current design: only block 0 (stable system) carries a breakpoint
            # (2) prefix reuse: longest byte-identical prefix with the previous request
            lcp = lcp_tokens(ser_p, prev_plain) if prev_plain else 0
            prev_len = sum(t for _, t in prev_plain) if prev_plain else 0
            # why the prefix broke: first differing block index
            brk = None
            if prev_plain:
                for k, (x, y) in enumerate(zip(ser_p, prev_plain)):
                    if x[0] != y[0]:
                        brk = k; break
                if brk is None:
                    brk = min(len(ser_p), len(prev_plain))
            # classify why the byte-identical prefix ended (only meaningful when it ended early)
            cause = None
            if prev_plain and lcp < prev_len * 0.999:
                sys_blocks = 2 if isinstance(sysm_plain["content"], list) else 1
                if removed:
                    cause = "trim"
                elif brk < sys_blocks:
                    cause = "system_tail"        # volatile work listing / NOTES.md changed
                elif brk < len(prev_plain) and "IMG" in prev_plain[brk][0]:
                    cause = "image_eviction"     # an older render message dropped by KEEP_IMAGE_MESSAGES
                else:
                    cause = "other"
            steps.append(dict(
                i=i, id=m["id"], model=model, ts=m["created_at"], removed=removed, n_msgs=len(full_p),
                sys_chars=sys_total, sys_stable_chars=ser_c[0][1] * CPT if isinstance(sysm["content"], list) else None,
                sys_volatile_chars=len(sysm["content"][1]["text"]) if isinstance(sysm["content"], list) else None,
                cls=dict(cls), n_images=nimg, image_tokens=itok,
                req_tokens=sum(t for _, t in ser_p), text_tokens=text_chars // CPT,
                total_tokens=text_chars // CPT + itok,
                wire_bytes=sum(len(json.dumps(_strip(x))) for x in full_p),
                out_chars=len(m.get('content') or '') + sum(len(tc['function']['arguments']) for tc in m.get('tool_calls') or []) + reasoning_text_chars(m),
                budget=(None if a.no_bound else budget),
                litellm_count=(lc(full_p, model) if (len(steps) % 10 == 0 or i == len(msgs) - 1) else None),
                wait=m['created_at'] - msgs[i - 1]['created_at'] if i else None,
                lcp_tokens=lcp, prev_tokens=prev_len, break_cause=cause,
                break_prev=(prev_plain[brk][0][:200] if cause and brk < len(prev_plain) else None),
                break_new=(ser_p[brk][0][:200] if cause and brk < len(ser_p) else None), break_block=brk, n_blocks=len(ser_p),
                volatile_changed=(prev_cached is not None and ser_c[1][0] != prev_cached[1][0]),
                stable_changed=(prev_cached is not None and ser_c[0][0] != prev_cached[0][0]),
            ))
            prev_plain, prev_cached = ser_p, ser_c
            effects(m)  # this step's writes become visible to the NEXT request only
        # per-message timing
        lat = []
        for j, m in enumerate(msgs):
            if m["role"] == "assistant" and j and not m.get("_ui_only"):
                lat.append(("llm", m["created_at"] - msgs[j - 1]["created_at"], j))
            elif m["role"] == "tool" and j:
                nm = m.get("name") or call_info.get(m["tool_call_id"], ("?",))[0]
                lat.append(("tool:" + nm, m["created_at"] - msgs[j - 1]["created_at"], j))
        tools = []
        for m in msgs:
            if m["role"] == "tool":
                nm, args = call_info.get(m["tool_call_id"], ("?", {}))
                sub = args.get("arguments", [None])[0] if nm == "run_toolkit" and args.get("arguments") else None
                tools.append(dict(id=m["id"], name=nm, sub=sub, chars=len(m["content"] or ""), path=args.get("path"),
                                  head=(m["content"] or "")[:120].replace("\n", " ")))
        summary[cid] = dict(steps=steps, latency=lat, tools=tools, n_msgs=len(msgs))
        print(cid, "steps", len(steps), file=sys.stderr)
    (out / "replay.json").write_text(json.dumps(summary))


if __name__ == "__main__":
    main()
