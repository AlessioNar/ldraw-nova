# Manual 3D builder: plan set

Goal: a browser editor where a person places, moves, rotates, recolours and deletes LDraw parts in 3D. The result is the same plan JSON the agent already builds from (`ldraw_tools/builder.py`, `plan.schema.json`), so a model started by hand can be continued by the agent, and the reverse.

## Shared constraints (every task)

- The plan is the source of truth. The editor never writes MPD directly; it edits a plan and rebuilds it with `build_plan`.
- Reuse what exists: `snap_placement` and `build_plan` in `ldraw_tools/builder.py`, the three.js viewer in `app/web/viewer/` (ldbi, Three.js r139), the parts search behind `my_parts` and `discover search`, and the Settings and Chat pages in `app/web/frontend/`.
- Add no runtime dependency unless the task says so. Pin any new npm or pip package exactly and explain why in the pull request.
- No model changes without an explicit save. Autosave writes only to the chat's `output/` folder.
- Each task ends with tests that run in the container: `docker compose exec ldraw-nova-app sh -c "cd /app/web/backend && pytest -q"`, and `ruff check` from the repo root.
- Commit once per task, with a plain commit message and no co-author line.
- **Used parts come off the available pool.** A part placed in a model is marked as used and no longer counts as available in that chat's inventories. Removing it from the model returns it to the pool. "Available" means owned minus used in the model being edited (see the open question below). The pool is computed from the plan, never stored as a separate count, so it cannot drift from the model. The same rule applies to the palette (task 04), the API responses (task 02) and the agent's view (task 05).

**Open question for the owner:** should a part used in one model also come off the pool for other models in the same chat? The briefs assume per model until this is answered.

## Tasks (in order; each depends on the ones before it)

| # | Task | Brief | Depends on |
|---|---|---|---|
| 01 | Plan editing core | [01-plan-editing-core.md](01-plan-editing-core.md) | none |
| 02 | Editor API and sessions | [02-editor-api.md](02-editor-api.md) | 01 |
| 03 | 3D editor view | [03-editor-view.md](03-editor-view.md) | 02 |
| 04 | Part palette and colours | [04-palette-and-colours.md](04-palette-and-colours.md) | 02 |
| 05 | Agent and editor share a plan | [05-agent-handover.md](05-agent-handover.md) | 02 |
| 06 | Hardening and end-to-end checks | [06-verification.md](06-verification.md) | 03, 04, 05 |

Tasks 03 and 04 can run in parallel after 02. Task 05 can run in parallel with 03 and 04.

## Out of scope for this set

- Multi-user editing and locking.
- Editing MPD files that have no plan. Importing them is a later task.
- Physical stability and connection checks beyond what `validate` already does.
