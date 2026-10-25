# 04 Shared budget ledger and usage recording

**Goal:** `budget_eur` caps planner plus workers, and worker cost appears in the chat's usage totals.

**Read first:** `app/web/backend/usage.py` (`usage_totals`, `step_cost_usd`), `app/web/backend/turn.py` (the budget check at the top of the loop), `app/web/backend/run_state.py` (`Run`), `app/web/backend/tests/test_cost_and_budget.py`, `README.md` section 4 in this folder.

**Build:**
1. `BudgetLedger` (in `subagents.py` or a small `budget.py`): `limit_eur`, `live_eur`, per-worker reservations, `reserve(worker_id, max_eur)`, `add(worker_id, cost_usd)`, `release(worker_id)`, `remaining_eur()`. Held on the `Run`. Internal amounts in USD, converted with `settings.eur_per_usd()`.
2. The planner's budget check uses stored cost plus `ledger.live`. The worker step hook (task 02) stops the worker with status `over_budget` when stored + live + other reservations would pass the limit. A worker's `max_eur` is clamped to `remaining_eur()`.
3. When a worker ends (also on cancel or error, in a `finally`), write the aggregate `_usage` record (summed tokens, summed `cost_usd`) and `_worker: {id, model, steps, status}` on the delegate tool-result message, release the reservation, and move its cost from `live` to stored.
4. Refuse to start a worker whose pricing is unknown while a budget is set.
5. `usage.usage_totals` gains `worker_cost_eur` (sum over messages with `_worker`), without changing the existing fields.

**Acceptance:**
- Planner cost 0.10 plus two concurrent workers that would each spend 0.30 under a 0.50 budget: recorded total never exceeds 0.50 plus one step of overshoot, and both workers report `over_budget` or finish within their clamp.
- `usage_totals(messages)["cost_eur"]` equals planner plus worker cost after reload from `messages.jsonl`; a worker killed by Stop is still counted.
- With no worker, `usage_totals` output is unchanged except for the new field being 0.

**Tests:** extend `tests/test_cost_and_budget.py` and `tests/test_subagents.py` with mock providers that return fixed usage.

**Out of scope:** UI display (08), currency changes, per-worker budgets chosen by the user.
