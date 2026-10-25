# PostgreSQL phase 3: retention, restore drill and cutover record

Status 2026-10-25: cutover and restore drill **done**; retention job checked and tested; removing the JSON write paths is **deliberately deferred** (see the last section). Part of [the proposal](PROPOSAL.md), issue #48.

## What happened on 2026-10-25

| Step | Result |
|---|---|
| Backup before the import | `obd-20261025-131956` (`scripts/backup.sh`: `pg_dump -Fc` plus the data volume tarball) |
| `migrate_json --commit` and `--verify` | builds 7, chats 9, messages 488, usage events 187, versions 5, drafts 3; 0 mismatches |
| Restart onto Postgres | `storage: postgres`, `json_import: done` |
| API comparison before and after | builds and models identical; chats identical apart from a missing `build_id: null` key; cost history now includes the imported pre-database costs |
| Restore drill | fresh backup `obd-20261025-132457` restored with `pg_restore` into a throwaway `postgres:18`; all 17 tables matched the live database row for row (for example chat 9, message 492, usage_event 194, build 7, request_log 36) |
| JSON files | untouched; they are the rollback |

## Assumed data volumes

The owner's database is tiny today (hundreds of rows). Retention and backup sizing assume a heavy single-owner install, an order of magnitude above current use:

| Table | Assumed rate | Size at steady state | Policy |
|---|---|---|---|
| `request_log` | 300 rows a day (one per describe or estimate call) | about 54,000 rows at 180 days, roughly 15 MB with indexes | pruned after 180 days |
| `usage_event` | 200 rows a day (every model call) | about 73,000 rows a year, roughly 15 MB a year | kept forever |
| `message` | 500 a day in a very busy chat period | grows with use; dominated by agent step payloads | kept until the chat is deleted |
| model files, renders | files, not rows | tens to hundreds of MB | data volume, backed up as a tarball |

A dump of this heavy case is a few tens of MB and takes seconds, so the 7 daily plus 4 weekly rotation of the `backup` profile costs well under 1 GB. Nothing here needs partitioning; one indexed `DELETE` a day is enough. If a deployment ever logs 100 times more, switch the prune to batches of 10,000 rows before partitioning.

## Retention job checks

`request_log.prune()` runs at app start and then every 24 hours (`main._prune_request_log_daily`). It deletes `request_log` rows whose `ts` is older than 180 days, using `request_log_ts_idx`. Checked by tests in `app/web/backend/tests/test_database.py`:

- the 180-day boundary (181 days goes, 179 stays) and a second run deletes nothing;
- a custom window (`prune(days=30)`) removes only old rows, leaves a cost row that a pruned log row pointed at, leaves `usage_event` (cost history is kept forever) and leaves the `keep_request_text` setting alone;
- a 60,000-row table (the heavy case above, spread over 240 days) prunes the exact expected count in one call;
- the daily job calls `prune` after each 24-hour sleep and **survives a prune that raises** (before this change one unexpected error ended the job until the next restart; it now logs a warning and tries again the next day);
- an unreachable database makes `prune` return 0 and never breaks the caller (existing test).

Not changed: the 180 days (owner decision 4 in the proposal) and "deleting a chat keeps its cost events".

## Restore drill (repeat before any risky change)

1. `bash scripts/backup.sh` against the running app.
2. Start a throwaway server: `docker run -d --rm --name obd-drill --tmpfs /var/lib/postgresql -e POSTGRES_PASSWORD=drill postgres:18-alpine`.
3. Copy the dump in and restore it: `docker cp backups/obd-<stamp>.dump obd-drill:/tmp/d.dump`, then `docker exec obd-drill sh -c 'createdb -U postgres obd && pg_restore -U postgres -d obd --no-owner /tmp/d.dump'`.
4. Compare row counts of every table with the live database (`SELECT relname, n_live_tup` is not exact; use `count(*)` per table on both sides). They must match, allowing for rows written between the dump and the count.
5. `docker rm -f obd-drill`.

A real restore is `bash scripts/restore.sh backups/obd-<stamp>.dump` (see [database.md](../../../system/database.md)).

## Rollback

While the JSON files are untouched: stop the app, set `LDRAW_NOVA_STORAGE=files` and start it again. Whatever was done after the cutover lives only in Postgres; run `python -m migrate_json --export /data` first to write it back out as JSON.

## Removing the JSON write paths (not done yet)

The cutover is only hours old, so the file write paths and `_groups_cache` stay. Remove them when all of these hold:

- at least two weeks on Postgres with no rollback needed and no `Database: unavailable` periods;
- a scheduled backup profile has been running and one more restore drill has passed after the cutover;
- the parts tables (#47) are on Postgres, so no feature still depends on the file store;
- `LDRAW_NOVA_STORAGE=files` has not been used in that time.

The change itself: delete the file-store write methods and `LDRAW_NOVA_STORAGE=files`, keep the read-only importer and `--export`, update README and `docs/system/data-and-storage.md`. Do it as its own PR so it can be reverted alone.
