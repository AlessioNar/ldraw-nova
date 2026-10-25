# 07 Planner prompt and split heuristics

**Goal:** the planner delegates when it pays off and writes tight contracts, without bloating the cached prompt of chats that do not use workers.

**Read first:** `app/web/backend/prompts/system.md`, `app/web/backend/system_prompt.py` (`system_message`, lean prompt, tail), `docs/plans/done/loop-perf/FIX-cache.md`, `README.md` sections 1 and 2 in this folder, and `MEASUREMENTS.md` from task 01 for which roles are a go.

**Build:**
1. A short delegation section (target under 400 tokens) added to the stable system prompt only when the `delegate` tool is offered. Otherwise the prompt is byte-identical to today's. Content: the split rules (README section 1), the contract fields with one worked example per role, "state interface dimensions", "never place a submodel whose validate verdict is not ok", one redelegation per piece, "you integrate and publish".
2. Keep it in the cached prefix, not the trailer, and constant for the whole chat so the prefix does not change between steps. Document the one-time prefix change when a chat turns delegation on.
3. A `delegate` tool description that carries the field limits.

**Acceptance:**
- Prompt unchanged byte for byte when delegation is off.
- The extra prompt text is constant across steps (assert in the `test_prompt_cache.py` style).
- Under 400 tokens by the repo's chars/4 estimate.

**Live check (manual, state the cost):** one chat with a mid-size model prompt; compare planner steps and delegations with and without the section; record in `MEASUREMENTS.md`.

**Out of scope:** model-specific prompt tuning, changing the plan format.
