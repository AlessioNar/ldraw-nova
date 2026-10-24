# The database (PostgreSQL)

Since phase 1 of [the PostgreSQL plan](../plans/postgres/PROPOSAL.md) the app has a PostgreSQL 18 database next to the files. **Today it holds only two things: the request log and the cost history.** Chats, builds and the parts collection are still files; they move in later phases (the tables for them already exist and are empty).

## What runs

| Piece | Where | Notes |
|---|---|---|
| `db-secrets` (one-shot) | `app/docker-compose.yml` | Generates two passwords on the first start into the volume `ldraw-nova-docker_secrets` (root-owned files, mode 0440, group 70). Nothing to set up by hand. |
| `db` | same, container `ldraw-nova-db` | `postgres:18-alpine` pinned by digest, non-root, `cap_drop: ALL`, `no-new-privileges`, read-only root with tmpfs, 512 MB, healthcheck. **No published port**; it sits on the internal network `db-net` that only the app joins. Data in the volume `ldraw-nova-docker_db`. |
| App | `ldraw-nova-app` | Waits for `db` to be healthy, then runs `alembic upgrade head` (under a PostgreSQL advisory lock) and prunes the request log. If the database cannot be reached the app still starts: Settings → Environment shows "Database: unavailable" and the logs pause. |
| Roles | created by `app/db/initdb/10-roles.sh` | `obd_app` (owns the database, not a superuser) is what the app uses. `obd_admin` (superuser) works only through the container's local socket (`docker exec`); its network logins are rejected. |

Dev mode (`docker-compose.dev.yml`) also publishes the database on `127.0.0.1:5433`.

## The password never reaches agent code

- It is a file (`/run/secrets/db_password`, root:70 0440), read by the server only. It is in no environment variable, so not in `docker inspect`, `/api/environment` or the agent's scrubbed environment (`sandbox.py`).
- `entrypoint.sh` rejects TCP from the `agent` user to port 5432, to any destination, next to the existing app-port rule. Settings → "Sandbox isolation: on" covers both rules.
- The database is on an internal network; nothing outside the app container can reach it.

## Code

`app/web/backend/db/` (engine and session from the password file, `models.py` with every table of [sql/0001_init.sql](../plans/postgres/sql/0001_init.sql), `migrate.py`), `app/web/backend/migrations/` (Alembic, see its README), `usage_log.py` (cost history, `GET /api/costs`), `request_log.py` (describe/estimate telemetry).

- **Request log**: one row per `/api/describe` and `/api/describe/estimate` request (route, who decided, generator, timing, a 12-character hash of the text). The sentence is stored only if `keep_request_text` is on (`PUT /api/request-log/settings`, default off). Rows older than 180 days are deleted at start and daily.
- **Cost history**: one `usage_event` per model call (planner steps, workers, the understanding call), kept forever, no link to chats (deleting a chat keeps the spend). The per-chat `_usage` in messages is unchanged. `GET /api/costs?from=2026-10-01&to=2026-10-31` returns the `cost_daily` view (per day and kind) with totals.

## Backup and restore

A full backup is **the database dump plus the data volume** (model files stay files). API keys are in neither (they are in `/config`).

```
bash scripts/backup.sh                          # ./backups/obd-<stamp>.dump + obd-data-<stamp>.tgz
BACKUP_DIR=/mnt/nas/obd bash scripts/backup.sh  # elsewhere
bash scripts/restore.sh backups/obd-<stamp>.dump          # asks first; stops the app, restores, starts it again
bash scripts/restore.sh -y backups/obd-<stamp>.dump none  # database only, no question
```

- Safe while the app runs (`pg_dump` takes a snapshot). On Windows run it from Git Bash or WSL.
- `restore.sh` replaces the database, and unpacks the data archive over `/data` (same names are overwritten, other files stay).
- **Scheduled backups are off by default.** Turn them on with `docker compose --profile backup up -d`: a dump a day (7 kept) and one on Sundays (4 kept) land in the volume `ldraw-nova-docker_backups`. A backup on the same disk does not survive a lost disk: copy them out, e.g.
  `docker run --rm -v ldraw-nova-docker_backups:/b:ro -v "$PWD/backups":/o postgres:18-alpine cp -r /b/. /o/`
  Restore one with `scripts/restore.sh` after copying it out.
- **`docker compose down -v` also deletes the database.** Back up first.
- Upgrading the image digest within 18: `docker compose pull db && docker compose up -d db`. A new major version: dump, new volume, restore.

## Tests

Database tests skip unless `LDRAW_NOVA_TEST_DATABASE_URL` points at a throwaway server (a superuser on database `postgres`); `conftest.py` builds one template database with Alembic per session and truncates between tests. Example (from the repo root, with the image built as `ldraw-nova-app`):

```
docker network create obd-pg-net
docker run -d --rm --name obd-pg-testdb --network obd-pg-net --tmpfs /var/lib/postgresql -e POSTGRES_PASSWORD=test postgres:18-alpine
docker run --rm --name obd-pg-tests --network obd-pg-net --cap-drop ALL --cap-add CHOWN --cap-add SETUID --cap-add SETGID \
  --cap-add DAC_OVERRIDE --cap-add FOWNER --cap-add KILL --cap-add SETPCAP --cap-add NET_ADMIN \
  -e LDRAW_NOVA_TEST_DATABASE_URL=postgresql://postgres:test@obd-pg-testdb:5432/postgres ldraw-nova-app \
  bash -c 'pip install -q --break-system-packages -r /app/web/backend/requirements-dev.txt && cd /app/web/backend && pytest -q tests/test_database.py tests/test_database_isolation.py'
```

With `NET_ADMIN` the entrypoint applies the firewall rule, so the isolation test can check that the agent user is refused.
