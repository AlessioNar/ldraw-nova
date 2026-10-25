# 05 File isolation, permissions and approvals

**Goal:** workers write only to their own folder, never exceed the chat's permission mode, and never ask the user anything.

**Read first:** `app/web/backend/tools.py` (`ToolContext`, `resolve_path`, `t_write_file`, `run_command`), `app/web/backend/sandbox.py`, `app/web/backend/tool_policy.py` (`READ_TOOLS`), `app/web/backend/agent.py` (`execute_tool`, approvals), `app/web/backend/paths.py` (`safe_join`), `README.md` sections 5 and 6 in this folder.

**Build:**
1. `ToolContext.write_root: Path | None`. `resolve_path(write=True)` rejects paths outside it when set. Workers get `write_root = work_dir / "workers" / <id>`; the planner keeps `None`.
2. `run_python` in a worker runs with cwd set to the worker root. Workers never get `run_shell`.
3. Stray-write guard: hash `output/` (excluding the worker's own root and other workers' roots) at worker start and end; report changed or new files outside the worker root, restore or delete them, and add a line to the delegate result.
4. Worker tool gating: a function in `tool_policy.py` returning the worker's tools = contract `tools` intersected with the role default, the chat mode (`plan` or `read_only` leaves `READ_TOOLS` only and refuses `builder`) and the parts steering rule (`my_parts` only when the chat has a steering mode, as in `available_tools`).
5. Approvals: `delegate` is asked once under `ask`, with an approval payload that carries the full contract. Inside an approved contract the worker's tool calls skip the approval step; a tool outside the contract returns a denial text, never an approval request. Under `full` nothing is asked.
6. Stop and cancel: cancelling the planner task cancels running workers.

**Acceptance:**
- A worker `write_file` to `../x` or to the planner's `output/` root is rejected; to its own folder it works.
- A worker `run_python` that writes outside its folder is reported and undone.
- In `read_only` mode a `builder` delegation is refused with a clear message; a `researcher` runs with read tools only.
- No `approval` event is ever emitted by a worker.

**Tests:** `tests/test_subagents.py` and `tests/test_agent.py` style; path-escape cases next to `tests/test_paths.py`.

**Out of scope:** OS-level jail, per-tool user prompts for workers, network restrictions.
