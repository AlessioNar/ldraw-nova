#!/usr/bin/env python
"""Offline effect of history.py's tool-output masking and batched image eviction on the recorded chats.

Usage (from app/web/backend, same env as replay.py):
  LDRAW_NOVA_TOOLKIT_DIR=<toolkit> python ../../../docs/plans/done/loop-perf/replay_history.py --cache <chats dir> [--chats ID,ID]

For every recorded assistant step it builds the history body with history.llm_history under four settings
  base    old behaviour (no masking, one image evicted per render)
  mask    LDRAW_NOVA_MASK_OLD_TOOL_OUTPUT=1 with the defaults
  images  batched image eviction (default batch)
  both    mask + images
and reports, per chat: summed request tokens (chars/4, images by dimensions, system block excluded
as it is identical in every scenario), stubs in the final request and their size, prefix reuse (bytes
identical to the previous step's request, as in replay.py), and the steps where the prefix breaks
and why (a masked result, an evicted render, other). Estimates only: no tokenizer, no cache.
"""
from __future__ import annotations

import argparse, json, os, sys
from pathlib import Path

sys.argv, argv = sys.argv[:1], sys.argv[1:]
import replay  # noqa: E402  (same directory; imports the real app modules)

SCENARIOS = {
    "base":   {"LDRAW_NOVA_MASK_OLD_TOOL_OUTPUT": "0", "LDRAW_NOVA_IMAGE_EVICTION_BATCH": "1"},
    "mask":   {"LDRAW_NOVA_MASK_OLD_TOOL_OUTPUT": "1", "LDRAW_NOVA_IMAGE_EVICTION_BATCH": "1"},
    "images": {"LDRAW_NOVA_MASK_OLD_TOOL_OUTPUT": "0", "LDRAW_NOVA_IMAGE_EVICTION_BATCH": None},
    "both":   {"LDRAW_NOVA_MASK_OLD_TOOL_OUTPUT": "1", "LDRAW_NOVA_IMAGE_EVICTION_BATCH": None},
}


def setenv(cfg):
    for k, v in cfg.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def run(msgs, cid, cache):
    import history
    resolve = lambda ref: Path(os.path.normpath(cache / cid / ref))  # noqa: E731
    res = {}
    for name, cfg in SCENARIOS.items():
        setenv(cfg)
        prev = None
        tot = lcp_sum = prev_sum = 0
        breaks = dict(mask=0, image=0, other=0)
        n = 0
        last = None
        for i, m in enumerate(msgs):
            if m["role"] != "assistant" or m.get("_ui_only"):
                continue
            model = m.get("_llm_model") or next((x.get("_llm_model") for x in msgs if x.get("_llm_model")), "")
            body = history.llm_history(msgs[:i], True, resolve, model, mark_turns=True)
            for x in body:
                x.pop("_turn_start", None)
            ser = replay.serialize([{"role": "system", "content": "S"}, *body])
            tot += sum(t for _, t in ser)
            if prev is not None:
                l, p = replay.lcp_tokens(ser, prev), sum(t for _, t in prev)
                lcp_sum += l; prev_sum += p
                if l < p * 0.999:
                    k = next((k for k, (a, b) in enumerate(zip(ser, prev)) if a[0] != b[0]), min(len(ser), len(prev)))
                    old = prev[k][0] if k < len(prev) else ""
                    new = ser[k][0] if k < len(ser) else ""
                    breaks["mask" if "[Output of" in new and "[Output of" not in old else
                           "image" if "IMG" in old else "other"] += 1
            prev, last, n = ser, body, n + 1
        stubs = [x["content"] for x in last if x["role"] == "tool" and x["content"].startswith("[Output of")]
        res[name] = dict(tokens=tot, steps=n, reuse=lcp_sum / prev_sum if prev_sum else 0, breaks=breaks,
                         stubs=len(stubs), stub_chars=sum(map(len, stubs)))
    setenv({k: None for k in SCENARIOS["base"]})
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", required=True)
    ap.add_argument("--chats", default="")
    ap.add_argument("--json", default="")
    a = ap.parse_args(argv)
    cache = Path(a.cache)
    only = set(filter(None, a.chats.split(",")))
    out = {}
    for d in sorted(cache.iterdir()):
        if not (d / "messages.jsonl").exists() or (only and d.name not in only):
            continue
        msgs = [json.loads(l) for l in (d / "messages.jsonl").read_text().splitlines() if l.strip()]
        out[d.name] = r = run(msgs, d.name, cache)
        b = r["base"]
        print(f"\n{d.name}  ({b['steps']} steps)")
        print(f"  {'scenario':8} {'sum req Mtok':>12} {'vs base':>8} {'prefix reuse':>13} {'break steps (mask/image/other)':>32} {'stubs@end':>10} {'stub chars':>11}")
        for k, v in r.items():
            br = v["breaks"]
            print(f"  {k:8} {v['tokens']/1e6:12.2f} {v['tokens']/b['tokens']-1:+8.1%} {v['reuse']:13.1%} "
                  f"{br['mask']:>10}/{br['image']}/{br['other']:<19} {v['stubs']:10} {v['stub_chars']:11}")
    if a.json:
        Path(a.json).write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
