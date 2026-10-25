# 09 End-to-end cost and quality check

**Goal:** show, on real runs, whether planner plus workers reaches at most 2 EUR for a medium model with acceptable quality, and set the defaults from the numbers.

**Read first:** `README.md` sections 8 and 9 in this folder, `MEASUREMENTS.md` from task 01, `docs/plans/done/loop-perf/live_check.py`, `app/web/backend/tests/test_wire_live_check.py`.

**Build:** a script `docs/plans/done/swarm-subagents/e2e.py` and a results file `docs/plans/done/swarm-subagents/E2E.md`:

1. Three fixed prompts (small, medium, one that is hard to split), each run in a fresh chat under: single Opus 5.5, single Sonnet 5.5, Sonnet planner with Haiku-class workers, and if budget allows Opus planner with the same workers. Each run has `budget_eur` 4.
2. Per run: total euros from `usage_totals` split into planner and workers, steps, delegations, statuses, repair rounds, wall time, parts-check result, validate verdict, final renders saved.
3. Compare with the README table row by row; list every assumption that was off by more than 30 percent.
4. A quality read: side-by-side renders and a short human score (recognisable, stable, owned parts), filled in by the owner.
5. Set defaults from the data: worker count, step caps, `max_eur`, repair rounds, and whether builders are delegated at all.

**Acceptance:**
- `E2E.md` has one table with the measured cost per configuration and prompt, and states whether the 2 EUR target is met for the medium prompt, and at what quality.
- The defaults in `settings.py` match the conclusions, or `E2E.md` says why not.
- The script stops any run that would pass 4 EUR and records that.

**Out of scope:** new features; anything the results suggest goes into a follow-up brief.
