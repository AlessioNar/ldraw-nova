#!/usr/bin/env python
"""Brief 09: end-to-end cost and quality check of planner plus workers against single-model runs.

Owner scope: at most 3 EUR in total. The default is ONE run: Sonnet 5.5 planner + Haiku 5.5 workers on the small
spaceship prompt, budget_eur 2.5. The single-Sonnet baseline was measured already (see E2E.md), so no baseline is
re-run. The other configurations and prompts exist only behind explicit --configs / --prompts and NEED THE OWNER'S
APPROVAL FOR THE EXTRA SPEND (and a higher --total-cap-eur).

Dry run by default: prints the plan and the worst-case cost, imports nothing from the app, calls nothing.
`--live` runs the app's own agent loop (agent.start_turn) in a scratch data and config dir, so it must run inside a
throwaway container of the app image. It needs ANTHROPIC_API_KEY in the environment (used only to create the scratch
LLM entries, never printed or written). Hard caps: per run (`--budget-eur`, enforced by the app every step), in total
(`--total-cap-eur`, checked before each run against that run's worst case) and per run in wall-clock time (`--timeout`).

Examples (Git Bash, from the repo root; MSYS_NO_PATHCONV=1 stops path rewriting):
  python docs/plans/swarm-subagents/e2e.py                                   # dry run of the default single run
  python docs/plans/swarm-subagents/e2e.py --configs opus,sonnet,sonnet+haiku,opus+haiku --prompts small,medium,hard \
      --total-cap-eur 48                                                     # dry run of the full matrix (needs approval)
  docker run --rm -e ANTHROPIC_API_KEY -v "$PWD/app/web/backend:/app/web/backend:ro" \
      -v "$PWD/docs/plans/swarm-subagents:/m:ro" -v "$PWD/e2e-out:/out" -w /app/web/backend ldraw-nova-app \
      python3 /m/e2e.py --live --out /out
  python docs/plans/swarm-subagents/e2e.py report --results e2e-out/results.jsonl   # markdown table for E2E.md
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

OPUS, SONNET, HAIKU = "anthropic/claude-opus-5-5", "anthropic/claude-sonnet-5-5", "anthropic/claude-haiku-5-5"

# name -> (planner model, worker model or None, README section 8 estimate in EUR for a finished small model)
CONFIGS = {
    "opus": (OPUS, None, 9.9),
    "sonnet": (SONNET, None, 5.0),
    "sonnet+haiku": (SONNET, HAIKU, 0.94),
    "opus+haiku": (OPUS, HAIKU, 1.65),
}
PROMPTS = {
    "small": "Build a small spaceship: a white hull with blue accents, two swept-back wings, a clear cockpit canopy at "
             "the front and twin engines at the back.",
    "medium": "Build a small harbour scene on a 32 x 32 stud baseplate with three independent parts that only meet at "
              "the baseplate: a lighthouse (round tapered tower with a lamp room and gallery), a two-storey "
              "warehouse (door, four windows, sloped roof) and a wooden pier with two bollards and a small rowing "
              "boat. About 150 to 300 parts in total, in realistic colours.",
    "hard": "Build a sleek coupe sports car, 6 studs wide and about 20 long, whose roof, windscreen, bonnet and wings "
            "form one continuous curved body shell (no separate boxes), with four wheels, headlights, tail lights and "
            "a visible interior with two seats.",
}
DEFAULT_CONFIGS, DEFAULT_PROMPTS = "sonnet+haiku", "small"
DEFAULT_BUDGET_EUR, DEFAULT_TOTAL_CAP_EUR, DEFAULT_TIMEOUT_S = 2.5, 3.0, 3600


def plan_runs(configs: list[str], prompts: list[str], repeat: int) -> list[dict]:
    """The ordered run list: prompt-major so a partial matrix still compares configs on the same prompt."""
    for name in configs:
        if name not in CONFIGS:
            raise SystemExit(f"unknown config {name!r}; choose from {', '.join(CONFIGS)}")
    for name in prompts:
        if name not in PROMPTS:
            raise SystemExit(f"unknown prompt {name!r}; choose from {', '.join(PROMPTS)}")
    runs = []
    for prompt in prompts:
        for config in configs:
            for rep in range(1, repeat + 1):
                runs.append({"run_id": f"{prompt}-{config}-{rep}", "prompt": prompt, "config": config, "repeat": rep,
                             "planner": CONFIGS[config][0], "worker": CONFIGS[config][1]})
    return runs


def dry_run_text(runs: list[dict], budget_eur: float, total_cap_eur: float, timeout_s: int) -> str:
    lines = ["DRY RUN: nothing is called. Add --live (inside the app image) to run.",
             f"Per run: budget_eur {budget_eur:.2f}, wall-clock cap {timeout_s} s. Total cap {total_cap_eur:.2f} EUR.", "",
             "| run | planner | workers | worst case EUR | README estimate EUR (small) |", "|---|---|---|---|---|"]
    for run in runs:
        lines.append(f"| {run['run_id']} | {run['planner']} | {run['worker'] or 'none'} | {budget_eur:.2f} | {CONFIGS[run['config']][2]} |")
    worst = budget_eur * len(runs)
    allowed = min(len(runs), int((total_cap_eur + 1e-9) // budget_eur))
    lines += ["", f"Runs planned: {len(runs)}. Worst case if every run hits its budget: {worst:.2f} EUR.",
              f"The total cap of {total_cap_eur:.2f} EUR lets at most {allowed} of them start, so the real worst case is "
              f"{min(worst, allowed * budget_eur):.2f} EUR (a run is skipped when spent + {budget_eur:.2f} would pass the cap)."]
    if worst > total_cap_eur + 1e-9:
        lines.append("NOTE: more runs than the total cap pays for; the extra ones need the owner's approval for the extra spend "
                     "and a higher --total-cap-eur.")
    lines.append("Prompts:")
    lines += [f"- {name}: {text}" for name, text in PROMPTS.items() if any(r["prompt"] == name for r in runs)]
    return "\n".join(lines)


def redact(text: str, secrets: list[str]) -> str:
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[key]")
    return text


# --- timing and record extraction (pure functions over the chat's messages) ----------------------------------------
def timing_split(messages: list[dict]) -> dict:
    """Model seconds: an assistant step's created_at minus the previous message's. Tool seconds: the last of the
    tool results that follow the step minus the step's created_at. Messages are in file order."""
    model_s = tool_s = 0.0
    steps = 0
    for i, msg in enumerate(messages):
        if msg.get("role") != "assistant" or msg.get("_ui_only") or not isinstance(msg.get("_usage"), dict):
            continue
        steps += 1
        if i > 0:
            model_s += max(msg["created_at"] - messages[i - 1]["created_at"], 0.0)
        j = i + 1
        last = None
        while j < len(messages) and messages[j].get("role") == "tool":
            last = messages[j]["created_at"]
            j += 1
        if last is not None:
            tool_s += max(last - msg["created_at"], 0.0)
    return {"model_s": round(model_s, 1), "tool_s": round(tool_s, 1), "steps": steps}


def _json_content(msg: dict) -> dict:
    try:
        data = json.loads(msg.get("content") or "")
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def delegation_summary(messages: list[dict]) -> dict:
    """Delegations with their statuses, and repair rounds: a delegate that ended not ok and was followed by another
    delegate of the same role, plus a publish_model that failed validation and was followed by another publish."""
    calls = {}
    for msg in messages:
        for call in msg.get("tool_calls") or []:
            calls[call["id"]] = call["function"]
    delegations, publishes = [], []
    for msg in messages:
        if msg.get("role") != "tool":
            continue
        if msg.get("name") == "delegate":
            try:
                role = json.loads(calls.get(msg.get("tool_call_id"), {}).get("arguments") or "{}").get("role")
            except ValueError:
                role = None
            text = msg.get("content") or ""
            status = text.split("status ", 1)[1].split()[0].strip(".,:") if "status " in text else "refused"
            worker = msg.get("_worker") or {}
            delegations.append({"role": role, "status": status, "steps": worker.get("steps")})
        elif msg.get("name") == "publish_model":
            data = _json_content(msg)
            publishes.append(bool(data.get("checks_passed")) if data else None)
    repairs = 0
    for k, item in enumerate(delegations[:-1]):
        if item["status"] != "ok" and any(later["role"] == item["role"] for later in delegations[k + 1:]):
            repairs += 1
    repairs += sum(1 for ok in publishes[:-1] if ok is False)
    counts: dict[str, int] = {}
    for item in delegations:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    return {"delegations": len(delegations), "statuses": counts, "repair_rounds": repairs,
            "publishes": len(publishes), "validate_passed": publishes[-1] if publishes else None}


def build_record(run: dict, messages: list[dict], totals: dict, wall_s: float, models: list[dict], extra: dict) -> dict:
    planner_steps = [m for m in messages if m.get("role") == "assistant" and isinstance(m.get("_usage"), dict)]
    planner_out = sum(int(m["_usage"].get("completion") or 0) for m in planner_steps)
    parts = [m["_parts_check"] for m in messages if m.get("role") == "tool" and m.get("_parts_check")]
    notices = [m.get("content", "") for m in messages if m.get("_notice") or m.get("_error")]
    record = {**run, "wall_s": round(wall_s, 1), **timing_split(messages), "planner_steps": len(planner_steps),
              "output_tokens": totals["completion"], "planner_output_tokens": planner_out,
              "eur_total": totals["cost_eur"], "eur_workers": totals["worker_cost_eur"],
              "eur_planner": round(totals["cost_eur"] - totals["worker_cost_eur"], 4), "unpriced_steps": totals["unpriced_steps"],
              **delegation_summary(messages), "published": bool(models), "models": [m.get("name") for m in models],
              "parts_check": (parts[-1].get("complete") if parts else None), "notices": notices[-3:]}
    record.update(extra)
    return record


# --- caps -----------------------------------------------------------------------------------------------------------
def execute_plan(runs: list[dict], runner, budget_eur: float, total_cap_eur: float, write=lambda record: None) -> list[dict]:
    """Run in order. Before each run: skip it if spent + its worst case (budget_eur) would pass the total cap.
    A run whose cost is unknown (it crashed before any record) is charged its worst case."""
    spent, out = 0.0, []
    for run in runs:
        if spent + budget_eur > total_cap_eur + 1e-9:
            record = {**run, "status": "skipped_total_cap", "eur_total": 0.0,
                      "note": f"spent {spent:.2f} + worst case {budget_eur:.2f} would pass the total cap {total_cap_eur:.2f}"}
        else:
            try:
                record = runner(run)
            except Exception as exc:  # noqa: BLE001 - one broken run must not stop the matrix or lose the spend
                record = {**run, "status": "error", "eur_total": None, "note": f"{type(exc).__name__}: {exc}"[:300]}
            cost = record.get("eur_total")
            spent += budget_eur if cost is None else max(cost, 0.0)
        out.append(record)
        write(record)
    return out


# --- the live runner (app code; only inside the app image) ----------------------------------------------------------
def setup_env() -> Path:
    """Scratch data, config and cache dirs, set BEFORE any app module is imported (as tests/conftest.py does)."""
    base = Path(tempfile.mkdtemp(prefix="ldraw-nova-e2e-"))
    os.environ["LDRAW_NOVA_DATA_DIR"] = str(base / "data")
    os.environ["LDRAW_NOVA_WEB_CONFIG_DIR"] = str(base / "config")
    os.environ["LDRAW_NOVA_GLB_CACHE_DIR"] = str(base / "glb-cache")
    (base / "data" / "output").mkdir(parents=True)
    os.chmod(base, 0o755)                 # the agent sandbox user must be able to traverse down to the data dir
    for path in ("/app/web/backend", "/app"):
        if os.path.isdir(path) and path not in sys.path:
            sys.path.insert(0, path)
    return base


def copy_artifacts(store, chat_id: str, models: list[dict], dest: Path) -> list[str]:
    dest.mkdir(parents=True, exist_ok=True)
    copied = []
    work = store.work_dir(chat_id)
    candidates = sorted(work.rglob("*.png")) if work.is_dir() else []
    candidates += [store.resolve(chat_id, m["model"]) for m in models]
    candidates += sorted(work.glob("*.mpd")) if work.is_dir() else []
    for n, src in enumerate(candidates):
        if src.is_file() and src.stat().st_size < 64 * 1024 * 1024:
            name = f"{n:03d}-{src.parent.name}-{src.name}" if src.suffix == ".png" else src.name
            shutil.copyfile(src, dest / name)
            copied.append(name)
    return copied


async def live_run(run: dict, ctx: dict) -> dict:
    import agent
    import run_state
    import usage
    store, out, args = ctx["store"], ctx["out"], ctx["args"]
    options = {"permissions": "full", "budget_eur": args.budget_eur,
               "worker_model_id": ctx["entries"][run["worker"]] if run["worker"] else None}
    chat = store.create_chat(title=run["run_id"])
    started = time.time()
    status, note = "ok", ""
    try:
        await agent.start_turn(store, chat["id"], PROMPTS[run["prompt"]], ctx["entries"][run["planner"]], options)
        task = run_state._runs[chat["id"]].task
        await asyncio.wait_for(asyncio.shield(task), args.timeout)
    except asyncio.TimeoutError:
        status, note = "timeout", f"wall-clock cap {args.timeout} s; the run was cancelled"
        await run_state.cancel(chat["id"])
    except Exception as exc:  # noqa: BLE001
        status, note = "error", redact(f"{type(exc).__name__}: {exc}", ctx["secrets"])[:300]
        await run_state.cancel(chat["id"])
    wall = time.time() - started
    messages, models = store.messages(chat["id"]), store.models(chat["id"])
    totals = usage.usage_totals(messages)
    files = copy_artifacts(store, chat["id"], models, out / run["run_id"])
    (out / run["run_id"] / "messages.jsonl").write_text(
        redact("\n".join(json.dumps(m, ensure_ascii=False) for m in messages), ctx["secrets"]), encoding="utf-8")
    notices = [str(m.get("content")) for m in messages if m.get("_notice") or m.get("_error")]
    hit = totals["cost_eur"] >= 0.98 * args.budget_eur or any("budget" in n.lower() for n in notices)
    record = build_record(run, messages, totals, wall, models, {"status": status, "note": note, "files": files,
                                                                 "budget_stop": bool(hit), "chat_id": chat["id"]})
    record["notices"] = [redact(str(n), ctx["secrets"]) for n in record["notices"]]
    return record


def cmd_live(args, runs: list[dict]) -> int:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise SystemExit("--live needs ANTHROPIC_API_KEY in the environment")
    setup_env()
    try:
        import llm_config
        import settings
        from store import ChatStore
    except ImportError as exc:
        raise SystemExit(f"cannot import the app modules ({exc}); run inside a throwaway container of the app image") from None
    entries = {model: llm_config.create({"model_name": model, "litellm_params": {"model": model, "api_key": key}})["id"]
               for model in sorted({m for r in runs for m in (r["planner"], r["worker"]) if m})}
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    store = ChatStore(settings.CHATS_DIR, settings.OUTPUT_DIR)
    ctx = {"store": store, "out": out, "args": args, "entries": entries, "secrets": [key]}

    def write(record: dict) -> None:
        with (out / "results.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        print(f"{record['run_id']}: {record.get('status')} EUR {record.get('eur_total')} {record.get('note', '')}", flush=True)

    results = execute_plan(runs, lambda run: asyncio.run(live_run(run, ctx)), args.budget_eur, args.total_cap_eur, write)
    spent = sum(r.get("eur_total") or 0 for r in results)
    print(f"Done. Spent EUR {spent:.4f} of total cap {args.total_cap_eur:.2f}. Results in {out}/results.jsonl")
    return 0


# --- the markdown report ---------------------------------------------------------------------------------------------
def report_text(records: list[dict]) -> str:
    def fmt(v, spec=""):
        return "n/a" if v is None else format(v, spec)
    lines = ["| prompt | config | status | EUR total | planner | workers | steps | out tokens | wall s | model s | tool s | delegations | repairs | published | validate | parts check |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in records:
        lines.append("| " + " | ".join([
            r["prompt"], r["config"], str(r.get("status")), fmt(r.get("eur_total"), ".3f"), fmt(r.get("eur_planner"), ".3f"),
            fmt(r.get("eur_workers"), ".3f"), fmt(r.get("steps")), fmt(r.get("output_tokens")), fmt(r.get("wall_s")),
            fmt(r.get("model_s")), fmt(r.get("tool_s")), json.dumps(r.get("statuses") or {}) if r.get("delegations") else "0",
            fmt(r.get("repair_rounds")), fmt(r.get("published")), fmt(r.get("validate_passed")), fmt(r.get("parts_check"))]) + " |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd")
    rep = sub.add_parser("report", help="markdown table from results.jsonl")
    rep.add_argument("--results", required=True)
    parser.add_argument("--configs", default=DEFAULT_CONFIGS,
                        help=f"comma list of {', '.join(CONFIGS)}; anything beyond the default needs the owner's approval for the extra spend")
    parser.add_argument("--prompts", default=DEFAULT_PROMPTS,
                        help=f"comma list of {', '.join(PROMPTS)}; anything beyond the default needs the owner's approval for the extra spend")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--budget-eur", type=float, default=DEFAULT_BUDGET_EUR, help="per-run budget_eur, enforced by the app every step")
    parser.add_argument("--total-cap-eur", type=float, default=DEFAULT_TOTAL_CAP_EUR, help="hard cap across all runs")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_S, help="wall-clock cap per run, seconds")
    parser.add_argument("--out", default="e2e-out")
    parser.add_argument("--live", action="store_true", help="call the provider (spends money); default is a dry run")
    args = parser.parse_args(argv)
    if args.cmd == "report":
        records = [json.loads(line) for line in Path(args.results).read_text(encoding="utf-8").splitlines() if line.strip()]
        print(report_text(records))
        return 0
    if args.repeat < 1 or args.budget_eur <= 0 or args.total_cap_eur <= 0:
        raise SystemExit("--repeat, --budget-eur and --total-cap-eur must be positive")
    runs = plan_runs([c for c in args.configs.split(",") if c], [p for p in args.prompts.split(",") if p], args.repeat)
    print(dry_run_text(runs, args.budget_eur, args.total_cap_eur, args.timeout))
    return cmd_live(args, runs) if args.live else 0


if __name__ == "__main__":
    sys.exit(main())
