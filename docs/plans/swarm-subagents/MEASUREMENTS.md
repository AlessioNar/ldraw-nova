# Measurements for brief 01 (prices, baseline split, worker capability)

Date of measurement: 2026-10-23. Script: `docs/plans/swarm-subagents/measure.py` (tests: `test_measure.py`). Nothing under `app/web/backend/` was changed. The live part (worker capability, section 3) has NOT run: there is no API key in the measuring environment. Everything that depends on it is marked **ASSUMPTION** below.

Sources used:
- **Pricing page**: https://platform.claude.com/docs/en/about-claude/pricing, read 2026-10-23 by the owner's session. The figures were handed to this task as verified; this task did not re-fetch the page.
- **PRICE_BOOK**: `app/web/backend/model_catalog.py`, this branch (commit e0141a4a), run through `model_catalog.profile` and `usage.step_cost_usd`.
- **litellm**: `litellm.get_model_info` inside the app image `ldraw-nova-app` (image id f4a2ee64ecbb), 2026-10-23.
- **Chats** (owner's real recordings, read-only): `C:\Users\aless\Desktop\Developer\lego\ldraw-nova\app\data\chats\20261022-170345-ad9df6` ("small red car with four black wheels", effort medium) and `...\20261022-191915-1bfc0b` ("6 x 8 cottage with a door, two windows and a sloped roof", effort high). Both claude-opus-5-5, permissions full, 1M context. 43 and 39 assistant messages; 38 and 38 carry `_usage` (the rest are interrupted or "please continue" turns with no usage, so they are unpriced and left out).
- **Loop-perf chats** in git history (`b24c492c`): 5 chats, none with `_usage`.

## 1. Prices (offline)

USD per 1M tokens. "above" = prompt over 100,000 tokens.

| model | tier | page in/out | page cache hit | page 5m write | page 1h write | PRICE_BOOK in/out, hit, 5m, 1h | litellm in/out, hit, 5m, 1h |
|---|---|---|---|---|---|---|---|
| Haiku 5.5 | up to 100k | 0.10 / 0.50 | 0.01 | 0.125 | 0.20 | 0.10 / 0.50, 0.01, 0.125, 0.20 | 0.10 / 0.50, 0.01, 0.125, 0.20 |
| Haiku 5.5 | above 100k | 0.50 / 2.50 | 0.05 | 0.625 | 1.00 | 0.50 / 2.50, 0.05, 0.625, 1.00 | no such tier |
| Sonnet 5.5 | any | 2 / 10 | 0.10 | not restated (1.25x) | not restated (2x) | 2 / 10, 0.10, 2.50, 4.00 | 2 / 10, 0.10, 2.50, 4.00 |
| Opus 5.5 | any | 4 / 20 | 0.20 | not restated (1.25x) | 8 | 4 / 20, 0.20, 5, 8 | 4 / 20, 0.20, 5, 8 |
| Haiku 4.5 | any | 1 / 5 | not restated | not restated | not restated | 1 / 5, 0.10, 1.25, 2 (derived) | 1 / 5, 0.10, 1.25, 2 |

(Regenerate: `measure.py prices`, run in a throwaway container, see section 5.)

Disagreements: exactly one. litellm's entry for Haiku 5.5 has no above-100k-tokens tier, so it understates a long Haiku 5.5 prompt by 5x. `PRICE_BOOK` agrees with the page on every figure the page states, including the tier (cache hit 0.05, 5m write 0.625 and 1h write 1.00 above 100k follow from the 0.1x, 1.25x and 2x multipliers applied to the $0.50 tier price). The README's earlier statement that LiteLLM lists Haiku 5.5 at $0.1 / $0.5 is right; its Haiku 4.5 figure ($1 / $5) also matches. `model_catalog_data.json` has no Haiku 5.5 entry; the bundled catalogue is therefore not the price source for it, `PRICE_BOOK` is.

Haiku 5.5 exists in litellm's catalogue (1M context, vision and function calling both true). **Whether Haiku 5.5 is available on the owner's account cannot be checked offline.** The live run (section 3) is the first thing that will tell: a "model not found" or permission error on the first call.

Worked check of the app's tier handling (`usage.step_cost_usd`, 1h cache): Haiku 5.5, prompt 150,000, of which 100,000 read and 20,000 written, 1,000 output. Fresh 30,000 + 100,000 x 0.1 + 20,000 x 2 = 80,000 weighted tokens x $0.50 = $0.0400, plus 1,000 x $2.50 = $0.0025, total $0.0425. The function returned $0.042500.

## 2. Baseline split (offline, from the owner's recorded chats)

Every assistant step was priced from its `_usage` tokens with `usage.step_cost_usd`, `model_catalog.profile(model)["pricing"]` (Opus 5.5: $4 / $20, reads 0.05x), 1h cache writes (2x, the app default `CACHE_TTL=1h`), EUR at 0.92. Command: `measure.py baseline` (inside the container). With 5m writes (1.25x) the two chats cost $9.30 and $6.41 instead; all shares below move by a few points only.

### Per chat

| chat | steps priced | prompt tokens | completion tokens | cost |
|---|---|---|---|---|
| 20261022-170345-ad9df6 (car) | 38 | 3,002,150 | 73,719 | $13.82 |
| 20261022-191915-1bfc0b (cottage) | 38 | 1,955,281 | 258,846 | $6.84 |
| both | 76 | 4,957,431 | 332,565 | $20.66 = EUR 19.01 |

Per class and chat (steps, cost, share of that chat's cost):

| class | car chat | cottage chat | both |
|---|---|---|---|
| research | 19 steps, $3.63 (26%) | 25 steps, $4.51 (66%) | 44 steps, $8.14 (39.4%) |
| building | 8 steps, $4.77 (35%) | 3 steps, $0.70 (10%) | 11 steps, $5.47 (26.5%) |
| validation / review | 8 steps, $3.24 (23%) | 7 steps, $0.72 (11%) | 15 steps, $3.96 (19.2%) |
| other | 3 steps, $2.18 (16%) | 3 steps, $0.91 (13%) | 6 steps, $3.10 (15.0%) |

Tokens per class, both chats (prompt / completion): research 2,258,282 / 219,324; building 952,369 / 42,231; validation 1,210,417 / 28,143; other 536,363 / 42,867.

### What the recordings show about where the money goes

- Cost by component, both chats: cache writes $13.20 (64%), output $6.65 (32%), cache reads $0.65 (3%), uncached input $0.15 (1%).
- In the car chat, 19 of 38 steps wrote most of the prompt to the cache again (cache write above half the prompt, cache read stuck at about 25k = the system prompt only). Those 19 steps cost $12.31 of its $13.82. The chat was recorded before or without the rolling cache breakpoint working, so the history prefix was lost on every other step. In the cottage chat only 1 step did (cost $0.36); there output dominated: 258,846 completion tokens = $5.18 of $6.84 (hidden reasoning on shell steps).
- **Counterfactual with a healthy rolling cache** (ASSUMPTION: each step reads the previous step's whole prompt and writes only the new tail; same output): both chats $9.28 (EUR 8.53), research 65.1%, building 12.3%, validation 11.3%, other 11.4%. This is closer to what the current loop should cost, and it is the better baseline for judging a swarm built on the current loop.

### Fixed prompt versus history in each step's input

Fixed prefix = the smallest positive `cache_read` in the chat (the stable system prompt plus tool schemas, which the breakpoint on the system message keeps readable).

| chat | fixed prefix | fixed share of all input tokens | history and new tool output share |
|---|---|---|---|
| car | 24,882 tokens | 31% | 69% |
| cottage | 25,417 tokens | 49% | 51% |

So a delegated step with a lean worker prompt does not carry the planner's 25k prefix, but half or more of what the planner re-sends each step is its own history.

### The ceiling on what delegation can move

Upper bound = the share of cost in steps whose work could be handed over, if the workers cost nothing and the planner paid nothing to brief them or read results:

| case | research | validation | building | research + validation | + building |
|---|---|---|---|---|---|
| as recorded | 39.4% | 19.2% | 26.5% | 58.6% | 85.1% |
| healthy-cache counterfactual | 65.1% | 11.3% | 12.3% | 76.4% | 88.7% |

Read this as a ceiling only. Real savings are smaller: the planner still pays for contracts and results, building steps are mostly integration and fixing (not a clean sub-assembly), and the research share in the cottage chat is mostly long hidden reasoning (174,541 completion tokens in 25 steps) that a cheap worker does not remove, it only makes cheaper. Against the README's 40% threshold: research alone is 39.4% as recorded (just under) or 65.1% with a healthy cache. Validation alone (11-19%) is the weakest candidate to delegate.

### Classification rules and the judgement calls (all in `measure.py`, `classify_call` and `classify_step`)

A step takes the highest-priority class among its tool calls: building, then validation, then research; a step with no tool call (a plain reply) is `other`.
- `run_toolkit` first argument: `discover`, `search`, `catalog`, `examples`, `study` plus `doctor`, `part`, `inventory` (show or search), `matrix`, `spec`, `extract` = research. `build`, `technic`, `spaceship`, `mechanism`, `vehicle` = building. `validate`, `inspect`, `cad-check`, `connectors`, `render`, `compare-bom`, `bom`, `manual` = validation.
- Judgement: `vehicle list` and `vehicle wheels` are research, `vehicle check` and `inventory check` are validation, other `vehicle` subcommands are building.
- Judgement: `run_shell` and `run_python` have no fixed meaning. Text mentioning `ldraw-agent build`, `.plan.json`, `write_text`, `json.dump`, `sed -i`, `generator` or `.write(` = building (this includes edits of plans and generators); text running `ldraw-agent validate/inspect/connectors/render/bom/inventory check` = validation; everything else (grep, sed -n, ls, cat, geometry lookups) = research. A step that edits a plan and also validates counts as building.
- Judgement: the cottage chat spent four shell steps (ids 8 to 18) grepping the toolkit source to debug a `.cache` permission problem. Those are classed research; they are really environment debugging and are no candidate for a worker.
- Judgement: `write_file` of `.md`, `.txt`, notes or reports = other (hand-over notes); any other `write_file` = building. `view_image` and `publish_model` = validation. `my_parts`, `buildable_models`, `read_file`, `list_files` = research. `report_progress` = other.
- Judgement: a step whose result is a final reply after a long chain (no tool call) is `other`; in the car chat these absorbed $2.18 mostly because the prefix had been lost (full 80k rewrite on a step that only writes the summary).
- Per-step cost is attributed in full to its class, including the cache write of the whole history, which is not caused by that step's own work.

### Other recorded chats (docs/plans/loop-perf)

`docs/plans/loop-perf` holds scripts and reports, no chat data files. Its 5 chats are in git history at `b24c492c` (`app/data/chats/<id>/messages.jsonl`). None has any `_usage` (the report says the field was added later), so they cannot be priced from billed tokens and are not usable for dollar shares. Step counts only (same classifier, `measure.py history`):

| chat | assistant steps | research | building | validation | other |
|---|---|---|---|---|---|
| 20260928-125940-5e681c | 55 | 13 | 15 | 17 | 10 |
| 20260929-213629-dd4aed | 176 | 45 | 39 | 48 | 44 |
| 20260930-201212-16f983 | 142 | 53 | 39 | 16 | 34 |
| 20261003-100404-9e6e55 | 151 | 49 | 69 | 26 | 7 |
| 20261003-110049-79b3d1 | 89 | 39 | 25 | 20 | 5 |

By step count research is 24%, 26% and 37% in the three Claude/GPT chats and 32% and 44% in the two Qwen chats, the same range as the dollar shares above. Step share is not cost share.

One reconciliation: README section 8 says a measured red-car chat cost $4.2 (0.5M cache writes, 62k output). The recorded car chat here has 1.51M writes, 1.50M reads and 73,719 output tokens and prices to $13.82 (1h) or $9.30 (5m); the README figure is not reproducible from this file (probably an earlier snapshot of the chat or a different run).

## 3. Worker capability (live, run 2026-10-23)

**Result: Haiku 5.5 ran all five sub-assembly tasks through the app's tool loop for EUR 0.072 in total (two runs). The model is available on the owner's account, litellm accepts `anthropic/claude-haiku-5-5` with the app's cache markers, and the prefix cached from step 2 on every task.**

How it was run: the harness below, in a throwaway container of the app image, with the owner's stored Anthropic key loaded in-process (never printed or written), hard cap EUR 3.00 (owner-approved), model `anthropic/claude-haiku-5-5`. Source files: `capability-results.json` (first run, all five tasks) and `capability-results-rerun.json` (chassis and roof only, after the fix below).

**First run, and a harness fault found by it.** The first run used a 4,096-token output limit per call. Chassis and roof ended after 3 and 2 steps with no file and an empty reply: the model's reply was cut off at the limit and the harness recorded "finished". That was the harness, not the model, so those two were re-run. The harness now records each step's `finish_reason`, marks a reply cut off by the limit as `truncated`, takes `--only`, and uses a 16,000-token limit. The two re-runs finished normally (`finish_reason` `stop`).

| task | valid first try (toolkit `validate`) | repair rounds | steps | prompt tokens | output tokens | cache read tokens | cost |
|---|---|---|---|---|---|---|---|
| chassis | True | 0 | 9 | 126,328 | 33,424 | 103,537 | $0.0223 (EUR 0.0205) |
| roof | True | 0 | 13 | 172,733 | 36,303 | 152,399 | $0.0237 (EUR 0.0218) |
| seats | True | 0 | 5 | 42,918 | 7,453 | 31,707 | $0.0063 (EUR 0.0058) |
| window | True | 0 | 6 | 73,303 | 7,767 | 56,513 | $0.0078 (EUR 0.0072) |
| door | True | 0 | 8 | 118,358 | 12,026 | 97,085 | $0.0112 (EUR 0.0103) |

Totals over the five final runs: $0.0714 (EUR 0.0657), mean $0.0143 per task (the README assumed $0.029 to $0.036), 533,640 prompt tokens of which 82.7% were cache reads, 96,973 output tokens. Output (largely hidden reasoning at the default effort) is most of each worker's cost; the harness sends no effort setting. The wasted first-run truncated calls cost EUR 0.0068.

**Quality, from the workers' own final replies (they reported these defects honestly) and the toolkit's verdict:**
- All five files pass `validate` on the first try with zero repair rounds. But `validate` passing is weaker than the brief: the toolkit reports disconnected parts as information, not as a failure.
- seats: one plate and two seats, connected, orientation not visually confirmed.
- window: frame and glass, 2 disconnected groups ("one connected structure" not met).
- door: frame and leaf, 2 disconnected groups (not met).
- chassis: wheels, holders and plates, 6 disconnected groups (not met).
- roof: a single 2x4 double slope brick (3041). It meets the footprint and the ridge direction but is the minimal answer to "use roof slope bricks".
So by the toolkit's check the builder worked on every task; by the brief's own acceptance (one connected structure) it met it on 2 of 5 (seats, roof). Connectivity is exactly the check the app-run validation gate of brief 06 must add, with one repair round.

Not tested by this run: researcher and reviewer quality, vision (`view_image`), more than one worker at once, and anything about how a planner integrates the pieces.

### The harness (`measure.py capability`)

Without `--live` it is a dry run (prints the tasks, the system prompt, caps, worst-case and expected cost; sends nothing, needs no key). With `--live` it needs `ANTHROPIC_API_KEY` in the environment only.

What it does, per task (chassis with given axle positions, 2x4 sloped roof, seat pair, window frame, door; interface values are in the task text):
- litellm `acompletion` loop with a lean system prompt (about 2k tokens) and the repo's own tools `list_files`, `read_file`, `write_file`, `run_python`, `run_toolkit` (`tools.dispatch`), 20-step cap, 4,096 output tokens per call, tool results cut to 6,000 characters. System prompt and the last message carry the app's cache marker (1h for Anthropic).
- After every step that changed the output `.mpd`, the harness itself runs the toolkit `validate --geometry` on it (not a model step, not billed). Recorded per run: `valid_first_try` (first verdict), `repair_rounds` (verdicts before the first valid one), `valid_final`, whether the worker ran `validate` itself, steps, the four token counters, cost in USD and EUR priced by `usage.step_cost_usd` with the app's pricing, total cache reads, and `prefix_cached_from_step_2` (cache read above 0 from step 2 on). Results go to a JSON file (`--out`).
- Hard cap, default 1.00 EUR at `settings.eur_per_usd()` (0.92): before every call the script computes a pessimistic bound for that call (whole prompt billed as a 1h cache write at 2.5 chars per token, plus the full 4,096 output tokens, never less than 1.5x the previous step) and refuses the call if spent plus bound would pass the cap. The run stops and the report says `cap_eur`. It prints the worst-case cost before starting: with this design, up to $0.70 per task or $3.50 (EUR 3.22) for 5 tasks if every call used its full allowance with no caching, so the cap, not the worst case, bounds the spend. Expected cost under README assumptions is about $0.15 (EUR 0.14), an ASSUMPTION.
- Refuses a model whose price is unknown to the app (a cap could not be enforced).

Checks actually done (none of them touch the network):
- `python -m pytest docs/plans/swarm-subagents/test_measure.py`: 9 passed, 1 skipped on the host. The skipped test compares the script's local cost function with the app's `usage.step_cost_usd`; I ran the same comparison inside the app image by hand: identical on 18 cases (3 models, 3 token mixes, 5m and 1h). The tests cover: dry run output (5 tasks, caps, "DRY RUN"), `--live` without a key refuses, the cap stops a run before the call that could pass it and spent never exceeds the cap with a fake completion function, step cap and finish and cache flag, an unpriceable step stops the run, the classifier, and the validate parser.
- In a throwaway container of the app image (no network, no key): `tools.dispatch` `write_file` then `run_toolkit validate` on an MPD returned `checks_passed: true`, and on a file naming a missing part returned `checks_passed: false`; `parse_validate` read both correctly. This is the harness's validation path. It also showed the validator requires an MPD with a `0 FILE` block first, which the worker prompt now says.
- These offline checks did not touch any real model. The live run (top of this section) has since tested the model call, the cache markers, availability and the worker's LDraw quality; the hidden-reasoning cost at the default effort showed up as most of each worker's output tokens.

## 4. Corrected README section 8 rows (recomputed)

Inputs and where they come from:

| input | value | status |
|---|---|---|
| Opus 5.5 / Sonnet 5.5 price, cache read 0.05x | $4 / $20, $2 / $10 | measured (pricing page 2026-10-23) |
| Haiku 5.5 price | $0.10 / $0.50 up to 100k prompt | measured (page); availability on the account unverified |
| cache writes | 2x input price (1h, the app default `CACHE_TTL=1h`); 5m = 1.25x shown as the alternative | measured (page rule, `settings.py`) |
| baseline single Opus run | average of the two recorded chats = ($13.82 + $6.84) / 2 = $10.33 = EUR 9.50 | measured, 2 chats only |
| baseline if the cache worked | ($9.28) / 2 = $4.64 = EUR 4.27 | measured tokens, ASSUMPTION about caching |
| planner: 15 steps, 45k average context: 150k writes, 525k reads, 30k output | as in README | ASSUMPTION (recorded chats had 38 priced steps and 51k-79k average prompts) |
| workers: 6, each 100k written, 600k read, 20k output, +30% for repairs | as in README | ASSUMPTION until the live run gives steps, tokens, repairs and cache reads |
| worker prompt under 100k tokens | yes by design | ASSUMPTION |

Arithmetic (USD; writes = 150,000 x input price x 2; reads = 525,000 x input price x 0.05; output = 30,000 x output price):
- Sonnet planner: 150,000 x $2 x 2 = $0.600; 525,000 x $2 x 0.05 = $0.0525; 30,000 x $10 = $0.300; total $0.9525.
- Opus planner: 150,000 x $4 x 2 = $1.200; 525,000 x $4 x 0.05 = $0.105; 30,000 x $20 = $0.600; total $1.905.
- Worker case A (Haiku 5.5, $0.10 / $0.50): 100,000 x $0.10 x 2 = $0.0200; 600,000 x $0.10 x 0.1 = $0.0060; 20,000 x $0.50 = $0.0100; per worker $0.0360; 6 workers x 1.3 = $0.2808.
- Worker case B (Haiku 4.5, $1 / $5): 100,000 x $1 x 2 = $0.200; 600,000 x $1 x 0.1 = $0.060; 20,000 x $5 = $0.100; per worker $0.360; 6 x 1.3 = $2.808.
- Single Sonnet, same tokens as the baseline: every Opus rate is exactly twice the Sonnet rate, so $10.33 / 2 = $5.17.

| scenario | USD | EUR (x0.92) | vs baseline $10.33 | same with 5m cache writes (USD / EUR) |
|---|---|---|---|---|
| single Opus 5.5 (measured average, 2 chats) | 10.33 | 9.50 | 1.00x | n/a (recordings are priced with 1h writes; 5m would be 7.86 / 7.23) |
| single Sonnet 5.5, same tokens | 5.17 | 4.75 | 0.50x | |
| Sonnet planner + workers A | 1.23 | 1.14 | 0.12x | 0.95 / 0.87 |
| Opus planner + workers A | 2.19 | 2.01 | 0.21x | 1.68 / 1.54 |
| Sonnet planner + workers B | 3.76 | 3.46 | 0.36x | 2.95 / 2.71 |
| Opus planner + workers B | 4.71 | 4.34 | 0.46x | 3.68 / 3.38 |

What changed against the README rows (Sonnet+A 0.94 EUR, Opus+A 1.65 EUR, Sonnet+B 2.8, Opus+B 3.5): the README priced writes at 1.25x, but the app runs a 1h cache (2x). With 1h writes the Opus planner plus Haiku 5.5 workers lands at EUR 2.01, just over the 2 EUR target; the Sonnet planner stays inside it (EUR 1.14). With `CACHE_TTL=5m` the README numbers come back (EUR 1.54 and 0.87). Planner cache writes are the largest single line (1h: $1.20 of $2.19 for the Opus planner), so planner steps and history growth matter more than the worker price: worker case A costs only $0.28 of the total.

If the baseline had a healthy cache ($4.64 = EUR 4.27) the swarm saves less: Opus planner + A is 0.47x, Sonnet planner + A is 0.27x, and single Sonnet is 0.5x of $4.64 = $2.32 (EUR 2.13), already near the target on its own. So the swarm's advantage over simply switching to Sonnet is about 2x (Sonnet+A $1.23 against $2.32 here; against $5.17 with the cache as recorded it is 4.2x). That comparison is the honest one to carry into the go/no-go.

### Revised with the measured worker cost (2026-10-23)

The live run replaces one assumption: a worker costs $0.0143 on average (section 3), not $0.036. Six workers with the same +30% for repairs: 6 x 1.3 x $0.0143 = $0.1113 (was $0.2808). The planner lines are unchanged and are now 94% (Opus) of the swarm cost, so the planner assumption (15 steps at 45k context) decides the result.

| scenario | USD | EUR (x0.92) |
|---|---|---|
| Sonnet 5.5 planner + measured Haiku 5.5 workers | 1.06 | 0.98 |
| Opus 5.5 planner + measured Haiku 5.5 workers | 2.02 | 1.86 |

Both are under the owner's 2 EUR target, the Opus one with a margin of about EUR 0.14. This holds only while the planner stays near 15 steps and the hidden cache problem stays fixed (the measured recordings are from before the cache fixes). Brief 09 measures it end to end.

## 5. What the owner must do to run the live part

Needs: Docker, the `ldraw-nova-app` image (already built), an Anthropic API key with access to Haiku 5.5. It runs in a throwaway container (`docker run --rm`), not in the running `ldraw-nova-app` container, and does not touch port 8765 or `docker compose`. The key goes in only as an environment variable of that one container and is never read from the app's settings.

1. Dry run first (no key, no network, host Python is enough). From the repo root:
   `python docs/plans/swarm-subagents/measure.py capability`
   Check the 5 tasks, the caps and the printed worst case.
2. Set the key in the current PowerShell window only: `$env:ANTHROPIC_API_KEY = "<your key>"` (do not put it in a file).
3. Live run, from the repo root, PowerShell:
   `docker run --rm -e ANTHROPIC_API_KEY -v "${PWD}\app\web\backend:/app/web/backend:ro" -v "${PWD}\docs\plans\swarm-subagents:/m" -w /app/web/backend ldraw-nova-app python3 /m/measure.py capability --live --model anthropic/claude-haiku-5-5 --cap-eur 1.00 --out /m/capability-results.json`
   Git Bash: prefix with `MSYS_NO_PATHCONV=1` and use `$(cygpath -m "$PWD")` for the two paths. Results: `docs/plans/swarm-subagents/capability-results.json` (one JSON line per task is also printed).
4. Maximum cost: the hard cap, EUR 1.00 (USD 1.09 at 0.92). Expected, under the README assumptions, about EUR 0.14 (ASSUMPTION). Change the cap with `--cap-eur`; the rate comes from `LDRAW_NOVA_EUR_PER_USD` (default 0.92).
5. Read the result: if the first task fails with a model-not-found or permission error, Haiku 5.5 is not on the account; try `--model anthropic/claude-haiku-4-5` (then price case B applies and the dry run shows its cost; its price is in the app's catalogue).
6. Optionally repeat the offline parts: `measure.py prices`, `measure.py baseline --chats-dir /chats` (mount the chats folder read-only) in the same kind of container.

## 6. Go or no-go per role

**Verdict after the live run (2026-10-23):**
- **Builder: conditional GO** for small, fully specified sub-assemblies. 5 of 5 tasks passed `validate` first time at about $0.014 each, far under the $0.06 threshold, the cache worked on every task, and no task hit a step cap or the euro cap. The condition: the workers' output did not meet "one connected structure" on 3 of 5 tasks, so the app must run its own validation including connectivity and give one repair round (brief 06) before accepting a worker's file, and the planner must treat worker files as drafts to integrate.
- **Researcher: provisional GO.** The tool loop works on Haiku 5.5 and research is the largest, safest share of cost. Its quality is untested; brief 09 checks it.
- **Reviewer: defer**, unchanged.

Thresholds and the evidence they were set from (kept for reference):

The live run only exercises builders. The other two roles are decided on the baseline and price evidence plus what the run shows about tool use. Thresholds restate README section 9.

| role | evidence available now | GO when the live run shows | NO-GO when |
|---|---|---|---|
| builder (sub-assembly `.ldr`) | ceiling: building is 12-27% of cost, so the saving is capped low; price case A makes it cheap per run ($0.036 per worker assumed) | at least 3 of 5 tasks valid after at most one repair round (`valid_final` true and `repair_rounds` at most 1); mean cost per task at most $0.06 (about 1.7x the assumed $0.036, so six workers stay near $0.3); at most one task ends in `step_cap` or `cap_eur`; `prefix_cached_from_step_2` true on most runs (otherwise input is billed in full and the cost per task rises, recompute section 4) | fewer than 3 of 5 valid after one repair: builders stay with the planner. Also no-go if the cost per task is above $0.15 (then Haiku 5.5 is not the cheap option the model assumes) |
| researcher (`my_parts`, `discover`, notes) | ceiling: research is 39% of recorded cost (65% with a healthy cache), the largest and the safest share, read-only | the run completes with tool calls working on Haiku 5.5 (no errors from the model or the cache markers), steps and cost per task near the assumption, and cache reads from step 2. The 5 tasks do not test research quality; that needs the brief 09 end-to-end check | Haiku 5.5 unavailable on the account, or the tool loop fails on Haiku (then fall back to Haiku 4.5 and recompute with case B). Provisional GO on cost grounds, quality untested |
| reviewer (`validate`, renders, defect list) | ceiling: validation is 11-19% of cost, the smallest delegable share; `view_image` needs vision and adds image tokens (not modelled, 10-30k per step) | the workers' own use of `validate` (`worker_ran_validate` above 0 on at least 4 of 5 tasks) and their repair rounds show they read diagnostics correctly | defer: with a ceiling of 11-19% and untested vision, a reviewer worker is unlikely to pay for its briefing cost; do not build it before builder and researcher are proven. This is a judgement, not a measurement |

Rules for the decision: if Haiku 5.5 is unavailable, redo section 4 with case B (Haiku 4.5): the Opus planner then costs EUR 4.34, the 2 EUR target is missed, and delegation only makes sense with a Sonnet planner (EUR 3.46, still above 2); in that case the plan should be reduced to a few researcher calls or dropped in favour of using Sonnet alone.

## Unverified, in one list

Resolved by the live run on 2026-10-23: Haiku 5.5 availability on the owner's account, litellm and cache-marker compatibility, worker cost, steps, cache behaviour (82.7% of prompt tokens were cache reads), and that workers pass `validate` first time.

Still open:
- Whether a worker's output meets the brief's connectivity requirement: it did on 2 of 5 tasks. Needs the app-run validation gate and a repair round (brief 06).
- Researcher and reviewer quality, vision, and several workers at once.
- That the planner needs about 15 steps at 45k context: recorded chats had 38 steps, from before the cache fixes. The planner is now about 94% of the swarm's cost, so this is the number that decides whether the 2 EUR target holds.
- The healthy-cache counterfactual: assumes the current loop caches the way FIX-cache intends.
- Classification judgement calls in section 2; the sample is two chats of one task family (a vehicle and a house).
- Lowering a worker's hidden-reasoning output (most of its cost) with an effort setting: the harness sends none.
