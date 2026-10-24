"""Offline measurement of the NL pipeline (no network, no LLM, no key). Run inside a throwaway container of the app image:

  docker run --rm --name obd-nl-review --entrypoint bash -v <worktree>/app:/app:ro -v <worktree>/ldraw_tools:/opt/ldraw-nova/ldraw_tools:ro \
     -v <worktree>/docs/plans/nl-review:/out ldraw-nova-app:latest -c "cd /app/web/backend && python3 /out/measure_nl.py"

Writes /out/results-rules.json and /out/results-golden.json.
"""
import asyncio, json, os, subprocess, sys, tempfile, time
sys.path.insert(0, "/app/web/backend")
os.environ.setdefault("LDRAW_NOVA_CONFIG_DIR", tempfile.mkdtemp())
import generator_bridge, fast_path, understand, understand_eval  # noqa: E402

DUMP = ("import json\nfrom ldraw_tools import generators as g\nfrom ldraw_tools.generators.colours import NAMES\n"
        "print(json.dumps({'digest': g.digest(), 'entries': [x.entry() for x in g.REGISTRY.values()], 'colours': sorted(NAMES)}))\n")
t = time.monotonic()
out = subprocess.run(["/opt/ldraw-nova/.venv/bin/python", "-c", DUMP], cwd="/opt/ldraw-nova", capture_output=True, text=True, check=True).stdout
registry = json.loads(out)
print(f"registry dump {time.monotonic()-t:.2f}s, {len(registry['entries'])} generators, digest {len(registry['digest'])} chars")
generator_bridge.set_registry(registry)
ENTRIES = {e["id"]: e for e in registry["entries"]}

async def no_model(llm_id=None):          # the understanding model is "not configured": exactly what a missing key does
    return None
understand.model_params = no_model

CTX = {"new": None, "house": "house", "car": "grand-tourer", "nongen": None}
def current(ctx):
    g = CTX[ctx]
    return {"generator": g, "params": dict(ENTRIES[g]["defaults"])} if g else None

prompts = json.load(open("/out/prompts.json"))["prompts"]
rows = []
for p in prompts:
    cur = current(p["ctx"])
    # 1. the composer hint exactly as the UI calls it: GET /api/describe/estimate?text=..&generator=<id or none>
    hint_cur = {"generator": cur["generator"], "params": {}} if cur else None
    t = time.monotonic(); hint = fast_path.rules_guess(p["text"], hint_cur); hint_ms = (time.monotonic() - t) * 1000
    # 1b. same hint WITHOUT the generator (what a non-generator build or the NewBuild page sends)
    hint_nogen = fast_path.rules_guess(p["text"], None)
    # 2. the submit path with no model: rules shortcut, else heuristic keyword reading ("rules_fallback")
    t = time.monotonic(); d = asyncio.run(fast_path.describe(p["text"], current=cur)); desc_ms = (time.monotonic() - t) * 1000
    shortcut = understand.rules_shortcut(p["text"], fast_path._current_spec(cur))
    rows.append({"id": p["id"], "cat": p["cat"], "ctx": p["ctx"], "text": p["text"],
                 "would_call_llm": shortcut is None,
                 "hint_route": hint["route"], "hint_conf": hint["confidence"], "hint_generator": hint.get("generator"), "hint_text": hint.get("hint"),
                 "hint_route_without_generator": hint_nogen["route"], "hint_ms": round(hint_ms, 2),
                 "describe_route": d["route"], "understood_by": d.get("understood_by"), "generator": d.get("generator"),
                 "params": d.get("params"), "changes": d.get("changes"), "notes": d.get("notes"), "reason": d.get("reason"),
                 "question": d.get("question"), "describe_ms": round(desc_ms, 2)})
json.dump(rows, open("/out/results-rules.json", "w"), indent=1)
for r in rows:
    print(f"{r['id']} [{r['ctx']:6}] {r['text'][:42]:42} hint={r['hint_route']:9}({r['hint_conf']}) nogen={r['hint_route_without_generator']:9} fallback={r['describe_route']:9} {r['generator'] or ''} {r['notes'] or ''}")

# golden set through the zero-cost paths
async def golden():
    res = {}
    for label, use_rules in (("rules+heuristic", True), ("heuristic only", False)):
        rs = await understand_eval.run_golden(None, use_rules=use_rules)
        res[label] = {"rates": understand_eval.rates(rs), "rows": rs}
    return res
g = asyncio.run(golden())
gold = understand_eval.load_golden()
ids = {c["id"]: c for c in gold}
summary = {}
for label, v in g.items():
    rs = v["rows"]
    summary[label] = {"n": len(rs), "rates": v["rates"], "by_source": {s: sum(r["source"] == s for r in rs) for s in {r["source"] for r in rs}},
                      "full_match": sum(all(x is not False for x in r["match"].values()) for r in rs),
                      "fails": [{"id": r["id"], "prompt": r["prompt"], "src": r["source"], "bad": [k for k, x in r["match"].items() if x is False]} for r in rs if any(x is False for x in r["match"].values())]}
json.dump(summary, open("/out/results-golden.json", "w"), indent=1)
print(json.dumps({k: {"n": v["n"], "by_source": v["by_source"], "full_match": v["full_match"], "rates": {d: v["rates"][d] for d in ("intent", "family", "colours", "edits")}} for k, v in summary.items()}, indent=1))
# which golden prompts the rules shortcut answers (zero cost) vs need the LLM
sc = [c["id"] for c in gold if understand.rules_shortcut(c["prompt"], c.get("current_spec"), c.get("previous_text"))]
print("golden answered by rules_shortcut:", len(sc), "of", len(gold), sc)

# colour vocabulary mismatch: understanding COLOURS vs generator colour names
unk = [c for c in understand.COLOURS if fast_path._norm_colour(c) is None]
print("understand.COLOURS names the generators reject:", unk)
alias_unk = [(k, v) for k, v in understand.COLOUR_ALIASES.items() if fast_path._norm_colour(v) is None]
print("aliases mapping to rejected names:", alias_unk)
json.dump({"colours_rejected_by_generators": unk, "aliases_rejected": alias_unk}, open("/out/results-colours.json", "w"))
