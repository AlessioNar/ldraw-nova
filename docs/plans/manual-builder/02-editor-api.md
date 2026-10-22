# 02 Editor API and sessions

**Goal:** HTTP endpoints that let the web app open, edit and save a plan for a chat, using the functions from task 01.

**Read first:** `app/web/backend/routers/chats.py` and `models_gallery.py` (the pattern to follow), `app/web/backend/store.py` (where a chat's files live), and `app/web/backend/sandbox.py` (how toolkit commands run).

**Build:**
- `GET /api/chats/{id}/editor`: the current plan, its MPD preview, and the undo depth.
- `POST /api/chats/{id}/editor/edits`: applies a list of operations from task 01 and returns the new plan and rebuild status. Reject the whole request if any operation is invalid.
- `POST /api/chats/{id}/editor/undo` and `/redo`.
- `POST /api/chats/{id}/editor/publish`: writes `output/<name>.mpd` and calls the existing publish path, so the model appears in My Models.
- Plans live in the chat's `output/editor/` folder. Writes are atomic (write a temp file, then rename), as `environment_config.save` does.

**Acceptance:**
- Concurrent edits from two browser tabs are serialised per chat with a lock. A request whose base revision is stale gets a 409 with the current revision.
- Every route is covered by a test that uses the `TestClient` pattern from `tests/test_api.py`.
- A failed rebuild never overwrites the last good plan.
- Every response includes the used and available counts per (part, colour) for the chat's selected inventories, computed from the plan. Adding a part past the owned quantity is rejected in `only` mode and flagged in `prefer` mode. Deleting or moving a part returns its count to the pool in the same response.

**Out of scope:** the 3D view (task 03) and the agent (task 05).
