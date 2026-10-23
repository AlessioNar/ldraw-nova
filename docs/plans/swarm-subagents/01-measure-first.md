# 01 Measure prices, baseline and worker capability

**Goal:** replace the three biggest guesses in the cost model with numbers, and give a go/no-go for the rest of the set. No product code.

**Read first:** `README.md` sections 8 and 9 in this folder, `docs/plans/loop-perf/live_check.py` and `docs/plans/loop-perf/analyze.py` (how recorded chats are read), `app/web/backend/usage.py`, `app/web/backend/model_catalog_data.json` (Haiku 4.5 is listed at $1 / $5), `app/web/backend/tests/test_wire_live_check.py`.

**Build:** a script `docs/plans/swarm-subagents/measure.py` and a results file `docs/plans/swarm-subagents/MEASUREMENTS.md`:

1. Prices: for the Haiku-class model the owner means (Haiku 5.5 if it exists, else Haiku 4.5), record input, output, cache read and cache write prices from the provider's pricing page and from `litellm.get_model_info`. State the source and date. If the model is not on the owner's account, say so.
2. Baseline split: from the recorded chats the loop-perf work used (the paths its scripts take), classify each assistant step as research (`my_parts`, `buildable_models`, `read_file` of docs), sub-assembly building, integration, validation or render review, and report the share of cost per class using `_usage.cost_usd`. This is the ceiling on what delegation can move.
3. Worker capability: using the existing tools and a lean prompt, run the Haiku-class model on 5 fixed sub-assembly tasks (for example a 4-wheel chassis with given axle positions, a 2x4 roof, a seat pair) with a 20-step cap, through a throwaway loop that calls `litellm` with the repo's tools. For each run record: valid on first try (toolkit `validate`), repair rounds needed, steps, tokens, cost, cache reads from step 2, and whether the prefix cached at all.
4. A one-page conclusion in `MEASUREMENTS.md`: the corrected rows of the README cost table, and go or no-go per role (builder, researcher, reviewer).

**Acceptance:**
- Every number in `MEASUREMENTS.md` names its source (provider page, file, run id) and date.
- The README table can be recomputed from the recorded values with no unstated assumption.
- The script runs offline for item 2 and says clearly which parts need an API key and what they cost (cap the live runs at about 1 EUR total, enforced in the script).

**Out of scope:** changes under `app/web/backend/`, the delegate tool, the UI.
