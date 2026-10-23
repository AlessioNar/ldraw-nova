# 06 Parallelism, failure handling and validation gate

**Goal:** several workers run at once without overshooting limits, and every failure mode returns a status the planner can act on.

**Read first:** `app/web/backend/turn.py` (the tool-call loop that awaits calls one by one), `app/web/backend/tools.py` (`t_publish_model`, `parts_check`), `ldraw_tools/cli.py` (the `validate` command, reached through `run_toolkit`), `app/web/backend/run_state.py`, `README.md` sections 5 and 6 in this folder.

**Build:**
1. A per-run `asyncio.Semaphore` of `LDRAW_NOVA_MAX_WORKERS` (default 3, ceiling 6), read in `settings.py`. In `turn.py`, run all `delegate` calls of one planner step with `asyncio.gather`; other tools stay sequential. Save the tool results in call order.
2. Stuck detection in the worker loop: step cap, the same tool call with identical arguments three times, no output file written in 8 steps. Statuses `step_cap` and `stuck`; partial outputs kept and listed.
3. Validation gate run by the app after each worker: toolkit `validate` plus the parts check (reuse `parts_check`) on declared `.ldr` and plan outputs. Missing or unparseable output gives `failed`.
4. One automatic repair round: the same worker continues with the validator's error text for at most 8 more steps, then final status `partial` with errors if still invalid. The budget still applies.
5. Provider errors and timeouts inside a worker end it as `failed` with the exception class name only (no secrets, as in `turn.py`'s handler).
6. Per-turn delegation cap (default 8).

**Acceptance:**
- Three workers on a mock provider with a delay run concurrently (wall time near one worker); a fourth waits.
- Each failure mode (stuck loop, step cap, over budget, empty output, invalid LDraw fixed by repair, invalid LDraw not fixed, provider exception) has a test and returns the documented status; the turn continues in every case.
- Stop cancels all workers and their cost is recorded.

**Tests:** `tests/test_subagents.py` with a mock provider that can loop, stall, raise and emit invalid LDraw.

**Out of scope:** quality scoring, automatic redelegation by the app (the planner decides), UI.
