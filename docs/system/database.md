# The database (PostgreSQL)

The app has a PostgreSQL 18 database that runs in its own container next to the app. It holds the chats and their messages, the builds with their drafts and proposals, the parts collection, the request log and the cost history. Model files, editor state and the AI's work folders are not in it; those stay files (see [data-and-storage.md](data-and-storage.md)).

Everything the database holds also has a JSON-file form, because the app started on files and moved over in phases. At start the app decides once whether to use the database or the files, and it never mixes the two for the same data. The JSON files are never deleted by the move: they stay as a frozen backup and as the way back.

## How it works

### What runs

| Piece | Where | Notes |
|---|---|---|
| `db-secrets` (one-shot) | `app/docker-compose.yml` | On the first start it writes two random passwords into the `secrets` volume (root-owned, mode 0440). Nothing to set up by hand |
| `db` | same file, container `ldraw-nova-db` | `postgres:18-alpine` pinned by digest, non-root user, all capabilities dropped, read-only root filesystem, 512 MB memory limit, health check. **No published port**: it sits on the internal network `db-net`, which only the app joins. Data in the volume `ldraw-nova-docker_db` |
| App | `ldraw-nova-app` | Waits for `db` to be healthy, then upgrades the schema (`alembic upgrade head`, under a database lock) and prunes the request log |
| Roles | created by `app/db/initdb/10-roles.sh` | `obd_app` owns the database and is what the app uses; it is not a superuser. `obd_admin` is a superuser reachable only through the container's local socket (`docker exec`); its network logins are rejected in `pg_hba.conf` |
| `db-backup` | same file, profile `backup` | Optional scheduled dumps (see Backup and restore) |

The development override `app/docker-compose.dev.yml` also publishes the database on `127.0.0.1:5433`.

If the database cannot be reached at start the app still starts, on JSON files; Settings shows "Database: unavailable" (`GET /api/environment`, from `db.status()`).

### The storage switch

`app/web/backend/storage.py` decides once per start, and the answer is read through `store.get_store()` (chats, builds, drafts, proposals) and `collection_store.use_database()` (the parts collection).

| `LDRAW_NOVA_STORAGE` | Result |
|---|---|
| `auto` (default) | PostgreSQL when the database answers and its schema is at the newest Alembic revision, **and** the files hold nothing that was not imported yet. Otherwise the JSON files |
| `files` | Always the JSON files (a database-less install, or the way back) |
| `postgres` | PostgreSQL even if files exist that nobody imported. If the database is unusable it falls back to the files and logs an error |

"Files that were not imported yet" means a `chat.json` or `build.json` exists on disk and `app_setting['json_import']` does not say `done` (`storage.json_state_present()` and `storage.import_done()`). This is the safety net of `auto`: an install that still has chats on disk keeps showing them instead of an empty list, and the log says why. The parts collection has its own gate: PostgreSQL is used when `storage` says so and either mode is `postgres`, or `app_setting['collection_import']` says `done`, or there are no collection files to import.

`GET /api/environment` reports `storage: {backend, mode, json_import}` so the owner can see which side is live.

```mermaid
flowchart TD
  A[start] --> B{LDRAW_NOVA_STORAGE=files?}
  B -->|yes| F[JSON files]
  B -->|no| C{database reachable and schema at head?}
  C -->|no| F
  C -->|yes| D{mode=postgres?}
  D -->|yes| P[PostgreSQL]
  D -->|no, auto| E{files on disk not yet imported?}
  E -->|yes| F
  E -->|no| P
```

### What is in the database

The schema is defined in `app/web/backend/db/models.py` (SQLAlchemy) and created by the Alembic revisions in `app/web/backend/migrations/versions/`. There are 18 tables and one view, plus Alembic's own `alembic_version`.

| Table | What it holds |
|---|---|
| `app_setting` | Small non-secret settings as key and JSON value: `json_import` and `collection_import` (import done markers), `collection_import_mappings` (remembered CSV column mappings), `keep_request_text` |
| `build` | A model project: id (a slug), title, source (generator, ai, import, editor or empty), dates |
| `chat` | A chat: title, model, options, the build it is attached to, a message counter |
| `message` | One chat message. The whole record is kept as JSON in `body` (including `_usage`, `_worker`, `_fast_path`), the same as a line of `messages.jsonl`. The id comes from the chat row under a row lock |
| `worker_step` | A delegated worker's transcript lines |
| `model_record` | A chat's published models and a build's versions: file path under `/data`, SHA-256, part count, warnings, origin. The file itself stays on disk |
| `build_draft` | A build's open editor draft: base version, revisions, and an `extra` JSON column for the remaining draft keys |
| `proposal` | An AI proposal for a build: summary, cost, warnings. The model file stays on disk. `chat_id` has no foreign key, because the proposal outlives its chat |
| `part_source` | One source of parts (an imported list, a set, or loose parts): metadata, the original upload as `csv_text`, totals |
| `part_row` | Exact parts of a source: LDraw part number, LDraw colour code, quantity, position |
| `part_review` | Approximate items (colour families) that are never counted as exact |
| `part_note` | Free-text note on a part ("blue tub") |
| `part_log` | A source's change history, one row per log line, grouped in batches (used by undo) |
| `part_note_log` | Change history of the notes |
| `part_reservation` | Parts held by models the owner built |
| `usage_event` | One row per LLM call (planner step, worker step, understanding call): tokens, cost. Kept forever, no foreign keys, so deleting a chat keeps the spend |
| `request_log` | One row per describe or estimate request: which route decided, generator, timing, a 12-character hash of the text. The sentence is stored only if `keep_request_text` is on. Rows older than 180 days are deleted |
| `import_run` | One row per run of the JSON importer, with its report |
| view `cost_daily` | `usage_event` summed per day and kind |

Rules worth knowing:

- Versions are still **the model files in `generated/`**; a database record adds what a file cannot say (hash, part count, origin). At start, `builds.reconcile()` adds a record for any version file of a recorded build that has none.
- Deleting a chat deletes its messages, steps and model records; its spend and its model files stay. Deleting a build deletes its version records, draft and proposals and detaches its chats.
- Not stored in the database: API keys and endpoints (`/config`), chat folders (renders, `workspace/`), editor state, work folders.
- Known harmless differences from the files: JSON keys come back in the database's order, times have microsecond precision, and a chat that was never attached to a build has no `build_id` key.
- **The toolkit command line never reads the database.** With the collection on PostgreSQL, every commit also writes a derived JSON copy into `/data/collection-export/`, regenerated completely at start. `collection_store.cli_store_args()` and `cli_environment()` point the toolkit and the AI's sandbox at that copy, so agent code gets the data without database access.

### Revisions so far

| Revision | Adds |
|---|---|
| `0001_initial_schema` | The whole reference schema from `docs/plans/active/postgres/sql/0001_init.sql`, plus the `cost_daily` view |
| `0002_phase2_draft_extra_proposal_chat` | `build_draft.extra`; drops the foreign key of `proposal.chat_id` |
| `0003_parts_collection_storage` | Completes the collection tables: `csv_text`, `extra`, `pos` columns, new `part_log`, `part_note_log`, `part_reservation`; drops the unused `part_change` |

### Importers

Two one-way importers read the JSON files and write the tables. They only read the files, they are safe to run twice (inserts skip rows that exist), and each run is checked afterwards by a verify step that compares files and database.

```
docker exec -w /app/web/backend ldraw-nova-app python3 -m migrate_json              # dry run: rolled back, prints counts and anomalies
docker exec -w /app/web/backend ldraw-nova-app python3 -m migrate_json --commit     # import, then verify; records json_import
docker exec -w /app/web/backend ldraw-nova-app python3 -m migrate_json --verify     # files against database (exit 1 on a mismatch)
docker exec -w /app/web/backend ldraw-nova-app python3 -m migrate_json --export /data/export-json [--force]
```

The same four modes exist for the parts collection by adding `--collection` (this runs `migrate_collection.py`): `python3 -m migrate_json --collection --commit`. Options: `--data-dir`, `--json` (report as JSON), and for the collection `--inventories-dir`.

- `migrate_json.py` imports chats, messages, worker transcripts, models, builds, versions, drafts and proposals. It creates a `usage_event` per message that carries `_usage` unless the live recorder already wrote one, so history is imported once. Anomalies (a cut-off `messages.jsonl` line, a chat whose build is gone) are reported, not guessed. A successful commit and verify writes `app_setting['json_import']` and a report to `/data/_migration/`.
- `migrate_collection.py` imports sources with rows, review items, uploads and change logs, notes, reservations and the CSV mappings, then writes the derived JSON copy and `app_setting['collection_import']`. A repeated part and colour in a file is added up and reported; a source whose name equals another's (ignoring case) is skipped and reported.
- `--export DIR` is the inverse: it writes the same JSON layout out of the database, and refuses to overwrite a file without `--force`. Use it before going back to files if you worked on PostgreSQL for a while.
- `LDRAW_NOVA_DB_IMPORT=auto` makes the app run the chats and builds importer by itself at the first start when files exist and no import is recorded (`migrate_json.first_start()`, called from `main.py`). The default is `manual`. The collection import is always started by hand.
- After an import, restart the app (`docker compose restart ldraw-nova-app`) so `auto` picks the database, or set `LDRAW_NOVA_STORAGE=postgres`.

### Cutover state

The plan is [PROPOSAL.md](../plans/active/postgres/PROPOSAL.md); the record of what was done is [CUTOVER.md](../plans/active/postgres/CUTOVER.md). In short, as of that record (2026-10-25):

- The owner's chats and builds were imported and verified (zero mismatches), the app was restarted onto PostgreSQL, and a restore drill from a backup matched row for row. The JSON files were left untouched as the rollback.
- The code still has both backends: `store.ChatStore` (files) and `pg_store.PgChatStore`, and `collection_files.py` next to `collection_pg.py`. **Removing the JSON write paths is deliberately not done yet.** CUTOVER.md lists the conditions: two weeks on PostgreSQL without a rollback, a scheduled backup running, a second restore drill, and the parts collection on PostgreSQL. It would be its own change, keeping the importer and `--export`.
- Whether the owner's parts collection has been imported is not visible from the code. Check Settings (the `storage` block) and `app_setting['collection_import']`.

Rollback while the files are untouched: stop the app, set `LDRAW_NOVA_STORAGE=files`, start it. Whatever was done since the cutover exists only in PostgreSQL, so run `python3 -m migrate_json --export /data` first to write it back out as JSON.

### Security of the database

- The password is a **file** (`/run/secrets/db_password`, readable by root and group 70), read only by the server. It is in no environment variable, so it does not show in `docker inspect`, `/api/environment` or the AI's scrubbed environment (`sandbox.py`). `db/engine.py` builds the connection from that file.
- `app/entrypoint.sh` adds a firewall rule that rejects TCP from the `agent` user to the database port (and to the app's own port). Settings shows "Sandbox isolation: on" when both rules are active.
- The database is on an internal Docker network with no route to the internet, and nothing outside the app container can reach it.
- The app role cannot create roles or databases; the superuser role cannot log in over the network.
- Connection limits: pool of 5 (plus 3 overflow), statement timeout 15 s, idle-in-transaction timeout 30 s (`db/engine.py`). A failed log-style write (`db.best_effort()`) pauses database writes for 30 seconds and never fails the request.

### Backup and restore

A full backup is **the database dump plus the data volume**, because model files are not in the database. API keys (`/config`) are in neither.

```
bash scripts/backup.sh                          # ./backups/obd-<stamp>.dump + obd-data-<stamp>.tgz
BACKUP_DIR=/mnt/nas/obd bash scripts/backup.sh  # somewhere else
bash scripts/restore.sh backups/obd-<stamp>.dump          # asks first; stops the app, restores, starts it again
bash scripts/restore.sh -y backups/obd-<stamp>.dump none  # database only, no question
```

- `backup.sh` is safe while the app runs (`pg_dump` takes a snapshot). The tarball covers `generated`, `builds`, `inventories` and `chats` only, not `templates/`, `output/` or `/config`. On Windows run the scripts from Git Bash or WSL.
- `restore.sh` **replaces** the database and unpacks the archive over `/data` (same names are overwritten, other files stay). Both scripts read `DB_CONTAINER`, `DATA_VOLUME`, `DB_NAME` and similar from the environment (see their headers).
- **Scheduled backups are off by default.** Turn them on with `docker compose --profile backup up -d`: one dump a day (newest 7 kept) and one on Sundays (newest 4 kept) in the volume `ldraw-nova-docker_backups` (`app/db/backup-loop.sh`). A backup on the same disk does not survive a lost disk, so copy them out, for example `docker run --rm -v ldraw-nova-docker_backups:/b:ro -v "$PWD/backups":/o postgres:18-alpine cp -r /b/. /o/`.
- To check a backup, restore it into a throwaway server and compare row counts per table with the live database; the steps are in CUTOVER.md ("Restore drill").
- `docker compose down -v` also deletes the database volume.
- Upgrading the image digest within version 18: `docker compose pull db && docker compose up -d db`. A new major version needs dump, new volume, restore.

## Where the code is

| File | Purpose |
|---|---|
| `app/web/backend/db/engine.py` | Engine from the password file, `session_scope()`, `best_effort()`, `status()` |
| `app/web/backend/db/models.py` | Every table, as SQLAlchemy classes |
| `app/web/backend/db/migrate.py` | `upgrade()` and `upgrade_when_ready()` under an advisory lock; also `python -m db.migrate` |
| `app/web/backend/migrations/` | `env.py`, `versions/0001..0003`, `README.md`; `alembic.ini` is in the backend folder |
| `app/web/backend/storage.py` | `mode()`, `schema_ready()`, `use_database()`, `status()` |
| `app/web/backend/pg_store.py`, `pg_builds.py` | Chats, messages, models; builds, drafts, proposals |
| `app/web/backend/collection_pg.py`, `collection_store.py` | Parts collection in PostgreSQL and the switch |
| `app/web/backend/migrate_json.py`, `migrate_collection.py` | The importers, verify and export |
| `app/web/backend/usage_log.py`, `request_log.py` | Cost history and request log writes, pruning |
| `app/web/backend/routers/costs_api.py` | `GET /api/costs`, request-log setting |
| `app/db/initdb/10-roles.sh`, `app/db/backup-loop.sh` | Roles at first start; scheduled backups |
| `scripts/backup.sh`, `scripts/restore.sh` | Backup and restore |

## Changing it

### Adding a migration

Always from `app/web/backend`, against a throwaway database (never the real one):

1. Change the class in `db/models.py`.
2. Point at a scratch database and bring it to the current head, then generate a draft:
   ```
   export LDRAW_NOVA_DATABASE_URL=postgresql://postgres:test@localhost:5432/scratch
   python -m db.migrate
   alembic revision --autogenerate -m "what changed"
   ```
3. **Read and fix the generated file** in `migrations/versions/`. Autogenerate is only a draft. Check that it did not drop and recreate a column instead of renaming it, that data-moving steps are written as SQL, that constraint names follow the naming convention at the top of `models.py`, and that `downgrade()` undoes the change. Number it `0004_...` and keep `down_revision` pointing at the previous one.
4. Run `python -m db.migrate` again and the tests: `pytest tests/test_database.py -k "migrations_agree or reference"`.
5. A migration that rewrites data should say so in its docstring. The owner takes a backup first (`scripts/backup.sh`).

The app applies new revisions by itself at start. Views are not models, so a new view is written by hand in the revision as `op.execute(...)`, as `cost_daily` is. The reference SQL file `docs/plans/active/postgres/sql/0001_init.sql` is compared with revision `0001` only; do not edit it for later changes.

### Other changes

- **Store a new kind of record.** Add the table, then a method on both `ChatStore` and `PgChatStore` (or both collection backends) with the same result; `tests/test_pg_store.py` checks that both give the same answers.
- **Keep best-effort writes best-effort.** Telemetry goes through `db.best_effort()` so a stopped database never breaks a request.
- **Never put the password in an environment variable or the image.**

## Testing it

The database tests skip unless `LDRAW_NOVA_TEST_DATABASE_URL` points at a throwaway PostgreSQL server (a superuser on database `postgres`). `tests/conftest.py` builds a template database with Alembic once per session and truncates all tables between tests. From the repo root, with the app image built as `ldraw-nova-app`:

```
docker network create obd-pg-net
docker run -d --rm --name obd-pg-testdb --network obd-pg-net --tmpfs /var/lib/postgresql -e POSTGRES_PASSWORD=test postgres:18-alpine
docker run --rm --name obd-pg-tests --network obd-pg-net --cap-drop ALL --cap-add CHOWN --cap-add SETUID --cap-add SETGID \
  --cap-add DAC_OVERRIDE --cap-add FOWNER --cap-add KILL --cap-add SETPCAP --cap-add NET_ADMIN \
  -e LDRAW_NOVA_TEST_DATABASE_URL=postgresql://postgres:test@obd-pg-testdb:5432/postgres ldraw-nova-app \
  bash -c 'pip install -q --break-system-packages -r /app/web/backend/requirements-dev.txt && cd /app/web/backend && pytest -q tests/test_database.py tests/test_database_isolation.py'
```

With `NET_ADMIN` the entrypoint applies the firewall rule, so the isolation test can check that the agent user is refused.

| Test file | Covers |
|---|---|
| `tests/test_database.py` | Migrations (upgrade, downgrade, models agree with revisions, schema equals the reference SQL, concurrent starts), request log and its pruning, cost events and `/api/costs` |
| `tests/test_database_isolation.py` | The agent user cannot reach the database |
| `tests/test_pg_store.py` | Both stores give the same results; numbering and versions under concurrency; cascades |
| `tests/test_migrate_json.py` | Importer: dry run, commit, verify, idempotence, tampering, export round trip, first start |
| `tests/test_collection_pg.py` | The parts collection on PostgreSQL |

Setting `LDRAW_NOVA_TEST_STORAGE=postgres` (with the same URL) runs the **whole** backend suite on the PostgreSQL store. Tests that read the JSON files themselves are marked `files_only` and skip in that mode.

## Related

- [data-and-storage.md](data-and-storage.md): files, volumes and what stays outside the database
- [backend.md](backend.md): start-up order and configuration variables
- [parts-and-collection.md](parts-and-collection.md): what the collection tables are for
- [security.md](security.md): the sandbox and the firewall rule
- [operations.md](operations.md): running the containers
- [PostgreSQL proposal](../plans/active/postgres/PROPOSAL.md) and [cutover record](../plans/active/postgres/CUTOVER.md)
