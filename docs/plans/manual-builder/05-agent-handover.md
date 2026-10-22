# 05 Agent and editor share a plan

**Goal:** the agent can read and change the plan the person is editing, and the person can take over a plan the agent made.

**Read first:** `app/web/backend/tools.py` (tool definitions and `dispatch`), `app/web/backend/agent.py` and `turn.py` (the tool loop), and `app/web/backend/tool_policy.py` (which tools each mode allows).

**Build:**
- An `editor_edit` tool with the operations from task 01. It is available only in agent mode, and only when the chat has an editor plan. It returns a short summary of the plan and the rebuild status, not the full JSON, which keeps tokens low.
- An `editor_state` tool that returns a compact summary: part counts, bounding box and the last validation status.
- When the agent edits a plan, the open editor refreshes from the server rather than showing a stale copy. The chat lists the change as one message.
- An agent's plan-based build is saved as the chat's editor plan, so the person can open it.

**Acceptance:**
- In `plan` and `chat` modes the editor tools are not offered. Add this to `tool_policy.py` and test it the way the parts tools are tested.
- An unsaved edit by the person is never overwritten by the agent. The agent gets a conflict result and must re-read the plan.
- An edit turn's tool result stays under 1,000 characters.
- `editor_state` reports the available quantities after the plan's used parts are taken off, so the agent's `my_parts` and `buildable_models` answers match what the editor shows. An agent edit that uses a part already at zero available is refused, not silently overdrawn.

**Out of scope:** new agent prompts beyond one short paragraph in `prompts/system.md`.
