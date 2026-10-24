"""Golden-set scoring for the understanding step, and the one-off live check against a real provider.

Run the live check ONCE, on purpose (it makes one model call per golden prompt, about 70 calls, a cent or two):

    docker compose exec ldraw-nova-app bash -c "cd /app/web/backend && python3 understand_eval.py --live"

It uses the model chosen by `understand.model_params()` (Settings understanding model, else LDRAW_NOVA_UNDERSTAND_LLM_ID,
else a configured anthropic/claude-haiku-5-5 entry), or `--llm-id <id>` for a specific configured entry (a Mistral
Small 4 entry works too). Without `--live` nothing is called. Prints the family / colour / edit match rates, median
cost and latency, and every mismatch; `--json FILE` also saves the rows.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
from pathlib import Path
from typing import Optional

import understand

GOLDEN_PATH = Path(__file__).with_name("understand_golden.json")


def load_golden() -> list[dict]:
    return json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))["prompts"]


def _edit_key(edit: dict) -> tuple:
    value = edit.get("value")
    return (edit.get("op"), str(edit.get("target", "")).lower(), str(value).lower() if value is not None else None,
            str(edit.get("delta", "")).lower() or None)


def match(expected: dict, actual: Optional[dict]) -> dict:
    """What matches, per dimension: intent, family (new models), colours (expected roles), edits (op, target, value, delta).
    A dimension the expected spec does not exercise is None."""
    actual = actual or {}
    out: dict = {"intent": actual.get("intent") == expected["intent"], "family": None, "colours": None, "edits": None}
    if expected["intent"] == "new_model":
        out["family"] = (actual.get("subject") or {}).get("family") == expected["subject"]["family"]
    if expected.get("colours"):
        have = {k: str(v).lower() for k, v in (actual.get("colours") or {}).items()}
        out["colours"] = all(have.get(role) == str(name).lower() for role, name in expected["colours"].items())
    if expected.get("edits"):
        out["edits"] = (sorted(map(str, map(_edit_key, expected["edits"])))
                        == sorted(map(str, map(_edit_key, actual.get("edits") or []))))
    return out


def rates(rows: list[dict]) -> dict:
    """Fraction matched per dimension over the rows that exercise it, plus cost and latency medians over the model calls."""
    out = {}
    for dim in ("intent", "family", "colours", "edits"):
        scored = [r["match"][dim] for r in rows if r["match"][dim] is not None]
        out[dim] = {"matched": sum(scored), "of": len(scored), "rate": round(sum(scored) / len(scored), 3) if scored else None}
    called = [r for r in rows if r["attempts"]]
    costs = [r["cost_usd"] for r in called if r["cost_usd"] is not None]
    out["calls"] = len(called)
    out["median_cost_usd"] = round(statistics.median(costs), 6) if costs else None
    out["median_latency_s"] = round(statistics.median(r["latency_s"] for r in called), 3) if called else None
    out["fallbacks"] = sum(r["source"] == "rules_fallback" for r in rows)
    return out


async def run_golden(params: Optional[dict], *, use_rules: bool = False, limit: Optional[int] = None,
                     digest: Optional[understand.Digest] = None) -> list[dict]:
    rows = []
    for case in load_golden()[:limit]:
        result = await understand.understand(case["prompt"], current_spec=case.get("current_spec"),
                                             previous_text=case.get("previous_text"), params=params,
                                             use_rules=use_rules, digest=digest)
        rows.append({"id": case["id"], "prompt": case["prompt"], "source": result.source, "attempts": result.attempts,
                     "valid": not understand.validate(result.spec), "match": match(case["expected"], result.spec),
                     "cost_usd": (result.usage or {}).get("cost_usd"), "latency_s": result.latency_s,
                     "errors": result.errors, "spec": result.spec})
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--live", action="store_true", help="really call the provider (required)")
    parser.add_argument("--llm-id", help="id of a configured LLM entry (default: understand.model_params())")
    parser.add_argument("--limit", type=int, help="only the first N golden prompts")
    parser.add_argument("--rules", action="store_true", help="let the free rules answer what they can (default: every prompt goes to the model)")
    parser.add_argument("--json", help="write the per-prompt rows to this file")
    args = parser.parse_args()
    if not args.live:
        parser.error("refusing to call a provider without --live")

    async def go():
        params = await understand.model_params(args.llm_id)
        if not params:
            raise SystemExit("No model configured: add a Haiku 5.5 / Mistral Small 4 entry in Settings or pass --llm-id.")
        print(f"model: {params['model']}")
        return await run_golden(params, use_rules=args.rules, limit=args.limit)

    rows = asyncio.run(go())
    summary = rates(rows)
    for r in rows:
        bad = [k for k, v in r["match"].items() if v is False]
        if bad or not r["valid"] or r["source"] == "rules_fallback":
            print(f"MISMATCH {r['id']}: {bad or 'invalid'} source={r['source']} {r['prompt']!r}")
    print(json.dumps(summary, indent=1))
    ok = [summary[d]["rate"] for d in ("family", "colours", "edits") if summary[d]["rate"] is not None]
    print(f"target: family, colours and edits >= 0.90 (min seen {min(ok):.2f}); median cost < $0.0005 (seen {summary['median_cost_usd']})")
    if args.json:
        Path(args.json).write_text(json.dumps({"summary": summary, "rows": rows}, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
