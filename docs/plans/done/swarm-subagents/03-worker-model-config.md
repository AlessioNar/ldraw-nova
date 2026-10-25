# 03 Worker model selection and settings

**Goal:** the owner picks the worker model once (Haiku-class) and can override it per chat; with none set, delegation is off.

**Read first:** `app/web/backend/llm_config.py` (`get`, `builder_entries`, `capabilities`), `app/web/backend/model_catalog.py` (`validate_options`, `builder_supported`, `entry_profile`), `app/web/backend/settings.py` (env flags), `app/web/backend/agent.py` (`start_turn`), `app/web/frontend/src/pages/Settings.tsx`, `app/web/frontend/src/components/Composer.tsx`, `app/web/frontend/src/components/ModelPicker.tsx`, `README.md` section 3 in this folder.

**Build:**
1. Option `worker_model_id` in `model_catalog.validate_options` (string entry id or `None`), stored in the chat's options like `budget_eur`. Default from env `LDRAW_NOVA_WORKER_MODEL_ID`, then none.
2. In `start_turn`, resolve the worker entry and check that it exists, that tool calling is not `False` (`llm_config.capabilities`), and that its pricing is known when a budget is set. Raise a `ValueError` with a plain message otherwise, like the planner checks do.
3. Worker effort: the lowest entry of `entry_profile(entry)["efforts"]` for the worker model.
4. Frontend: a worker model select in the chat box next to the budget field ("No workers" first), and a default in Settings. Reuse `ModelPicker.tsx`.
5. Show the worker model's per-million prices from the catalogue next to the picker, so a wrong catalogue price (Haiku 4.5 at $1 / $5 against the brief's $0.1 / $0.5) is visible.

**Acceptance:**
- No `worker_model_id`: no `delegate` tool, no UI change besides the empty picker, existing chats unchanged.
- An entry without tool support, or with unknown pricing while a budget is set, is refused with a readable message.
- The option survives a page reload and a resumed chat.

**Tests:** extend `tests/test_api.py` or `tests/test_builder_settings.py` in their style; `ruff check`.

**Out of scope:** automatic model choice by task, a worker pool, provider onboarding.
