#!/usr/bin/env python
"""Summarise replay.json (from replay.py) into tables. Pure stdlib; run anywhere.
  python analyze.py [replay.json] [chats_cache_dir]
Token figures are chars/4 estimates; images use w*h/750. Cost units are 'input-token
equivalents' under Anthropic-style multipliers (read 0.1x, 5-minute write 1.25x, miss 1.0x)."""
import collections, json, statistics as st, sys
from pathlib import Path

R = json.loads(Path(sys.argv[1] if len(sys.argv) > 1 else "out/replay.json").read_text())
READ, WRITE = 0.1, 1.25
P = lambda *a: print(*a)


def pct(x, tot): return f"{100*x/tot:5.1f}%" if tot else "  -  "


P("## Per-chat request size (estimated tokens, chars/4)\n")
P("| chat | model | steps | first req | median req | max req | sum of all requests | stable sys | volatile sys (max) |")
P("|---|---|---|---|---|---|---|---|---|")
tot_all = collections.Counter()
for cid, d in R.items():
    S = d["steps"]
    if not S: continue
    tt = [s["total_tokens"] for s in S]
    stable = (S[0]["sys_stable_chars"] or 0) // 4
    vol = max((s["sys_volatile_chars"] or 0) for s in S) // 4
    P(f"| {cid} | {S[0]['model'].split('/',1)[-1]} | {len(S)} | {tt[0]:,} | {int(st.median(tt)):,} | {max(tt):,} | {sum(tt):,} | {stable:,} | {vol:,} |")

P("\n## Composition of what is re-sent (sum over all steps; share of total est. tokens)\n")
P("| chat | system | tool output | assistant text+tool args | reasoning re-sent | user text | images |")
P("|---|---|---|---|---|---|---|")
for cid, d in R.items():
    S = d["steps"]
    if not S: continue
    c = collections.Counter()
    for s in S:
        c["sys"] += s["sys_chars"] // 4
        for k, v in s["cls"].items(): c[k] += v // 4
        c["img"] += s["image_tokens"]
    T = sum(c.values())
    for k, v in c.items(): tot_all[k] += v
    P(f"| {cid} | {pct(c['sys'],T)} | {pct(c['tool_output'],T)} | {pct(c['assistant_text']+c['tool_call_args'],T)} | {pct(c['reasoning_resent'],T)} | {pct(c['user_text'],T)} | {pct(c['img'],T)} |")
T = sum(tot_all.values())
P(f"| ALL | {pct(tot_all['sys'],T)} | {pct(tot_all['tool_output'],T)} | {pct(tot_all['assistant_text']+tot_all['tool_call_args'],T)} | {pct(tot_all['reasoning_resent'],T)} | {pct(tot_all['user_text'],T)} | {pct(tot_all['img'],T)} |")

P("\n## Prefix stability between consecutive requests\n")
P("lcp = tokens of the byte-identical leading blocks shared with the previous request. 'ceiling' = prev request fully reusable.\n")
P("| chat | steps | mean reuse of prev req | steps with >=95% reuse | system block changed (non-caching layout: stable+volatile in one string) | volatile tail changed | steps where trimming/eviction broke prefix before the last 3 blocks |")
P("|---|---|---|---|---|---|---|")
cost = {}
for cid, d in R.items():
    S = d["steps"]
    if len(S) < 2: continue
    reuse = [s["lcp_tokens"] / s["prev_tokens"] for s in S[1:] if s["prev_tokens"]]
    hi = sum(r >= 0.95 for r in reuse)
    sysbrk = sum(1 for s in S[1:] if s["break_block"] == 0)
    vol = sum(s["volatile_changed"] for s in S[1:])
    early = sum(1 for s in S[1:] if s["break_block"] is not None and s["break_block"] > 0 and s["break_block"] < s["n_blocks"] - 3 and s["break_block"] < 10**6
                and s["lcp_tokens"] < s["prev_tokens"] * 0.95 and s["break_block"] != 0)
    P(f"| {cid} | {len(S)} | {100*st.mean(reuse):.0f}% | {hi}/{len(reuse)} | {sysbrk} | {vol} | {early} |")

    # cost model (anthropic-like) ---------------------------------------------------------
    none = sum(s["total_tokens"] for s in S)
    stable = (S[0]["sys_stable_chars"] or 0) // 4
    cur = 0.0
    ideal = 0.0
    ideal_ok = 0.0   # ideal, but with volatile system tail moved after the history (no break from file writes)
    for k, s in enumerate(S):
        t = s["total_tokens"]
        # current: breakpoint after the stable system prefix only
        cur += (t - stable) + (stable * (READ if k and not s["stable_changed"] else WRITE))
        # ideal rolling breakpoint at end of every request
        lcp = s["lcp_tokens"] if k else 0
        ideal += lcp * READ + (t - lcp) * WRITE
        if k and s.get("break_cause") == "system_tail":
            lcp = s["prev_tokens"]      # volatile tail moved behind the history: it no longer breaks the prefix
        ideal_ok += lcp * READ + (t - lcp) * WRITE
    cost[cid] = (none, cur, ideal, ideal_ok)
P("\n## Input-cost counterfactual (token-equivalents, Anthropic-style multipliers; only valid if the model supports caching)\n")
P("| chat | no cache | A: current (stable-system breakpoint only) | B: + rolling breakpoint on last message | C: B + volatile tail moved behind history | B vs A | C vs A |")
P("|---|---|---|---|---|---|---|")
for cid, (n, c, i, k) in cost.items():
    P(f"| {cid} | {n:,.0f} | {c:,.0f} | {i:,.0f} | {k:,.0f} | {100*(1-i/c):.0f}% | {100*(1-k/c):.0f}% |")

P("\n## Tool outputs (chars; est tokens = /4)\n")
byt = collections.defaultdict(list)
big = []
for cid, d in R.items():
    for t in d["tools"]:
        key = t["name"] + (f":{t['sub']}" if t.get("sub") else "")
        byt[key].append(t["chars"]); big.append((t["chars"], cid, t["id"], key, t.get("path"), t["head"]))
P("| tool[:toolkit subcommand] | calls | total chars | median | p95 | max |")
P("|---|---|---|---|---|---|")
for k, v in sorted(byt.items(), key=lambda kv: -sum(kv[1]))[:14]:
    v = sorted(v)
    P(f"| {k} | {len(v)} | {sum(v):,} | {int(st.median(v)):,} | {v[int(.95*(len(v)-1))]:,} | {v[-1]:,} |")
P("\nTop 12 single tool results:\n")
for c, cid, mid, k, path, head in sorted(big, reverse=True)[:12]:
    P(f"- {c:,} chars (~{c//4:,} tok) {cid} msg#{mid} {k} {path or ''} | {head[:90]}")

P("\n## Latency (recorded created_at gaps)\n")
for cid, d in R.items():
    llm = [g for k, g, j in d["latency"] if k == "llm"]
    tool = [g for k, g, j in d["latency"] if k.startswith("tool:")]
    long_ = [g for g in llm if g > 300]
    if llm:
        P(f"- {cid}: LLM waits n={len(llm)} sum={sum(llm)/60:.1f} min median={st.median(llm):.1f}s p95={sorted(llm)[int(.95*(len(llm)-1))]:.1f}s; "
          f"tool/gap sum={sum(tool)/60:.1f} min; gaps>300s (cache TTL): llm={len(long_)}")
byk = collections.defaultdict(float)
for d in R.values():
    for k, g, j in d["latency"]:
        byk[k] += g
P("\nTotal seconds by kind (all chats; gaps include user approval waits for tools):")
for k, v in sorted(byk.items(), key=lambda kv: -kv[1])[:10]:
    P(f"- {k}: {v/60:.1f} min")


# ---- sections that need the raw messages (cache dir) ---------------------------------------
import re
cache = Path(sys.argv[2]) if len(sys.argv) > 2 else None
if cache:
    GUIDES = {"instructions.md", "docs/agent/ldraw-reference.md", "docs/agent/geometry.md", "docs/agent/visual-design.md",
              "docs/agent/reference-discovery.md", "docs/agent/validation.md"}
    P("\n## Tool results: duplicates of the system prompt, repeats, and cost of carrying them (re-send weight)\n")
    P("Re-send weight = est. tokens of the result x number of later requests (ignores trimming) = what carrying it costs in total.\n")
    agg = collections.Counter(); rows = []
    for cid, d in R.items():
        msgs = [json.loads(l) for l in (cache / cid / "messages.jsonl").read_text().splitlines() if l.strip()]
        step_idx = [s["i"] for s in d["steps"]]
        calls = {}
        for m in msgs:
            for tc in m.get("tool_calls") or []:
                try: calls[tc["id"]] = (tc["function"]["name"], json.loads(tc["function"]["arguments"] or "{}"))
                except Exception: calls[tc["id"]] = (tc["function"]["name"], {})
        seen = {}
        for j, m in enumerate(msgs):
            if m["role"] != "tool": continue
            nm, args = calls.get(m["tool_call_id"], ("?", {}))
            later = sum(1 for i in step_idx if i > j)
            toks = len(m["content"] or "") // 4
            w = toks * later
            agg["all_tool_weight"] += w
            key = nm + (":" + args["arguments"][0] if nm == "run_toolkit" and args.get("arguments") else "")
            agg["w:" + key] += w
            path = args.get("path") if nm == "read_file" else None
            if path:
                norm = re.sub(r"^.*?/(docs/|instructions)", r"\1", path)
                if norm in GUIDES:
                    agg["dup_sys_weight"] += w; agg["dup_sys_n"] += 1; agg["dup_sys_toks"] += toks
                    rows.append((w, cid, m["id"], f"read_file {norm} (already in system prompt)"))
                if (path, args.get("offset", 0)) in seen:
                    agg["reread_weight"] += w; agg["reread_n"] += 1
                seen[(path, args.get("offset", 0))] = j
            rows.append((w, cid, m["id"], f"{key} {path or ''} {toks:,} tok x {later} later requests"))
    T = agg["all_tool_weight"]
    P(f"- total tool-result re-send weight: {T:,} token-requests")
    P(f"- read_file of text already embedded in the system prompt: {agg['dup_sys_n']} calls, {agg['dup_sys_toks']:,} tok, weight {agg['dup_sys_weight']:,} ({pct(agg['dup_sys_weight'],T)})")
    P(f"- read_file of the same path+offset twice in a chat: {agg['reread_n']} calls, weight {agg['reread_weight']:,} ({pct(agg['reread_weight'],T)})")
    P("- weight by tool:")
    for k, v in sorted(((k[2:], v) for k, v in agg.items() if k.startswith("w:")), key=lambda kv: -kv[1])[:8]:
        P(f"  - {k}: {v:,} ({pct(v,T)})")
    P("- top 10 single results by weight:")
    for w, cid, mid, desc in sorted(rows, reverse=True)[:10]:
        P(f"  - {w:,}  {cid} msg#{mid} {desc}")

# ---- reasoning payload and latency vs request size -----------------------------------------
P("\n## Reasoning blocks re-sent (OpenRouter reasoning_details)\n")
for cid, d in R.items():
    S = d["steps"]
    if not S: continue
    ov = sum(s["cls"].get("reasoning_wire_overhead", 0) for s in S)
    txt = sum(s["cls"].get("reasoning_resent", 0) for s in S)
    if txt or ov:
        mb = [s["wire_bytes"] / 1e6 for s in S]
        P(f"- {cid}: reasoning text re-sent {txt/4/1e6:.1f}M tok-equiv, JSON framing {ov/1e6:.0f}M chars on the wire; "
          f"request body median {st.median(mb):.1f} MB, max {max(mb):.1f} MB; trimmed steps: {sum(1 for s in S if s['removed'])}")
P("\n## litellm.token_counter vs this harness (what bounded_history thinks the request is)\n")
for cid, d in R.items():
    rows_ = [(s["litellm_count"], s["total_tokens"], s["budget"]) for s in d["steps"] if s.get("litellm_count")]
    if rows_:
        P(f"- {cid}: budget {rows_[-1][2]:,}; last sampled: litellm {rows_[-1][0]:,} vs harness {rows_[-1][1]:,}; max litellm {max(r[0] for r in rows_):,}")

P("\n## LLM wait vs request size (correlation only)\n")
def ols(X, y):
    # y = b0 + b1 x1 + b2 x2 by normal equations
    n = len(y)
    A = [[1, a, b] for a, b in X]
    ata = [[sum(r[i]*r[j] for r in A) for j in range(3)] for i in range(3)]
    aty = [sum(r[i]*v for r, v in zip(A, y)) for i in range(3)]
    for c in range(3):
        p = max(range(c, 3), key=lambda r: abs(ata[r][c])); ata[c], ata[p] = ata[p], ata[c]; aty[c], aty[p] = aty[p], aty[c]
        for r in range(3):
            if r != c:
                f = ata[r][c] / ata[c][c]
                ata[r] = [a - f*b for a, b in zip(ata[r], ata[c])]; aty[r] -= f * aty[c]
    return [aty[i] / ata[i][i] for i in range(3)]
for cid, d in R.items():
    S = [s for s in d["steps"] if s.get("wait") and s["wait"] < 3600]
    if len(S) < 20: continue
    X = [(s["total_tokens"] / 1e3, s["out_chars"] / 1e3) for s in S]; y = [s["wait"] for s in S]
    b0, b1, b2 = ols(X, y)
    q = sorted(S, key=lambda s: s["total_tokens"]); h = len(q)//3
    P(f"- {cid}: wait ~ {b0:.1f}s + {b1:.2f}s per 1k request tokens + {b2:.2f}s per 1k output chars (n={len(S)}); "
      f"median wait first third of steps by size {st.median(s['wait'] for s in q[:h]):.0f}s, last third {st.median(s['wait'] for s in q[-h:]):.0f}s")

P("\n## Why the byte-identical prefix ended early (tokens that a rolling cache breakpoint would have had to re-write)\n")
P("| chat | steps | lost tokens total | system_tail (work listing/NOTES.md) | image_eviction (KEEP_IMAGE_MESSAGES) | trim | other | events: sys/img/trim/other |")
P("|---|---|---|---|---|---|---|---|")
for cid, d in R.items():
    lost = collections.Counter(); n = collections.Counter()
    for s in d["steps"][1:]:
        c = s.get("break_cause")
        if c:
            lost[c] += s["prev_tokens"] - s["lcp_tokens"]; n[c] += 1
    tot = sum(lost.values())
    P(f"| {cid} | {len(d['steps'])} | {tot:,} | {lost['system_tail']:,} | {lost['image_eviction']:,} | {lost['trim']:,} | {lost['other']:,} | {n['system_tail']}/{n['image_eviction']}/{n['trim']}/{n['other']} |")

P("\n## Slowest tool calls (recorded gap before the tool message was saved)\n")
allt = sorted(((g, cid, j, k) for cid, d in R.items() for k, g, j in d["latency"] if k.startswith("tool:")), reverse=True)[:8]
for g, cid, j, k in allt:
    P(f"- {g/60:.1f} min  {cid} msg index {j} {k}")
P("\nLLM waits over 5 minutes (Anthropic 5-minute cache TTL would expire between tool end and next request only if the TOOL gap exceeds it; LLM time itself is within one request):")
for cid, d in R.items():
    big = [(g, j) for k, g, j in d["latency"] if k == "llm" and g > 300]
    if big: P(f"- {cid}: {len(big)} waits >5 min, max {max(big)[0]/60:.1f} min")

if cache:
    P("\n## What-if simulations (offline, token-count only)\n")
    P("### Mask old bulky tool results (keep first/last 350 chars) once they are older than 10 assistant steps and over 2000 chars\n")
    P("| chat | sum of requests | saved | % | results masked |")
    P("|---|---|---|---|---|")
    for cid, d in R.items():
        msgs = [json.loads(l) for l in (cache / cid / "messages.jsonl").read_text().splitlines() if l.strip()]
        step_idx = [s["i"] for s in d["steps"]]
        saved = 0; n = 0
        for j, m in enumerate(msgs):
            if m["role"] != "tool": continue
            c = len(m["content"] or "")
            if c <= 2000: continue
            later = [i for i in step_idx if i > j]
            old = max(0, len(later) - 10)
            if old: n += 1
            saved += old * (c - 700) // 4
        tot = sum(s["total_tokens"] for s in d["steps"])
        P(f"| {cid} | {tot:,} | {saved:,} | {100*saved/tot:.0f}% | {n} |")
    P("\n### Coalesce adjacent reasoning_details deltas (same type/format/index) before saving\n")
    for cid in R:
        msgs = [json.loads(l) for l in (cache / cid / "messages.jsonl").read_text().splitlines() if l.strip()]
        before = after = 0; nb = na = 0
        for m in msgs:
            rd = m.get("_reasoning_details")
            if not rd: continue
            before += len(json.dumps(rd)); nb += len(rd)
            out = []
            for dlt in rd:
                key = lambda x: (x.get("type"), x.get("format"), x.get("index"), x.get("id"), x.get("signature") is None)
                if out and key(out[-1]) == key(dlt) and dlt.get("type") == "reasoning.text" and dlt.get("signature") is None and out[-1].get("signature") is None:
                    out[-1] = {**out[-1], "text": (out[-1].get("text") or "") + (dlt.get("text") or "")}
                else:
                    out.append(dict(dlt))
            after += len(json.dumps(out)); na += len(out)
        if before:
            P(f"- {cid}: stored blocks {nb:,} -> {na:,}; JSON {before/1e6:.1f} MB -> {after/1e6:.2f} MB ({100*after/before:.1f}%)")
