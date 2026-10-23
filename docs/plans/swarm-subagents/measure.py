#!/usr/bin/env python
"""Brief 01: measure prices, the baseline cost split and worker capability. See MEASUREMENTS.md for results.

Three subcommands (run from the repo root; the first two import the app's own pricing code, so they run inside
a throwaway container of the app image, read-only, never inside the running app container):

  prices      Offline. Provider page figures against model_catalog.PRICE_BOOK and litellm.get_model_info.
  baseline    Offline. Prices every recorded assistant step from its tokens with usage.step_cost_usd and splits
              the cost by what the step did (research / building / validation / other). Read-only on the chats.
  capability  Dry run by default (prints tasks, prompts, caps, worst-case cost; no network, no key).
              With --live it calls the provider: needs ANTHROPIC_API_KEY in the environment and costs money,
              hard-capped by --cap-eur (default 1.00 EUR at settings.eur_per_usd()).

Examples (PowerShell or Git Bash, from the repo root; MSYS_NO_PATHCONV=1 in Git Bash):
  docker run --rm -v "<repo>/app/web/backend:/app/web/backend:ro" -v "<repo>/docs/plans/swarm-subagents:/m:ro" \
      -v "<owner chats dir>:/chats:ro" -w /app/web/backend ldraw-nova-app python3 /m/measure.py baseline --chats-dir /chats
  python docs/plans/swarm-subagents/measure.py capability            # dry run, host python is enough
"""
from __future__ import annotations

import argparse
import asyncio
import collections
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

_here = Path(__file__).resolve()
REPO = _here.parents[3] if len(_here.parents) > 3 else Path.cwd()      # inside a container the script sits at /m/
BACKEND = REPO / "app" / "web" / "backend"
DEFAULT_CHATS = Path(r"C:\Users\aless\Desktop\Developer\lego\ldraw-nova\app\data\chats")
PAGE_URL = "https://platform.claude.com/docs/en/about-claude/pricing"
PAGE_DATE = "2026-10-23"

# --- 1. provider page figures (USD per 1M tokens), read by the owner's session on PAGE_DATE -------------------
# tier "base" is a prompt up to 100,000 tokens; "above" a longer prompt (only Haiku 5.5 has a surcharge).
# None = the figure was not restated in the verified facts (the multiplier 1.25x / 2x is the page's rule).
PAGE = {
    "anthropic/claude-haiku-5-5": {"base": {"in": 0.10, "out": 0.50, "hit": 0.01, "w5m": 0.125, "w1h": 0.20},
                                   "above": {"in": 0.50, "out": 2.50, "hit": 0.05, "w5m": 0.625, "w1h": 1.00}},
    "anthropic/claude-sonnet-5-5": {"base": {"in": 2.0, "out": 10.0, "hit": 0.10, "w5m": None, "w1h": None}},
    "anthropic/claude-opus-5-5": {"base": {"in": 4.0, "out": 20.0, "hit": 0.20, "w5m": None, "w1h": 8.0}},
    "anthropic/claude-haiku-4-5": {"base": {"in": 1.0, "out": 5.0, "hit": None, "w5m": None, "w1h": None}},
}


def page_row(model: str) -> dict:
    """The page figures as a PRICE_BOOK-shaped row (input, output, cache_read_x, tiers), for the offline dry run."""
    base = PAGE[model]["base"]
    row = {"input": base["in"], "output": base["out"], "cache_read_x": round((base["hit"] or 0.1 * base["in"]) / base["in"], 4)}
    if "above" in PAGE[model]:
        above = PAGE[model]["above"]
        row["tiers"] = [{"over_prompt_tokens": 100_000, "input": above["in"], "output": above["out"]}]
    return row


def local_step_cost(record: dict, row: dict, ttl: str = "1h") -> float:
    """Same arithmetic as the app's usage.step_cost_usd for an Anthropic model (checked against it in test_measure.py)."""
    read_x, write_x = row.get("cache_read_x", 0.1), (2.0 if ttl == "1h" else 1.25)
    fresh = max(record["prompt"] - record["cache_read"] - record["cache_write"], 0)
    weighted = fresh + record["cache_read"] * read_x + record["cache_write"] * write_x
    price_in, price_out = row["input"], row["output"]
    for tier in sorted(row.get("tiers") or [], key=lambda t: t["over_prompt_tokens"]):
        if record["prompt"] > tier["over_prompt_tokens"]:
            price_in, price_out = tier["input"], tier["output"]
    return (weighted * price_in + record["completion"] * price_out) / 1_000_000


def backend():
    """Import the app's modules (works inside the app image only: litellm and /app are needed)."""
    for path in (str(BACKEND), "/app/web/backend", "/app"):
        if path not in sys.path and os.path.isdir(path):
            sys.path.insert(0, path)
    try:
        import model_catalog
        import settings
        import usage
    except ImportError as exc:
        raise SystemExit(f"cannot import the app modules ({exc}). Run this inside a throwaway container of the "
                         "ldraw-nova-app image, see the usage at the top of this file.") from None
    return model_catalog, settings, usage


def cmd_prices(_args) -> int:
    model_catalog, _settings, usage = backend()
    import litellm
    print(f"Provider page: {PAGE_URL}, read {PAGE_DATE} (figures supplied verified by the owner's session)\n")
    print("| model | tier | page in/out | page cache hit | page 5m write | page 1h write | PRICE_BOOK in/out | PB hit | PB 5m write | PB 1h write | litellm in/out | litellm hit | litellm 5m write | litellm 1h write |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    problems: list[str] = []
    for model, tiers in PAGE.items():
        info = {}
        try:
            info = litellm.get_model_info(model)
        except Exception:  # noqa: BLE001
            problems.append(f"{model}: litellm.get_model_info has no entry")
        pricing = model_catalog.profile(model)["pricing"] or {}
        for tier, page in tiers.items():
            pb_in, pb_out = pricing.get("input"), pricing.get("output")
            if tier == "above":
                t = (pricing.get("tiers") or [{}])[0]
                pb_in, pb_out = t.get("input"), t.get("output")
            read_x = pricing.get("cache_read_x", 0.1)
            pb_hit = pb_in * read_x if pb_in is not None else None
            pb_w5, pb_w1 = (pb_in * 1.25, pb_in * 2.0) if pb_in is not None else (None, None)
            ll = (lambda key, info=info: round(info[key] * 1e6, 6) if info.get(key) is not None else None)
            ll_in, ll_out = (ll("input_cost_per_token"), ll("output_cost_per_token")) if tier == "base" else (None, None)
            ll_hit = ll("cache_read_input_token_cost") if tier == "base" else None
            ll_w5 = ll("cache_creation_input_token_cost") if tier == "base" else None
            ll_w1 = ll("cache_creation_input_token_cost_above_1hr") if tier == "base" else None
            fmt = lambda v: "n/a" if v is None else f"{v:g}"
            print(f"| {model} | {tier} | {fmt(page['in'])}/{fmt(page['out'])} | {fmt(page['hit'])} | {fmt(page['w5m'])} | {fmt(page['w1h'])} | "
                  f"{fmt(pb_in)}/{fmt(pb_out)} | {fmt(pb_hit)} | {fmt(pb_w5)} | {fmt(pb_w1)} | {fmt(ll_in)}/{fmt(ll_out)} | {fmt(ll_hit)} | {fmt(ll_w5)} | {fmt(ll_w1)} |")
            for label, a, b in (("input", page["in"], pb_in), ("output", page["out"], pb_out), ("cache hit", page["hit"], pb_hit),
                                ("5m write", page["w5m"], pb_w5), ("1h write", page["w1h"], pb_w1)):
                if a is not None and (b is None or abs(a - b) > 1e-9):
                    problems.append(f"PRICE_BOOK vs page, {model} {tier} {label}: page {a} vs PRICE_BOOK {b}")
            for label, a, b in (("input", page["in"], ll_in), ("output", page["out"], ll_out), ("cache hit", page["hit"], ll_hit),
                                ("5m write", page["w5m"], ll_w5), ("1h write", page["w1h"], ll_w1)):
                if tier == "base" and a is not None and b is not None and abs(a - b) > 1e-9:
                    problems.append(f"litellm vs page, {model} {label}: page {a} vs litellm {b}")
        if "above" in tiers and info.get("input_cost_per_token_above_200k_tokens") is None:
            problems.append(f"litellm has no above-100k-tokens tier for {model} (page: 5x input and output above 100,000 prompt tokens)")
    print("\nDisagreements:")
    print("\n".join(f"- {p}" for p in problems) or "- none")
    row = model_catalog.profile("anthropic/claude-haiku-5-5")["pricing"]
    sample = {"prompt": 150_000, "completion": 1_000, "cache_read": 100_000, "cache_write": 20_000}
    print(f"\nusage.step_cost_usd on a 150k-prompt Haiku 5.5 step (1h cache): ${usage.step_cost_usd(sample, row, 'anthropic/claude-haiku-5-5', '1h'):.6f}")
    print("Availability of Haiku 5.5 on the owner's account cannot be checked offline (needs a live call).")
    return 0


# --- 2. baseline split ----------------------------------------------------------------------------------------
RESEARCH_TOOLKIT = {"doctor", "discover", "search", "catalog", "examples", "study", "part", "inventory", "matrix", "spec", "extract", "help"}
VALIDATION_TOOLKIT = {"validate", "inspect", "cad-check", "connectors", "render", "compare-bom", "bom", "manual"}
BUILDING_TOOLKIT = {"build", "technic", "spaceship", "mechanism", "vehicle"}
VALIDATION_RE = re.compile(r"ldraw-agent\s+(validate|inspect|cad-check|connectors|render|compare-bom|bom\b|inventory\s+check|vehicle\s+check)")
BUILDING_RE = re.compile(r"ldraw-agent\s+(build|vehicle\s+(?!check|list|wheels)|technic)|\.plan\.json|write_text|json\.dump|sed\s+-i|generator|\.write\(")
CLASSES = ("research", "building", "validation", "other")


def classify_call(name: str, args: dict) -> str:
    """Class of one tool call. Judgement calls are listed in MEASUREMENTS.md."""
    if name == "run_toolkit":
        a = args.get("arguments") or ["?"]
        first, second = a[0], (a[1] if len(a) > 1 else "")
        if first == "inventory" and second == "check":
            return "validation"
        if first == "vehicle":
            return "validation" if second == "check" else "research" if second in ("list", "wheels") else "building"
        return ("validation" if first in VALIDATION_TOOLKIT else "building" if first in BUILDING_TOOLKIT
                else "research" if first in RESEARCH_TOOLKIT else "other")
    if name in ("run_shell", "run_python"):
        text = args.get("command") or args.get("code") or ""
        if BUILDING_RE.search(text):
            return "building"
        if VALIDATION_RE.search(text):
            return "validation"
        return "research"
    if name in ("my_parts", "buildable_models", "read_file", "list_files"):
        return "research"
    if name == "write_file":
        return "other" if re.search(r"\.(md|txt)$|notes|report", str(args.get("path", "")), re.I) else "building"
    if name in ("view_image", "publish_model"):
        return "validation"
    return "other"                                   # report_progress, delegate_task, editor_*, unknown


def classify_step(calls: list[tuple[str, dict]]) -> str:
    """A step takes the highest-priority class among its calls: building > validation > research; no calls = other."""
    kinds = {classify_call(n, a) for n, a in calls}
    for kind in ("building", "validation", "research", "other"):
        if kind in kinds:
            return kind
    return "other"


def load_steps(messages: list[dict]) -> list[dict]:
    steps = []
    for m in messages:
        if m.get("role") != "assistant":
            continue
        calls = []
        for tc in m.get("tool_calls") or []:
            try:
                args = json.loads(tc["function"]["arguments"] or "{}")
            except ValueError:
                args = {}
            calls.append((tc["function"]["name"], args))
        steps.append({"id": m.get("id"), "usage": m.get("_usage"), "model": m.get("_llm_model"), "calls": calls})
    return steps


def baseline_chat(chat_id: str, messages: list[dict], model_catalog, usage, ttl: str = "1h") -> dict:
    steps = load_steps(messages)
    by_class = {c: collections.Counter() for c in CLASSES}
    parts = collections.Counter()
    unpriced = 0
    positive_reads = [s["usage"]["cache_read"] for s in steps if s["usage"] and s["usage"].get("cache_read", 0) > 0]
    fixed = min(positive_reads) if positive_reads else 0       # smallest cache hit = the stable system+tools prefix
    fixed_tokens = history_tokens = rewrites = 0
    rewrite_cost = 0.0
    first = next((s for s in steps if s["usage"] and s["model"]), None)
    previous_prompt = 0
    counterfactual = {c: 0.0 for c in CLASSES}
    for s in steps:
        rec = s["usage"]
        if not rec or not s["model"]:
            unpriced += 1
            continue
        pricing = model_catalog.profile(s["model"])["pricing"]
        total = usage.step_cost_usd(rec, pricing, s["model"], ttl)
        if total is None:
            unpriced += 1
            continue
        out = usage.step_cost_usd({"prompt": 0, "completion": rec["completion"], "cache_read": 0, "cache_write": 0}, pricing, s["model"], ttl)
        rd = usage.step_cost_usd({"prompt": rec["cache_read"], "completion": 0, "cache_read": rec["cache_read"], "cache_write": 0}, pricing, s["model"], ttl)
        wr = usage.step_cost_usd({"prompt": rec["cache_write"], "completion": 0, "cache_read": 0, "cache_write": rec["cache_write"]}, pricing, s["model"], ttl)
        cls = classify_step(s["calls"])
        read = min(previous_prompt, rec["prompt"])                 # counterfactual: the cache always hits up to the previous prompt
        counterfactual[cls] += usage.step_cost_usd({"prompt": rec["prompt"], "completion": rec["completion"], "cache_read": read,
                                                    "cache_write": rec["prompt"] - read}, pricing, s["model"], ttl)
        previous_prompt = rec["prompt"]
        c = by_class[cls]
        c["steps"] += 1
        c["cost"] += total
        c["prompt"] += rec["prompt"]
        c["completion"] += rec["completion"]
        parts["output"] += out
        parts["cache_read"] += rd
        parts["cache_write"] += wr
        parts["fresh_input"] += total - out - rd - wr
        f = min(fixed, rec["prompt"])
        fixed_tokens += f
        history_tokens += rec["prompt"] - f
        if rec["cache_write"] > rec["prompt"] / 2 and s is not first:        # most of the prompt written again: the history prefix was lost
            rewrites += 1
            rewrite_cost += total
    return {"chat": chat_id, "models": sorted({s["model"] for s in steps if s["model"]}), "assistant_messages": len(steps),
            "unpriced": unpriced, "by_class": {k: dict(v) for k, v in by_class.items()}, "cost_parts": dict(parts),
            "fixed_prefix_tokens": fixed, "fixed_token_sum": fixed_tokens, "history_token_sum": history_tokens,
            "history_rewrite_steps": rewrites, "history_rewrite_cost": rewrite_cost, "counterfactual": counterfactual}


def fmt_chat(r: dict) -> list[str]:
    steps = sum(c.get("steps", 0) for c in r["by_class"].values())
    cost = sum(c.get("cost", 0) for c in r["by_class"].values())
    tokens_in = sum(c.get("prompt", 0) for c in r["by_class"].values())
    tokens_out = sum(c.get("completion", 0) for c in r["by_class"].values())
    lines = [f"| {r['chat']} | {','.join(m.split('/')[-1] for m in r['models'])} | {steps} (+{r['unpriced']} unpriced) | {tokens_in:,} | {tokens_out:,} | ${cost:.2f} |"]
    for c in CLASSES:
        d = r["by_class"][c]
        lines.append(f"|  - {c} | | {d.get('steps', 0)} | {d.get('prompt', 0):,} | {d.get('completion', 0):,} | ${d.get('cost', 0):.2f} ({100 * d.get('cost', 0) / cost:.0f}%) |" if cost else "")
    return lines


def cmd_baseline(args) -> int:
    model_catalog, settings, usage = backend()
    chats_dir = Path(args.chats_dir)
    results = []
    for path in sorted(chats_dir.glob("*/messages.jsonl")):
        messages = [json.loads(line) for line in path.read_text(encoding="utf8").splitlines() if line.strip()]
        results.append(baseline_chat(path.parent.name, messages, model_catalog, usage, args.ttl))
    if not results:
        raise SystemExit(f"no chats under {chats_dir}")
    rate = settings.eur_per_usd()
    print(f"Recorded chats read (read-only) from {chats_dir}; priced with usage.step_cost_usd, {args.ttl} cache writes, EUR at {rate}/USD, run {time.strftime('%Y-%m-%d')}\n")
    print("| chat | model | steps | prompt tokens | completion tokens | cost |")
    print("|---|---|---|---|---|---|")
    total = {c: collections.Counter() for c in CLASSES}
    for r in results:
        for line in fmt_chat(r):
            print(line)
        for c in CLASSES:
            total[c].update(r["by_class"][c])
    cost = sum(t["cost"] for t in total.values())
    print("\nOverall (all chats)\n")
    print("| class | steps | prompt tokens | completion tokens | cost USD | cost EUR | share of cost |")
    print("|---|---|---|---|---|---|---|")
    for c in CLASSES:
        t = total[c]
        print(f"| {c} | {t['steps']} | {t['prompt']:,} | {t['completion']:,} | ${t['cost']:.2f} | EUR {t['cost'] * rate:.2f} | {100 * t['cost'] / cost:.1f}% |")
    print(f"| all | {sum(t['steps'] for t in total.values())} | {sum(t['prompt'] for t in total.values()):,} | {sum(t['completion'] for t in total.values()):,} | ${cost:.2f} | EUR {cost * rate:.2f} | 100% |")
    print("\nCost by component (all chats, USD)")
    parts = collections.Counter()
    for r in results:
        parts.update(r["cost_parts"])
    print("| " + " | ".join(f"{k} ${v:.2f} ({100 * v / cost:.0f}%)" for k, v in parts.items()) + " |")
    print("\nFixed prompt versus history in each step's input")
    print("| chat | fixed prefix tokens (smallest cache hit) | fixed share of all input tokens | history+new share | steps that re-wrote the history | cost of those steps |")
    print("|---|---|---|---|---|---|")
    for r in results:
        fixed, hist = r["fixed_token_sum"], r["history_token_sum"]
        print(f"| {r['chat']} | {r['fixed_prefix_tokens']:,} | {100 * fixed / (fixed + hist):.0f}% | {100 * hist / (fixed + hist):.0f}% | {r['history_rewrite_steps']} | ${r['history_rewrite_cost']:.2f} |")
    cf = {c: sum(r["counterfactual"][c] for r in results) for c in CLASSES}
    cft = sum(cf.values())
    print("\nCounterfactual with a healthy rolling cache (assumption: every step reads the previous step's whole prompt, writes only the new tail)")
    print("| class | cost USD | share |")
    print("|---|---|---|")
    for c in CLASSES:
        print(f"| {c} | ${cf[c]:.2f} | {100 * cf[c] / cft:.1f}% |")
    print(f"| all | ${cft:.2f} (EUR {cft * rate:.2f}) | 100% |")
    if args.json:
        Path(args.json).write_text(json.dumps(results, indent=1))
    return 0


def cmd_history(args) -> int:
    """Step shares only (no _usage exists in those chats) for the loop-perf chats kept in git history."""
    ids = ["20260928-125940-5e681c", "20260929-213629-dd4aed", "20260930-201212-16f983", "20261003-100404-9e6e55", "20261003-110049-79b3d1"]
    print(f"Chats from git revision {args.rev} (docs/plans/loop-perf), read with git show; step counts only\n")
    print("| chat | assistant steps | with _usage | research | building | validation | other |")
    print("|---|---|---|---|---|---|---|")
    for cid in ids:
        raw = subprocess.run(["git", "show", f"{args.rev}:app/data/chats/{cid}/messages.jsonl"], capture_output=True, cwd=REPO, check=False)
        if raw.returncode:
            print(f"| {cid} | not found | | | | | |")
            continue
        steps = load_steps([json.loads(line) for line in raw.stdout.decode("utf8").splitlines() if line.strip()])
        kinds = collections.Counter(classify_step(s["calls"]) for s in steps)
        print(f"| {cid} | {len(steps)} | {sum(1 for s in steps if s['usage'])} | " + " | ".join(str(kinds[c]) for c in CLASSES[:4]) + " |")
    return 0


# --- 3. worker capability harness -----------------------------------------------------------------------------
LEAN_SYSTEM = """You are a worker agent building ONE LEGO sub-assembly for a planner. You have no other context.
Rules:
- Write exactly one LDraw file with write_file at the path the task gives. Units are LDU: stud pitch 20, brick height 24, plate height 8. LDraw Y points DOWN, so up is negative y; the ground plane is y=0.
- The file must be an MPD whose first line is `0 FILE <name>.ldr`, then `0 <title>`, `0 Name: <name>.ldr`, `0 Author: worker`, `0 !LDRAW_ORG Model`, then part lines (the validator rejects a file without the `0 FILE` block). Prefer CRLF line ends.
- A part line is: `1 <colour> x y z a b c d e f g h i <part>.dat` (a..i is the 3x3 rotation matrix row by row; identity is `1 0 0 0 1 0 0 0 1`).
- Use only real LDraw part numbers. `run_toolkit` runs the toolkit: `["part","3001"]` prints a part's bounds and connectors, `["catalog","parts","roof","--limit","10"]` finds parts, `["vehicle","wheels"]` lists wheel and tyre pairs, `["validate","<file>","--geometry","--detail","summary"]` checks your file (look at checks_passed and the diagnostics). Documentation is under docs/agent/ (list_files, read_file).
- Work efficiently: every step re-sends the conversation. Look up bounds once, write the whole file, validate, fix errors, stop.
- Finish by replying in at most 400 characters: what you built, the interface values you used, known defects."""

TASKS = [
    {"id": "chassis", "output": "output/chassis.mpd",
     "task": "Build a 4-wheel car chassis. Interface (relative to the submodel origin): axle centres at z=-60 (front) and z=+60 (rear); wheel centres at x=-70 and x=+70, y=-20 (the wheels rest on y=0); wheels symmetric about x=0. Use a wheel and tyre pair from the vehicle wheels list, a plate base about 4 studs wide on the axles, and make it one connected structure. Do not build a body."},
    {"id": "roof", "output": "output/roof.mpd",
     "task": "Build a 2x4 sloped roof: footprint 4 studs along x (80 LDU) by 2 studs along z (40 LDU), centred on x=0,z=0, lowest edge on y=0 (y is up negative). Red. Use roof slope bricks (45 degree) so the ridge runs along x in the middle. One connected structure, nothing outside the footprint."},
    {"id": "seats", "output": "output/seats.mpd",
     "task": "Build a pair of car seats side by side on a 2x4 plate base: base plate top at y=0, base centred on x=0,z=0 (80 LDU along x, 40 along z). Seat centres at x=-20 and x=+20, z=0, both facing -z. Use real seat or brick-with-back parts. One connected structure."},
    {"id": "window", "output": "output/window.mpd",
     "task": "Build a window frame submodel: opening 2 studs wide (x=-20..+20) and 3 bricks high (72 LDU), centred on x=0,z=0, bottom edge on y=0. White frame, transparent clear glass in the opening. Use a real window frame part plus glass, or bricks and plates around a window part, but keep the opening clear. One connected structure."},
    {"id": "door", "output": "output/door.mpd",
     "task": "Build a door submodel: a door frame 4 studs wide and 6 bricks (144 LDU) high, centred on x=0,z=0, bottom edge on y=0, with a door leaf in the frame. Use a real door frame part and matching door part; verify they fit (matching dimensions) with the part bounds. One connected structure."},
]
MAX_TOKENS = 16000                    # output cap per call (4096 truncated two of five tasks in the first live run)
RESULT_CHARS = 6000                   # tool results are cut to this before the model sees them
WORKER_TOOLS = ("list_files", "read_file", "write_file", "run_python", "run_toolkit")
CHARS_PER_TOKEN = 2.5                 # deliberately low, so the pre-call cost bound is an overestimate


def estimate_tokens(messages: list[dict], tool_schemas: list[dict]) -> int:
    return int((len(json.dumps(messages, default=str)) + len(json.dumps(tool_schemas))) / CHARS_PER_TOKEN) + 1


def worst_step_cost(prompt_tokens: int, row: dict) -> float:
    """Upper bound of one call: the whole prompt billed as a 1 h cache write, plus the full output allowance."""
    return local_step_cost({"prompt": prompt_tokens, "completion": MAX_TOKENS, "cache_read": 0, "cache_write": prompt_tokens}, row, "1h")


def worst_case_run(row: dict, max_steps: int, base_tokens: int) -> float:
    total = 0.0
    for k in range(max_steps):
        total += worst_step_cost(base_tokens + int(k * (RESULT_CHARS / CHARS_PER_TOKEN + MAX_TOKENS)), row)
    return total


@dataclass
class Budget:
    """Hard cap in EUR. `allows` is checked before every call; `add` after it."""
    cap_eur: float
    eur_per_usd: float
    spent_usd: float = 0.0
    last_usd: float = 0.0
    refused: str = ""

    @property
    def spent_eur(self) -> float:
        return self.spent_usd * self.eur_per_usd

    def allows(self, bound_usd: float) -> bool:
        bound_usd = max(bound_usd, 1.5 * self.last_usd)         # a step is never expected to cost much less than the last one
        if (self.spent_usd + bound_usd) * self.eur_per_usd > self.cap_eur:
            self.refused = f"cap {self.cap_eur:.2f} EUR: spent {self.spent_eur:.4f} EUR, next call could cost up to {bound_usd * self.eur_per_usd:.4f} EUR"
            return False
        return True

    def add(self, usd: float) -> None:
        self.spent_usd += usd
        self.last_usd = usd


def record_of(response) -> dict:
    """Token counters of one response (same fields and fallbacks as usage.usage_record)."""
    u = getattr(response, "usage", None)
    def num(*names):
        for n in names:
            v = getattr(u, n, None)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                return int(v)
        return 0
    details = getattr(u, "prompt_tokens_details", None)
    cached = getattr(details, "cached_tokens", None) if details is not None else None
    return {"prompt": num("prompt_tokens"), "completion": num("completion_tokens"),
            "cache_read": num("cache_read_input_tokens") or (int(cached) if isinstance(cached, (int, float)) else 0),
            "cache_write": num("cache_creation_input_tokens")}


@dataclass
class RunResult:
    task: str
    steps: int = 0
    stop: str = ""                                    # finished | truncated | step_cap | cap_eur | error
    finish_reasons: list = field(default_factory=list)
    verdicts: list = field(default_factory=list)      # harness validation after each change of the output file
    self_validations: int = 0
    tokens: dict = field(default_factory=lambda: {"prompt": 0, "completion": 0, "cache_read": 0, "cache_write": 0})
    cost_usd: float = 0.0
    prefix_cached: bool = False
    final: str = ""
    error: str = ""

    def summary(self, eur_per_usd: float) -> dict:
        first = self.verdicts[0] if self.verdicts else None
        repairs = (self.verdicts.index(True) if True in self.verdicts else len(self.verdicts)) if self.verdicts else None
        return {"task": self.task, "valid_first_try": first, "valid_final": bool(self.verdicts and self.verdicts[-1]),
                "repair_rounds": repairs, "output_versions": len(self.verdicts), "worker_ran_validate": self.self_validations,
                "steps": self.steps, "stop": self.stop, **{f"tokens_{k}": v for k, v in self.tokens.items()},
                "cost_usd": round(self.cost_usd, 6), "cost_eur": round(self.cost_usd * eur_per_usd, 6),
                "cache_read_total": self.tokens["cache_read"], "prefix_cached_from_step_2": self.prefix_cached,
                "final_reply": self.final[:400], "finish_reasons": self.finish_reasons, "error": self.error}


def with_cache_marks(system: str, messages: list[dict], marker: dict) -> list[dict]:
    """System prompt and the last message carry a cache breakpoint (the app's layout, minus the volatile trailer)."""
    out = [{"role": "system", "content": [{"type": "text", "text": system, "cache_control": marker}]}]
    out += [dict(m) for m in messages]
    last = out[-1]
    text = last.get("content")
    if isinstance(text, str) and text:
        last["content"] = [{"type": "text", "text": text, "cache_control": marker}]
    return out


async def run_task(task: dict, *, complete, dispatch, validate, cost_fn, row: dict, budget: Budget, schemas: list[dict],
                   max_steps: int = 20, marker: dict | None = None) -> RunResult:
    """One worker run. `complete(messages, tools)` -> response, `dispatch(name, args_json)` -> str,
    `validate(path)` -> bool or None (None: the file does not exist yet), `cost_fn(record)` -> USD or None.
    The budget is checked before every call, so a run never starts a call that could pass the cap."""
    result = RunResult(task["id"])
    marker = marker or {"type": "ephemeral"}
    history: list[dict] = [{"role": "user", "content": task["task"] + f"\n\nWrite the result to {task['output']}."}]
    last_seen = None
    for step in range(max_steps):
        messages = with_cache_marks(LEAN_SYSTEM, history, marker)
        if not budget.allows(worst_step_cost(estimate_tokens(messages, schemas), row)):
            result.stop = "cap_eur"
            result.error = budget.refused
            return result
        try:
            response = await complete(messages, schemas)
        except Exception as exc:  # noqa: BLE001 - report, never raise
            result.stop, result.error = "error", f"{exc.__class__.__name__}: {str(exc)[:300]}"
            return result
        rec = record_of(response)
        cost = cost_fn(rec)
        if cost is None:
            result.stop, result.error = "error", "step could not be priced"
            return result
        budget.add(cost)
        result.cost_usd += cost
        result.steps += 1
        for k, v in rec.items():
            result.tokens[k] += v
        if step >= 1 and rec["cache_read"] > 0:
            result.prefix_cached = True
        reply = response.choices[0].message
        result.finish_reasons.append(getattr(response.choices[0], "finish_reason", None))
        calls = [{"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments or "{}"}}
                 for tc in (getattr(reply, "tool_calls", None) or [])]
        history.append({"role": "assistant", "content": reply.content or (None if calls else ""), **({"tool_calls": calls} if calls else {})})
        if not calls:
            # A reply cut off at the output limit has no usable tool call: that is a failed run, not a finished one.
            result.stop = "truncated" if result.finish_reasons[-1] == "length" else "finished"
            result.final = reply.content or ""
            break
        for call in calls:
            name, raw = call["function"]["name"], call["function"]["arguments"]
            if name not in WORKER_TOOLS:
                out = f"Error: tool {name!r} is not available to workers."
            else:
                out = (await dispatch(name, raw))[:RESULT_CHARS]
                if name == "run_toolkit" and '"validate"' in raw:
                    result.self_validations += 1
            history.append({"role": "tool", "tool_call_id": call["id"], "content": out})
        verdict = await validate(task["output"])               # independent check, not billed, not a model step
        if verdict is not None and verdict != last_seen:
            result.verdicts.append(verdict)
            last_seen = verdict
    else:
        result.stop = "step_cap"
    final = await validate(task["output"])
    if final is not None and (not result.verdicts or result.verdicts[-1] != final):
        result.verdicts.append(final)
    return result


def plan_text(model: str, args, rate: float) -> str:
    lines = [f"Model: {model}", f"Steps cap per task: {args.max_steps}; max output tokens per call: {MAX_TOKENS}; tool results cut to {RESULT_CHARS} chars",
             f"Hard total cap: {args.cap_eur:.2f} EUR at {rate} EUR/USD = ${args.cap_eur / rate:.4f}; checked before every call against a pessimistic bound",
             f"System prompt ({len(LEAN_SYSTEM)} chars):", LEAN_SYSTEM, ""]
    for t in TASKS:
        lines += [f"--- task {t['id']} -> {t['output']}", t["task"], ""]
    return "\n".join(lines)


def cmd_capability(args) -> int:
    model = args.model
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if args.live and not key:
        raise SystemExit("--live needs ANTHROPIC_API_KEY in the environment (it is never read from the app's settings or a file).")
    try:
        row_local = page_row(model)
    except KeyError:
        row_local = None
    if args.live:
        model_catalog, settings, usage = backend()
        rate = settings.eur_per_usd()
        pricing = model_catalog.profile(model)["pricing"]
        if not pricing or pricing.get("input") is None:
            raise SystemExit(f"{model} has no known price in the app's catalogue, so a cap cannot be enforced. Refusing.")
        row = pricing
    else:
        rate = float(os.environ.get("LDRAW_NOVA_EUR_PER_USD", "") or 0.92)
        if row_local is None:
            print(f"Note: {model} has no price in this script's page table; the dry run cannot estimate its cost and --live would use the app's catalogue.")
        row = row_local
    print(plan_text(model, args, rate))
    if row:
        base = estimate_tokens([{"role": "system", "content": LEAN_SYSTEM}], [TOOLS_PLACEHOLDER_SCHEMA] * len(WORKER_TOOLS)) + 400
        per_run = worst_case_run(row, args.max_steps, base)
        total = per_run * len(TASKS)
        print(f"Worst case if every call used its full allowance and nothing cached: ${per_run:.3f} per task, ${total:.3f} for {len(TASKS)} tasks "
              f"= EUR {total * rate:.3f}. The hard cap of EUR {args.cap_eur:.2f} {'will stop the runs first' if total * rate > args.cap_eur else 'is above that bound'}.")
        expected = 0.03 * len(TASKS)
        print(f"Expected (README section 8 assumption: ${0.029:.3f} per worker): about ${expected:.2f} = EUR {expected * rate:.2f}. This is an assumption, not a measurement.")
    if not args.live:
        print("\nDRY RUN: nothing was sent. Add --live (and set ANTHROPIC_API_KEY) to run it.")
        return 0
    return asyncio.run(live(args, model, key, row, rate, usage))


TOOLS_PLACEHOLDER_SCHEMA = {"type": "function", "function": {"name": "tool", "description": "x" * 450, "parameters": {"type": "object", "properties": {"a": {"type": "string"}, "b": {"type": "string"}}, "required": []}}}


def parse_validate(text: str) -> bool | None:
    """checks_passed of the toolkit's validate JSON, False when it did not run or has an error diagnostic."""
    body = text.split("--- stdout ---", 1)[-1]
    start, end = body.find("{"), body.rfind("}")
    if start < 0 or end < start:
        return False
    try:
        data = json.loads(body[start:end + 1])
    except ValueError:
        return False
    errors = [d for d in data.get("diagnostics", []) if d.get("severity") == "error"]
    return bool(data.get("checks_passed")) and not errors


async def live(args, model: str, key: str, row: dict, rate: float, usage) -> int:
    import tempfile
    import litellm
    import system_prompt
    import tools
    from store import ChatStore
    budget = Budget(args.cap_eur, rate)
    marker = system_prompt.cache_control(model)
    schemas = [tools.TOOLS[n][0] for n in WORKER_TOOLS]
    root = Path(tempfile.mkdtemp(prefix="measure-live-"))
    root.chmod(0o755)                                  # the sandbox runs tools as the unprivileged agent user
    (root / "chats").mkdir(mode=0o755)
    (root / "output").mkdir(mode=0o755)
    store = ChatStore(root / "chats", root / "output")
    results = []
    only = {t.strip() for t in (args.only or "").split(",") if t.strip()}
    for task in [t for t in TASKS if not only or t["id"] in only]:
        chat_id = f"measure-{task['id']}-{int(time.time())}"
        (root / "chats" / chat_id).mkdir(parents=True, exist_ok=True)
        ctx = tools.ToolContext(chat_id=chat_id, store=store, emit=lambda *_: None, options={})

        async def complete(messages, tool_schemas):
            return await litellm.acompletion(model=model, messages=messages, tools=tool_schemas, max_tokens=MAX_TOKENS,
                                             api_key=key, num_retries=1, timeout=180)

        async def dispatch(name, raw, ctx=ctx):
            return (await tools.dispatch(ctx, name, raw)).content

        async def validate(path, ctx=ctx):
            target = ctx.work_dir / Path(path).relative_to("output")
            if not target.is_file():
                return None
            res = await tools.dispatch(ctx, "run_toolkit", {"arguments": ["validate", path, "--geometry", "--detail", "summary"]})
            return parse_validate(res.content)

        cost_fn = lambda rec: usage.step_cost_usd(rec, row, model, "1h")  # noqa: E731
        print(f"== {task['id']} (spent so far EUR {budget.spent_eur:.4f})", flush=True)
        result = await run_task(task, complete=complete, dispatch=dispatch, validate=validate, cost_fn=cost_fn, row=row, budget=budget,
                                schemas=schemas, max_steps=args.max_steps, marker=marker)
        summary = result.summary(rate)
        results.append(summary)
        print(json.dumps(summary), flush=True)
        if result.stop == "cap_eur":
            print(f"STOPPED: {budget.refused}")
            break
    total = {"model": model, "date": time.strftime("%Y-%m-%d"), "cap_eur": args.cap_eur, "spent_eur": round(budget.spent_eur, 6), "runs": results}
    out = Path(args.out)
    out.write_text(json.dumps(total, indent=1))
    print(f"\nTotal spent EUR {budget.spent_eur:.4f} of cap {args.cap_eur:.2f}. Results written to {out}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("prices")
    b = sub.add_parser("baseline")
    b.add_argument("--chats-dir", default=str(DEFAULT_CHATS))
    b.add_argument("--json")
    b.add_argument("--ttl", default="1h", choices=("5m", "1h"), help="cache write price: 1h = 2x (the app default), 5m = 1.25x")
    h = sub.add_parser("history")
    h.add_argument("--rev", default="b24c492c")
    c = sub.add_parser("capability")
    c.add_argument("--live", action="store_true", help="really call the provider (needs ANTHROPIC_API_KEY, costs money)")
    c.add_argument("--model", default="anthropic/claude-haiku-5-5")
    c.add_argument("--cap-eur", type=float, default=1.00, help="hard total cap in EUR at settings.eur_per_usd()")
    c.add_argument("--max-steps", type=int, default=20)
    c.add_argument("--only", default="", help="comma-separated task ids to run (default: all five)")
    c.add_argument("--out", default="capability-results.json")
    args = ap.parse_args(argv)
    return {"prices": cmd_prices, "baseline": cmd_baseline, "history": cmd_history, "capability": cmd_capability}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
