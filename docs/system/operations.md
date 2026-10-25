# Operations: running and maintaining the app

How to run the app day to day: normal and dev mode, what to rebuild after a change, where the logs are, backups, and common tasks. For a first setup, start with [Getting started](getting-started.md).

## Normal and dev mode

| | Command | What runs |
|---|---|---|
| Production-style | `cd app && docker compose up -d --build` | The image as built: UI from the image, no reload |
| **Dev mode** | `cd app && docker compose up -d` **if `app/.env` sets `COMPOSE_FILE=docker-compose.yml,docker-compose.dev.yml`**  | The backend is mounted read-only from your checkout, `uvicorn --reload` (polling), and the UI comes from `app/web/frontend/dist` |

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
- **Understanding-call cost and every other model call**: the `usage_event` table (`GET /api/costs`), see [database.md](database.md).
- **Database**: `docker logs ldraw-nova-db`; a shell with `docker compose exec db psql -U obd_admin obd`.
- **Shell in**: `docker compose exec ldraw-nova-app bash`.

## Tests

Every suite, how to run it and what CI checks is on [Testing and CI](testing-and-ci.md). Test against throwaway containers, never the running `ldraw-nova-app` or its volumes.

## Backups

`bash scripts/backup.sh` (database dump plus data volume, into `./backups` or `$BACKUP_DIR`) and `bash scripts/restore.sh <dump>`; opt-in daily backups with `docker compose --profile backup up -d`. Details in [database.md](database.md).

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
