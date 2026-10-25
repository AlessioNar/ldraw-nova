# 01 Plan editing core

**Goal:** pure Python functions that load, edit, validate and rebuild a plan, with no web code. Every later task calls these.

**Read first:** `ldraw_tools/builder.py` (`load_plan`, `check_schema`, `build_plan`, `snap_placement`, `serialize_mpd`), `ldraw_tools/data/plan.schema.json`, and one example plan under `examples/`.

**Build:** a new module `ldraw_tools/editor.py` with:

1. Edit operations as plain dicts, checked against the same schema:
   - `add(section, part, colour, position, rotation)`
   - `move(entry_id, position)`, `rotate(entry_id, axis, degrees)`
   - `recolour(entry_id, colour)`, `delete(entry_id)`
2. `apply(plan, edits) -> plan`. It returns a new plan and never mutates its input. Each entry needs a stable `id` that survives unrelated edits: generate it once and store it in the plan file.
3. `rebuild(plan) -> mpd text`: runs `check_schema`, then `build_plan`. On failure it raises an error that names the offending entry id.
4. `snap(plan, entry_id)`: uses `snap_placement`, so a dropped part locks to its studs or connections.
5. Undo history as a list of inverse operations, not full copies of the plan.

**Acceptance:**
- Applying an operation and then its inverse gives back byte-identical plan JSON.
- The input plan is never mutated. Test this with a deep-copied snapshot.
- Unknown part numbers, colours and ids are rejected with a message, not a traceback.
- `rebuild` of an unedited example equals `build_plan` of that example. No drift.

**Tests:** the toolkit's tests live under `tests/` at the repo root. Follow the naming used there. Do not put these in `app/web/backend/tests/`.

**Out of scope:** any HTTP, UI or persistence.
