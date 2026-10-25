# The database (PostgreSQL)

Since phase 1 of [the PostgreSQL plan](../plans/active/postgres/PROPOSAL.md) the app has a PostgreSQL 18 database next to the files. **It holds the request log and the cost history, and, since phase 2, the chats, builds, drafts and proposals** (once they were imported, see "Storage: database or files" below). The parts collection moved in phase 2b (see "The parts collection" below). Model files, editor state and agent work folders are files either way.

## What runs

| Piece | Where | Notes |
|---|---|---|
| `db-secrets` (one-shot) | `app/docker-compose.yml` | Generates two passwords on the first start into the volume `ldraw-nova-docker_secrets` (root-owned files, mode 0440, group 70). Nothing to set up by hand. |
| `db` | same, container `ldraw-nova-db` | `postgres:18-alpine` pinned by digest, non-root, `cap_drop: ALL`, `no-new-privileges`, read-only root with tmpfs, 512 MB, healthcheck. **No published port**; it sits on the internal network `db-net` that only the app joins. Data in the volume `ldraw-nova-docker_db`. |
| App | `ldraw-nova-app` | Waits for `db` to be healthy, then runs `alembic upgrade head` (under a PostgreSQL advisory lock) and prunes the request log. If the database cannot be reached the app still starts: Settings → Environment shows "Database: unavailable" and the logs pause. |
| Roles | created by `app/db/initdb/10-roles.sh` | `obd_app` (owns the database, not a superuser) is what the app uses. `obd_admin` (superuser) works only through the container's local socket (`docker exec`); its network logins are rejected. |

Dev mode (`docker-compose.dev.yml`) also publishes the database on `127.0.0.1:5433`.

## Storage: database or files

Chats, messages, worker transcripts, published-model records, builds, versions, drafts and proposals have two interchangeable backends behind the same code (`store.py` `ChatStore` = files, `pg_store.py` `PgChatStore` = PostgreSQL; `pg_builds.py` for build, draft and proposal rows). `storage.py` chooses once per start:

| `LDRAW_NOVA_STORAGE` | Result |
|---|---|
| `auto` (default) | PostgreSQL when the database is reachable, migrated to the newest revision **and** the files hold nothing that is not in it yet; the JSON files otherwise |
| `files` | Always the JSON files (a db-less install, or the way back) |
| `postgres` | PostgreSQL even if the files hold data nobody imported; falls back to the files, with an error in the log, when the database is not usable |

"Nothing that is not in it yet" is the safety net of `auto`: an install with chats or builds on disk (a `chat.json` or `build.json`) and no recorded import keeps working on the files (the log says so) instead of showing an empty list. After the import (`app_setting['json_import'].done`), or on a fresh volume, the database is used. Settings > Environment (`GET /api/environment`) reports `storage: {backend, mode, json_import}`.

What lives where in PostgreSQL mode:

- `chat` (title, model, options, `build_id`, the message counter), `message` (the **whole message record** as jsonb `body`: `_usage`, `_worker`, `_fast_path`, ... exactly as in `messages.jsonl`; the id comes from the chat row under a row lock, so two writers never share one), `worker_step` (a delegated worker's transcript), `model_record` (a chat's published models **and** a build's versions, with the file path under `/data`, `sha256` and part count).
- `build` (title, source, dates; the chat list is `chat.build_id`), `build_draft` (the draft's columns; `forked`, `drift`, `sources` and any other key in `extra`), `proposal` (the row; the `.mpd` stays a file in `builds/<slug>/proposals/`).
- Versions are still **the model files in `generated/`** (what exists is what is on disk). `model_record` adds what files cannot say (sha256, parts, a recorded origin); a version file with no record is adopted at start for builds that have a row (`builds.reconcile`, cheap, origin left NULL = still derived from the chats) and stays derived otherwise.
- Cost events: unchanged. `usage_log.record` writes a `usage_event` next to each step with the chat and message id; the store does not write a second one. Deleting a chat deletes its messages, steps and model records; its spend and its model files stay. Deleting a build deletes its versions' records, draft and proposals and detaches its chats.
- Not stored: API keys (`/config`), the chat's folder (renders, `workspace/`, editor `edit.json`), work folders. A build's workspace key `b-<slug>` is not a chat: its messages and worker files stay files.
- Known harmless differences from the files: JSON object keys come back in jsonb order, times in chat and model records have microsecond precision, and a chat that was never attached to a build has no `build_id` key (the file store left `"build_id": null` after a detach). On the owner's real data, 30 read routes (chats, chat detail, builds, build detail, workspace, proposals, models, a worker transcript) return identical JSON from both backends apart from that one key.

## Importing the JSON files

`app/web/backend/migrate_json.py` reads the files in `/data` and writes the tables. The JSON files are only read: they stay as a frozen backup (rollback = run the previous image on the same volume, or `--export` first to keep what was done since). The parts collection (`inventories/`) has its own importer (next section).

```
docker exec -w /app/web/backend ldraw-nova-app python3 -m migrate_json              # dry run: rolled back, prints counts and anomalies
docker exec -w /app/web/backend ldraw-nova-app python3 -m migrate_json --commit     # dry run, import, then verify; sets app_setting json_import
docker exec -w /app/web/backend ldraw-nova-app python3 -m migrate_json --verify     # files against database (exit code 1 on a mismatch)
docker exec -w /app/web/backend ldraw-nova-app python3 -m migrate_json --export /data/export-json [--force]
```

Take a backup first (`bash scripts/backup.sh`), then restart the app so `auto` picks the database (`docker compose restart ldraw-nova-app`), or set `LDRAW_NOVA_STORAGE=postgres`.

- **Idempotent**: inserts are `ON CONFLICT DO NOTHING` on the natural key (chat + message id, build + version, ...), one transaction per build and per chat; a crash leaves whole entities. A second run adds nothing. A file that changed after it was imported is reported (`changed: ...`), never copied over the rows; new messages in it are added.
- **Anomalies are reported, not guessed**: a truncated `messages.jsonl` line, a chat whose build does not exist (imported unattached), a version recorded twice (the later record wins), unknown keys, unknown origins.
- **Cost events**: one `usage_event` per message that carries `_usage`, unless the live recorder already wrote one for that chat and message (or worker): history is imported once and live rows are never doubled. The exchange rate stored is today's.
- **Verify** (also after `--commit`): per chat the fields, the message count and a canonical sha256 of all messages, the model records and the sha256 of each model file, worker steps and the cost sum against `usage_event`; per build the record, versions, draft and proposals. Records that point at a deleted model file are counted (`model_files_missing`), not failed: a chat keeps them as history. Rows that exist only in the database (made after the import) are fine.
- Every run is a row in `import_run` (counts, anomalies, per-entity file size and mtime); a successful commit + verify writes `app_setting['json_import']` and a report to `/data/_migration/report-<time>.json`.
- **First start**: `LDRAW_NOVA_DB_IMPORT=auto` runs dry run, commit and verify by itself when the files hold chats or builds and no import is recorded. The default is `manual` (never).
- **Export** (`--export DIR`): the inverse mapping, writing the same JSON layout (`chats/<id>/chat.json`, `messages.jsonl`, `models.jsonl`, `workers/`; `builds/<slug>/build.json`, `models.jsonl`, `draft.json`, `proposals/`) from the database; an existing file is never overwritten without `--force`.

## The parts collection

`collection_store.py` is the only module the collection feature uses for its data; it picks a backend once per start: `collection_files.py` (the JSON files in `/data/inventories/`) or `collection_pg.py` (PostgreSQL). PostgreSQL is used when `storage.py` says so **and** the collection is in it: `LDRAW_NOVA_STORAGE=postgres` always; `auto` once the collection import below was committed and verified (`app_setting['collection_import'].done`), or when there are no source files to import. Until then `auto` keeps the collection on its files, so cutting over is explicit.

Tables (Alembic revision `0003` completes the reference schema): `part_source` (metadata columns, `csv_text` = the original upload, `extra` = any other top-level key), `part_row` (exact rows, with their order and extra keys), `part_review` (the "maybe" pieces), `part_log` (the change log: one row per log line, the whole entry as jsonb; replaces the reference `part_change`, whose uuid batches and fixed ops did not fit the real entries and which was never written), `part_note` + `part_note_log`, `part_reservation` (reservations; built sets are `part_source.set_info->state`), remembered CSV mappings in `app_setting['collection_import_mappings']`. A source dict loads back exactly as the file had it (keys come in jsonb order, `kind` is always present, `created_at` has microsecond precision).

**The toolkit CLI never reads the database.** In PostgreSQL mode every commit also updates a derived JSON copy in `COLLECTION_EXPORT_DIR` (default `/data/collection-export/`: `<id>.json` per source in the file format, `.collection-reservations.json`, `.collection-notes.json`), and the whole copy is regenerated at start (stale files removed, unchanged files not rewritten). `collection_store.cli_store_args()` / `cli_environment()` / `scratch_dir()` point `inventory ...`, `buildable` and the import preview at it, so the agent sandbox sees the same data without database access. The original `inventories/` files are never touched in this mode: they stay as the frozen backup.

Importer (`migrate_collection.py`, also `python -m migrate_json --collection ...`; same modes as `migrate_json`): `--dry-run` (default, rolled back), `--commit` (import one transaction per source, verify, mark done, write the derived copy), `--verify`, `--export DIR` (the inverse, never overwriting without `--force`). It imports sources with rows, review pieces, uploads and change logs, the notes and their log, reservations and CSV mappings; idempotent (an existing source is kept, a differing file is reported); verify compares per source row count, piece total, review items, upload and log length, then notes and reservations, and tolerates sources edited in the app since (`edited_since_import`). A repeated part+colour row in a file is added up and reported; a source whose name equals another's (case-insensitive) is skipped and reported.

```
docker exec -w /app/web/backend ldraw-nova-app python3 -m migrate_json --collection              # dry run
docker exec -w /app/web/backend ldraw-nova-app python3 -m migrate_json --collection --commit     # import, verify, mark done, derived copy
docker exec -w /app/web/backend ldraw-nova-app python3 -m migrate_json --collection --verify
```

Then restart the app (`docker compose restart ldraw-nova-app`) so the collection switches.

## The password never reaches agent code

- It is a file (`/run/secrets/db_password`, root:70 0440), read by the server only. It is in no environment variable, so not in `docker inspect`, `/api/environment` or the agent's scrubbed environment (`sandbox.py`).
- `entrypoint.sh` rejects TCP from the `agent` user to port 5432, to any destination, next to the existing app-port rule. Settings → "Sandbox isolation: on" covers both rules.
- The database is on an internal network; nothing outside the app container can reach it.

## Code

`app/web/backend/db/` (engine and session from the password file, `models.py` with every table of [sql/0001_init.sql](../plans/active/postgres/sql/0001_init.sql) plus revision 0002, `migrate.py`), `app/web/backend/migrations/` (Alembic, see its README; `0002` adds `build_draft.extra` and drops the foreign key of `proposal.chat_id`, because a proposal file kept its chat id after the chat was deleted), `storage.py` (the switch), `pg_store.py`, `pg_builds.py`, `migrate_json.py`, `usage_log.py` (cost history, `GET /api/costs`), `request_log.py` (describe/estimate telemetry).

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

`LDRAW_NOVA_TEST_STORAGE=postgres` (with the same database URL) runs the **whole** suite on the PostgreSQL store: every test gets an empty database and `get_store()` / the `store` fixture are `PgChatStore`; tests that read the JSON files themselves carry `@pytest.mark.files_only` and skip. `tests/test_pg_store.py` (the same operations on both stores give the same results, the switch, numbering and versions under concurrency, cascades) and `tests/test_migrate_json.py` (importer: dry run, commit, verify, idempotence, tampering, export round trip, first start) always use the database.
