# Swarm subagents: plan set

Goal: the app's agent (the planner) can delegate bounded pieces of a build to cheap worker agents, under one euro budget, so a medium model costs at most about 2 EUR instead of about 10 EUR.

Status: design only. A flag-gated prototype of a `delegate` tool (default off) is being written in `app/web/backend/subagents.py` by another agent. That file does not exist on this branch yet. The briefs build on it. Where this document and the prototype disagree on a detail, the prototype wins and the brief is adjusted, but the budget, isolation and failure rules below are requirements.

## What exists today (and constrains the design)

- One loop: `run_turn` in `app/web/backend/turn.py`. One model per chat (`entry`), one message list, one stream per step. It reads the chat's `budget_eur` and compares it with `usage.usage_totals(stored)["cost_eur"]`, which sums the `_usage.cost_usd` of messages saved in that chat.
- Cost per step: `usage.step_cost_usd`, from `model_catalog.entry_profile(entry)["pricing"]`. Anthropic caching is billed 0.1x for a read and 1.25x for a write (2x with `CACHE_TTL=1h`, the current default in `settings.py`). Other providers: cached tokens priced in full (a safe overestimate).
- Tools: `TOOLS` / `TOOL_SCHEMAS` in `tools.py`. Gating: `tool_policy.available_tools` (mode `plan` or permissions `read_only` expose only `READ_TOOLS`). Approvals: `agent.execute_tool` asks the user for every non-read tool when `permissions == "ask"`, one pending approval per call, 600 s timeout.
- Files: `ToolContext.work_dir` is `/data/output/<chat>/`. `resolve_path` confines tool paths to it. `run_python` and `run_shell` run in the sandbox with that cwd and are not confined at the file level.
- Prompt caching shapes everything (`docs/plans/loop-perf/FIX-cache.md`): the system prompt is a stable cached prefix, a rolling breakpoint sits on the last history message, and the changing work listing goes in a trailer after it. A worker is a separate request stream with its own prefix. The planner's prefix must not change when workers run: worker output reaches the planner only through the delegate tool result, appended at the end of history, which keeps the planner's cache valid.

## 1. When the planner should split

Split only when the pieces are independent and the planner can state each as a contract (section 2). Good splits:

- Sub-assemblies with a fixed interface: chassis, body shell, interior, wheels, accessories. Each worker builds one `.ldr` submodel at its own origin; the planner places submodels in the final plan. The interface is written in the task ("axle holes at x=+-80, y=24, z=0 relative to the origin; width 160 LDU").
- Parts research: "find owned parts that can make a 2x4 wedge roof, return 5 candidates with ids, colours, counts". Read-only (`my_parts`, `discover search`), no file writes except a notes file. This is the safest first use.
- Validation and render review: run `validate`, render from named angles, return a short list of defects. Read tools plus `run_toolkit` only.

Do not split: models under about 40 parts, anything where pieces share geometry that is still undecided, final integration, and `publish_model`. The planner keeps integration, final validation and publishing. The planner prompt carries this rule, a cap on delegations per turn (default 8) and on concurrent workers (section 5).

## 2. Worker task contract

The `delegate` tool takes one object; the planner cannot give a worker more than this:

| field | meaning |
|---|---|
| `role` | `builder`, `researcher` or `reviewer`; picks default tools, prompt and step cap |
| `task` | what to produce, plain text, at most 1500 characters, including interface dimensions |
| `inputs` | paths under `output/` the worker may read (plans, notes, other workers' results). It also gets the read tools over the toolkit docs |
| `tools` | subset of an allowlist: `read_file`, `list_files`, `my_parts`, `view_image`, `write_file`, `run_toolkit`, `run_python`. Default per role. Never `publish_model`, `delegate`, `run_shell` |
| `outputs` | file names the worker must create, all inside `output/workers/<worker_id>/` |
| `max_steps` | model calls; default 20, hard ceiling 40 |
| `max_eur` | budget for this worker; default 0.10, clamped to the remaining shared budget |
| `summary_chars` | the final reply is cut to this length; default 600, ceiling 1500 |

The worker's system prompt is fixed and short (a lean variant of `app/web/backend/prompts/system.md`; see the `LEAN_PROMPT` flag in `settings.py`): the contract, the plan format, and the rule "finish by writing the outputs, then reply with at most N characters: what you made, interface values you used, known defects". It does not receive the planner's history. A worker's context therefore stays at 20-40k tokens while the planner's grows.

The delegate tool result is: the contract echo, a status (`ok`, `partial`, `failed`, `over_budget`, `step_cap`, `stuck`), the capped summary, the output files with sizes, the `validate` verdict the app ran itself (section 6) and the worker's cost. Nothing else.

## 3. Model tiers and configuration

- Planner: the chat's model, as today (Sonnet 5.5 as the cost target; Opus 5.5 for hard models).
- Worker: a separate entry from `llm_config`, chosen by chat option `worker_model_id`, falling back to env `LDRAW_NOVA_WORKER_MODEL_ID`, falling back to none. With none set, the `delegate` tool is not offered, so nothing changes for existing chats. The Settings page already manages model entries; the worker is one more entry plus a picker next to the budget field in the chat box.
- Capability check at the start of the turn, reusing `model_catalog.builder_supported` and `llm_config.capabilities`: tool calling required; vision required for roles that call `view_image`. Fail with a visible notice, never a silent downgrade.
- Pricing comes from `model_catalog.entry_profile(worker_entry)["pricing"]` and the same `step_cost_usd`. **LiteLLM's catalogue puts Haiku 5.5 at about $0.1 / $0.5 per 1M tokens (the owner never quoted this figure; it is unverified). The bundled catalogue (`app/web/backend/model_catalog_data.json`, verified 2026-09-30) lists Claude Haiku 4.5 at $1 / $5, ten times higher, and no Haiku 5.5 entry. Task 01 settles the real number first, because the cost target depends on it (section 8).**
- Workers run at the lowest reasoning effort the model offers (`options.effort`); hidden reasoning was most of the output tokens in the measured red-car chat.

## 4. One shared budget

Rule: the chat's `budget_eur` covers the planner and all workers, including workers still running.

- Recording. A worker keeps its transcript in a side file, `chats/<chat>/workers/<worker_id>.jsonl`, not in `messages.jsonl`, so the planner's history and cache are untouched. When a worker ends, the delegate tool-result message carries one aggregate `_usage` record: summed `prompt/completion/cache_read/cache_write` and summed per-step `cost_usd`, plus `_worker: {id, model, steps, status}`. `usage.usage_totals` already sums every message's `_usage`, so chat totals and the euro display include workers with no change to the totals code. Per-step records stay in the side file. A worker cancelled or killed mid-run still gets its aggregate (written in a `finally`).
- Enforcement. A `BudgetLedger` on the `Run` holds `limit_eur`, `live_eur` (cost of worker steps not yet saved in a message) and a reservation per running worker. Before every worker step: `stored + live + reservations >= limit` stops the worker with `over_budget`. The top-of-step check in `turn.py` uses stored cost plus `ledger.live`. A worker's `max_eur` is reserved when it starts and released as it spends, so ten workers launched together cannot all pass the check and overshoot.
- Overshoot bound: one step of one model, as today (the existing check also runs only before a step).
- When the budget is hit mid-delegation: running workers stop at their next step boundary and return `over_budget` with the files they have; the planner's own check then posts the existing "Budget reached" notice.
- Display uses `settings.eur_per_usd()` like everything else. If a worker model's pricing is unknown (`step_cost_usd` returns `None`) and a budget is set, the worker is refused: an unpriced worker defeats the cap.

## 5. Parallelism and file safety

- Limits: `LDRAW_NOVA_MAX_WORKERS` (default 3 running at once, ceiling 6), one `asyncio.Semaphore` per run; extra `delegate` calls queue. A planner step may emit several `delegate` calls. `run_turn` currently awaits tool calls one by one, so task 06 runs delegate calls concurrently (`asyncio.gather`) and still saves results in call order.
- Provider rate limits: 429s retry through `num_retries=2` as today. The semaphore is the only throttle; lower the default if measurement shows 429s.
- Files: each worker writes only below `output/workers/<worker_id>/`. `ToolContext` gets an optional `write_root`; `resolve_path(write=True)` rejects anything outside it. Two workers never share a write root, so nothing needs locking. Reads: the `inputs` list plus toolkit docs. The planner merges by referencing worker `.ldr` submodels in its plan or by copying with `run_python`.
- Known gap: `run_python` can write anywhere under `output/`. Mitigation (task 05): workers never get `run_shell`, `run_python` runs with cwd set to the worker directory, and the app hashes `output/` at worker start and end, reports files changed outside the worker root and restores or deletes them. An OS-level jail is out of scope; the UI notice says so.
- Worker renders go under the worker directory and are not auto-attached to the planner's turn.

## 6. Approvals, permissions, failure handling

Permissions
- A worker never exceeds the chat's policy. Mode `plan` or permissions `read_only`: workers get only `READ_TOOLS`, and build-role delegation is refused.
- `delegate` is not in `READ_TOOLS`, so under `ask` the user approves each delegation once. The approval card shows the contract: role, task text, tools, step cap, max euros, worker model. Tools inside an approved contract run without further prompts; the allowlist and write root bound them. Under `full`, nothing is asked.
- Workers cannot ask the user. A tool outside the contract is denied with a message, not queued. This avoids ten parallel approval cards and the 600 s timeout stalling a swarm.
- Stop: the existing `CancelledError` path in `turn.py` must also cancel all workers and write their aggregate usage.

Failure handling (the planner receives the status and decides to retry once, do it itself, or give up)
- Stuck: step cap reached, the same tool call with identical arguments three times, or no output file written in the last 8 steps. Status `step_cap` or `stuck`; partial outputs are kept and listed.
- Over budget: `over_budget` (section 4).
- Bad output: after the worker ends, the app (not the worker) runs the toolkit `validate` and a parts check on each declared `.ldr` or plan output and returns the verdict. Nothing produced, or a file that does not parse: `failed`. Invalid LDraw from a weak model is expected, so the app does one automatic repair round (same worker, validator error text, at most 8 more steps) and then reports `partial` with the errors. The planner prompt allows fixing small defects itself or one redelegation per piece.
- The planner must not place a submodel whose verdict is not `ok`. Provider errors inside a worker end that worker as `failed` with the error class, never the turn.
- Only the planner calls `publish_model`.

## 7. UI

- A delegate tool call renders as one card (extend `app/web/frontend/src/components/ToolCard.tsx`): role, one-line task, worker model, live status, steps against cap, euros against `max_eur`. Expand for the worker transcript (read only, from the side file) and output files. Concurrent workers are several cards.
- `UsageBadge.tsx`: the chat total already includes workers; the tooltip adds "planner EUR x / workers EUR y" from the `_worker` markers.
- `TurnStatus.tsx`: phase text such as "3 workers running".
- Chat box: worker model picker beside the budget field; "No workers" is the default.
- The approval card for `delegate` shows the contract.
- A notice for a worker that is `failed`, `over_budget` or had stray writes.

## 8. Cost model

USD unless stated; EUR at the app's 0.92 EUR per USD (`settings.eur_per_usd`). Every number is an assumption unless marked measured.

Prices per 1M tokens: Opus 5.5 $4 in / $20 out (catalogue). Cache read 0.1x, write 1.25x (5 minute TTL; the app default is 1h at 2x, which raises writes). Sonnet 5.5 $2 / $10 (LiteLLM's catalogue; the bundled catalogue lists Sonnet 5 at the same price). Haiku-class worker, case A $0.1 / $0.5 (LiteLLM's catalogue, unverified), case B $1 / $5 (the catalogue's Haiku 4.5).

**Check against the measured chat.** 0.5M cache writes x $5.0 + 1.0M reads x $0.4 (assuming the other 1.0M input were all reads) + 62k output x $20 = 2.50 + 0.40 + 1.24 = $4.14, versus the measured $4.2. The price model reproduces that chat.

**Baseline, single Opus run, finished small model.** Owner figure about 10 EUR = $10.9. Assumed to be 2.6x the measured chat (about 62 steps): 4.0M input (1.3M writes, 2.7M reads), 160k output: 6.5 + 1.08 + 3.2 = $10.8, about 9.9 EUR. Consistent.

**Swarm assumptions.** The planner needs 15 steps at 45k average context (shorter than baseline because workers' histories never enter it): 150k writes, 525k reads, 30k output. Six workers (3 builders, 1 researcher, 2 reviewers), 20 steps each at 35k average context (15k of it fixed prompt): 700k input per worker with 100k written and 600k read, 20k output; plus 30% for retries and repair rounds. All models cache like Anthropic models.

| item | Sonnet planner | Opus planner |
|---|---|---|
| writes (150k) | $0.38 | $0.75 |
| reads (525k) | $0.11 | $0.21 |
| output (30k) | $0.30 | $0.60 |
| planner total | $0.79 | $1.56 |

| item | workers case A ($0.1/$0.5) | workers case B ($1/$5) |
|---|---|---|
| per worker writes (100k) | $0.0125 | $0.125 |
| per worker reads (600k) | $0.006 | $0.06 |
| per worker output (20k) | $0.010 | $0.10 |
| per worker | $0.029 | $0.285 |
| 6 workers, +30% | $0.23 | $2.2 |

| scenario | USD | EUR | vs baseline |
|---|---|---|---|
| single Opus 5.5 (baseline) | 10.8 | 9.9 | 1.0x |
| single Sonnet 5.5, same steps, no swarm | 5.4 | 5.0 | 0.5x |
| Sonnet planner + workers A | 1.02 | 0.94 | 0.09x |
| Opus planner + workers A | 1.79 | 1.65 | 0.17x |
| Sonnet planner + workers B | 3.0 | 2.8 | 0.28x |
| Opus planner + workers B | 3.8 | 3.5 | 0.35x |

Reading: with a Sonnet planner and case A workers the 2 EUR target is met with margin (0.94 EUR), and an Opus planner still fits (1.65 EUR). With case B worker prices the target is missed (2.8 and 3.5 EUR) unless the swarm shrinks to about 3 workers at 15 steps. Against simply switching to Sonnet, the swarm gains about 5x in case A and only about 1.8x in case B. The result is driven by (1) the planner's step count and output tokens, (2) the worker price, (3) how many repair rounds a weak model needs.

**Intended configuration (owner, 2026-10-23).** Workers run on Haiku 5.5; the planner is Opus 5.5 or Sonnet 5.5. That is the "Opus planner + workers A" row (about 1.65 EUR) and the "Sonnet planner + workers A" row (about 0.94 EUR), valid only if Haiku 5.5 really costs about $0.1 / $0.5 per 1M tokens. That figure is LiteLLM's and unverified, and the bundled catalogue has no Haiku 5.5 entry, so the app prices the worker through LiteLLM's fallback until one is added. At $1 / $5 the Opus planner row becomes about 3.5 EUR. Brief 01 checks the price and that the model is available on the account before anything else is built. Once the price is confirmed, add a Haiku 5.5 entry to `app/web/backend/model_catalog_data.json` with its source and date.

Not modelled: image tokens of renders (a reviewer viewing 6 renders can add 10-30k tokens per step), the 1h cache TTL, tool-result sizes, the planner redoing failed pieces, and any minimum cacheable prompt length on the worker model.

## 9. What is uncertain, and what to measure first

1. The real Haiku-class price and whether the model exists on the account (task 01). A 10x error flips the conclusion.
2. Whether a Haiku-class model produces valid LDraw sub-assemblies with the current tools and lean prompt: valid-first-try rate, repair rounds, parts-check pass rate. If under about 60 percent are valid after one repair, builders stay with the planner and only research and review are delegated (still cheap).
3. How much of a baseline run is splittable: from recorded chats, the share of steps that are research, single-sub-assembly building, and review. If under about 40 percent, the savings ceiling is low.
4. Planner overhead: contracts, interfaces and integration may push the planner from the assumed 15 steps to 30.
5. Worker cache behaviour: whether cache reads appear from step 2 on the cheap model, and whether a 15k worker prefix is under that model's minimum cacheable length (then input is billed in full).
6. Output quality: swarm model against single-run model, side-by-side renders.
7. Interface mismatch between independently built pieces is the main design risk and cannot be estimated without trying.

## Tasks (in order)

| # | Task | Brief | Depends on |
|---|---|---|---|
| 01 | Measure prices, baseline and worker capability | [01-measure-first.md](01-measure-first.md) | none |
| 02 | Reusable worker loop and the delegate tool | [02-worker-loop.md](02-worker-loop.md) | 01 (go/no-go), prototype `subagents.py` |
| 03 | Worker model selection and settings | [03-worker-model-config.md](03-worker-model-config.md) | 02 |
| 04 | Shared budget ledger and usage recording | [04-shared-budget.md](04-shared-budget.md) | 02 |
| 05 | File isolation, permissions and approvals | [05-isolation-permissions.md](05-isolation-permissions.md) | 02 |
| 06 | Parallelism, failure handling and validation gate | [06-parallel-and-failures.md](06-parallel-and-failures.md) | 04, 05 |
| 07 | Planner prompt and split heuristics | [07-planner-prompt.md](07-planner-prompt.md) | 02 |
| 08 | UI | [08-ui.md](08-ui.md) | 04, 06 |
| 09 | End-to-end cost and quality check | [09-end-to-end.md](09-end-to-end.md) | 03, 06, 07, 08 |

Tasks 03, 04, 05 and 07 can run in parallel after 02.

## Shared constraints (every task)

- Build on the prototype in `app/web/backend/subagents.py`; do not redesign it. Keep the flag default off; with it off, behaviour and token use of existing chats are unchanged.
- Reuse `usage.step_cost_usd`, `usage.usage_totals`, `model_catalog.entry_profile`, `llm_config`, `tool_policy` and `ToolContext`. No new runtime dependency.
- Backend tests live in `app/web/backend/tests/` and run in the container: `docker compose exec ldraw-nova-app sh -c "cd /app/web/backend && pytest -q"`, plus `ruff check` from the repo root.
- Tests use the mock providers (`tests/mock_providers.py`), never a live API. Live checks are separate, manual and say what they cost.
- Commit once per task, with a plain commit message and no co-author line.

## Out of scope for this set

- Workers spawning workers (depth is 1).
- An OS-level sandbox per worker.
- Cross-chat worker pools, and the `claude_agent` browser-login path.
- Automatic choice of the worker model by task difficulty.
