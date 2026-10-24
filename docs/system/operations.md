# Operations: running, changing, testing

## Normal and dev mode

| | Command | What runs |
|---|---|---|
| Production-style | `cd app && docker compose up -d --build` | The image as built: UI from the image, no reload |
| **Dev mode** | `cd app && docker compose up -d` **if `app/.env` sets `COMPOSE_FILE=docker-compose.yml,docker-compose.dev.yml`** (your `.env` does) | The backend is mounted read-only from your checkout, `uvicorn --reload` (polling), and the UI comes from `app/web/frontend/dist` |

Dev mode details (`app/docker-compose.dev.yml`):

- **Backend and viewer**: Python and viewer edits apply on save.
- **Frontend**: run `cd app/web/frontend && npm run build`, then reload the page. Or use `npm run dev` on :5173 for hot reload.
- **Toolkit**: `ldraw_tools/` is **baked into the image**, so toolkit changes need `docker compose up -d --build`. So do changes to `requirements.lock` or the Dockerfile.
- `.env` is gitignored and not in this worktree. It also holds key variables.

## Rebuild triggers

| You changed | Do |
|---|---|
| Backend Python, viewer JS | Nothing in dev mode (auto-reload) |
| React frontend | `npm run build` in `app/web/frontend` |
| `ldraw_tools/`, `models-packed/`, `requirements*.lock`, Dockerfile | `docker compose up -d --build` |
| New LDraw library release or LeoCAD bump | The build **fails on purpose**. Update `LDRAW_COMPLETE_SHA256` / `LEOCAD_SHA256` in `app/Dockerfile`. |

## Logs and where to look

- **Server log**: `docker logs -f ldraw-nova-app`. Python logging goes to stdout, at INFO level. There are no log files.
- **Startup line**: "Sandbox isolation: on (...)", or a WARNING if it is off.
- **Agent transcripts**: `/data/chats/<id>/messages.jsonl`, and the workers in `workers/<id>.jsonl`. Cost per step is the `_usage` field.
- **Agent files**: `/data/output/<chat-id>/`, including `NOTES.md` and the scripts in `generators/`.
- **Understanding-call cost**: only in the server log at INFO. It is not persisted.
- **Shell in**: `docker compose exec ldraw-nova-app bash`.

## Tests

| Suite | Where | How |
|---|---|---|
| Backend (about 55 files) | `app/web/backend/tests/` | In the container: `pip install -r requirements-dev.txt` then `cd /app/web/backend && pytest -q`. They need LeoCAD and the library; `conftest.py` redirects `/data` and `/config` to temp folders. |
| Toolkit (27 files) | `tests/` | CI mounts them into the image and runs `uv run pytest` (`.github/workflows/ci.yml`) |
| Viewer/editor in a browser | `app/web/viewer-deps/test/` (Playwright) | `npm test`, `npm run test:editor`, `test:canvas`, `test:core` against a **throwaway** container |
| Workspace end-to-end | `app/web/viewer-deps/test/workspace.e2e.mjs` | By hand, against a throwaway container seeded from a volume copy |
| Frontend type-check and build | `app/web/frontend` | `npm run build` (no unit tests) |
| Lint | repo root | `ruff check` |

- **CI jobs**: `quick` runs the frontend build, ruff and the models-pack verify. `image` runs the full build, smoke checks (including `jev-rerank --help`) and both test suites.
- **Rule of thumb from your memory notes**: test against throwaway containers, never the running `ldraw-nova-app` or its volumes. Live API tests have a small euro budget.

## Common tasks

| Task | How |
|---|---|
| Open from another device on the LAN | Set `LDRAW_NOVA_WEB_BIND=0.0.0.0` in `app/.env` and add the hostname to `LDRAW_NOVA_ALLOWED_HOSTS`. There is no login. |
| Change the port | `LDRAW_NOVA_WEB_PORT` (default 8765) |
| Add a reference model | `python3 tools/models_pack.py add path/to/12345-1.mpd`, then `git add models-packed`, then rebuild |
| Export all models | My Builds → "Download all (.zip)", or `docker cp ldraw-nova-app:/data/generated ./out` |
| Turn Jev on or off | Settings → Advanced → Environment: set or clear `TYPESAFE_API_KEY` / `JEV_OPENROUTER_API_KEY`. Remember that `OPENROUTER_API_KEY` also turns it on. |
| Tune the agent | Environment variables in `app/web/backend/settings.py`: `LDRAW_NOVA_MAX_STEPS` (80), `LDRAW_NOVA_CACHE_TTL` (1h), `LDRAW_NOVA_MAX_WORKERS` (3), `LDRAW_NOVA_EUR_PER_USD` (0.92) |
| Tune the build worker | `LDRAW_NOVA_WORKER_TIMEOUT` (300 s), `LDRAW_NOVA_WORKER_QUEUE` (4), `LDRAW_NOVA_RENDER_POOL` (2) |
| Run the toolkit by hand | `docker compose exec ldraw-nova-app bash`, then `cd /opt/ldraw-nova && ./ldraw-agent --help` |
