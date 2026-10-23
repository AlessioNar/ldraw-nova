# 02 Reusable worker loop and the delegate tool

**Goal:** a worker can run the same agent loop as the planner on its own model, from a bounded contract, and the `delegate` tool returns its result to the planner. Build on the flag-gated prototype; do not redesign it.

**Read first:** `app/web/backend/subagents.py` (the prototype, written in parallel; it is not on this branch yet, so wait for it or stub the same names), `app/web/backend/turn.py`, `app/web/backend/agent.py`, `app/web/backend/tools.py` (`TOOLS`, `ToolContext`, `dispatch`), `app/web/backend/tool_policy.py`, `app/web/backend/prompts/system.md`, `README.md` section 2 in this folder.

**Build:**
1. Extract the per-step body of `run_turn` (stream, build the message, usage record with `step_cost_usd`, save, run tool calls) into a function that both the planner and a worker call. Parameters: entry, message sink (the chat store for the planner, a side file `chats/<chat>/workers/<id>.jsonl` for a worker), tool list, step cap, a per-step hook (used later by the budget). The planner's behaviour and stored messages stay byte-identical when delegation is off; `tests/test_agent.py` and `tests/test_prompt_cache.py` pass unchanged.
2. A worker runner in `subagents.py` taking the contract of README section 2 (`role, task, inputs, tools, outputs, max_steps, max_eur, summary_chars`). It validates the contract: unknown tools, ceilings exceeded, outputs outside `output/workers/<id>/` are rejected with a message the planner can read.
3. The fixed lean worker system prompt per role (builder, researcher, reviewer) and the capped final summary.
4. The `delegate` tool entry in `TOOLS`, offered by `tool_policy.available_tools` only when the flag is on and a worker model is configured (task 03 supplies the configuration; until then read it from the flag's env var).
5. The result format from README section 2: status, summary, files with sizes, cost. The aggregate usage record is task 04; here return the cost as text only.

**Acceptance:**
- With the flag off, `available_tools` output and every stored message are unchanged.
- With a mock provider, a planner step that calls `delegate` gets a worker run of at most `max_steps` model calls and a result message; the worker's messages are in the side file and not in `messages.jsonl`.
- A worker cannot call `delegate`, `publish_model` or `run_shell`.
- The planner's request prefix (system plus history) is byte-identical before and after a delegate result, apart from the appended messages (assert as in `test_prompt_cache.py`).

**Tests:** `app/web/backend/tests/test_subagents.py` using `tests/mock_providers.py`.

**Out of scope:** budget sharing (04), isolation (05), concurrency and validation (06), UI.
