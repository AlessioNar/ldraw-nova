# Data and storage

Chats, builds, models and parts lists are **files** on two Docker volumes, plus read-only data baked into the image. A PostgreSQL database (a third volume) holds only the request log and the cost history so far; see [database.md](database.md). More moves there in later phases.

## Volumes

| Volume (compose project `ldraw-nova-docker`) | Mounted at | Holds | Notes |
|---|---|---|---|
| `ldraw-nova-docker_data` | `/data` | Builds, versions, chats, parts lists, agent work folders | Writable by the agent user only in its work folders |
| `ldraw-nova-docker_config` | `/config` | `models.json` (LLM entries), `environment.json` (keys and env vars), `generators.json` (registry cache) | root-only (0700); agent code cannot read it |
| `ldraw-nova-docker_db` | `/var/lib/postgresql` in the `db` container | PostgreSQL data: request log, cost history | Not mounted in the app. Back up with `scripts/backup.sh` |
| `ldraw-nova-docker_secrets` | `/run/secrets` (read-only in the app) | The generated database passwords | root-owned, 0440 |

- Defined in `app/docker-compose.yml:20-25, 72-78`.
- They are named volumes, not bind mounts, because bind-mount file access is slow on Windows and macOS.
- **`docker compose down -v` deletes them** (and the database volumes). Back up first: `bash scripts/backup.sh` writes a database dump and an archive of the data volume.
- To copy models out: `docker cp ldraw-nova-app:/data/generated ./my-models`.

## Layout of `/data`

| Path | Contents | Served to the browser? |
|---|---|---|
| `/data/generated/` | Every saved model: `<slug>-vN.mpd` plus `.png` snapshot and `.csv` BOM. This is "My Builds". | yes |
| `/data/builds/<slug>/` | `build.json`, `models.jsonl` (version log), `draft.json`, `edit.json`, `proposals/`, `output/` (editor `plan.json`, `state.json`, `preview.mpd`) | via API |
| `/data/chats/<chat-id>/` | `chat.json`, `messages.jsonl` (with `_usage` per step), `models.jsonl`, `renders/`, `workers/<id>.jsonl` | yes |
| `/data/output/<chat-id>/` | The agent's work folder: scripts, plans, `NOTES.md`, renders | **no** (never served; reached only through the artifact API) |
| `/data/inventories/` | Parts lists: `<id>.json` and `<id>.csv` | via API |

- Paths come from `app/web/backend/settings.py:21-33`.
- A new empty volume is seeded with 2 showcase models.

## Baked into the image (read-only)

| What | Where in the image | Source in repo |
|---|---|---|
| LDraw parts library (release 2026-09, sha-checked) | `/opt/ldraw/ldraw`, served at `/ldraw/` | downloaded at build |
| Toolkit plus its own Python venv | `/opt/ldraw-nova` (`.venv`, with `output` symlinked to `/data/output`) | `ldraw_tools/`, `ldraw-agent` |
| 1,821 reference models | `/opt/ldraw-nova/data/models-annotated/` | `models-packed/`: 130 `.tar.xz` shards plus `manifest.json`; `tools/models_pack.py unpack` |
| Descriptions of parts, models and submodels | `data/ldraw-info.db` (SQLite, about 26 MB, opened read-only) | committed. **It cannot be regenerated from this repo** (`docs/plans/done/data-audit.md:36`) |
| Search caches | `<toolkit>/.cache/discovery/<hash>/catalog.sqlite` (FTS5), `.cache/buildable/` | built at image build or on first use |
| Sets catalogue (about 20k sets) | `ldraw_tools/data/set-catalog.bin` | `tools/build_set_catalog.py` from Rebrickable CSVs |
| Rebrickable mappings | `ldraw_tools/data/rebrickable-*.json` | `tools/build_rebrickable_maps.py` |
| Frontend build, viewer pages, wasm player | `/opt/web/static`, `/opt/web/viewer`, `/opt/web/player-vendor` | `app/web/...`, `app/vendor/ldraw-player-0.8.1.zip` |
| LeoCAD v25.09 (AppImage unpacked, no FUSE) | see `app/Dockerfile:65-84` | downloaded at build |

## Where SQLite appears

SQLite is used only as read-only reference data and as caches. It is never used for user data (that is PostgreSQL or files):

- `data/ldraw-info.db`: the `*_DESCRIPTIONS` tables, plus the `*_DESCRIPTIONS_JEV` views used by search;
- the discovery `catalog.sqlite` (FTS5);
- Jev query snapshots under `.cache/discovery/<hash>/queries/`.

## How writes stay safe

- **Atomic writes.** JSON records are written to a temp file and then renamed (`builds.py:102-107`, `llm_config`).
- **One writer at a time.** Editor requests are serialised per build, and renders are serialised because there is a single Xvfb display (`render.py`).
- **Content-addressed caches.** The build worker caches results by a content hash of the toolkit version, library, options and plan (`ldraw_tools/pipeline.py:28-52`).

## Renders

- **Display.** `entrypoint.sh` starts one `Xvfb :99`, and LeoCAD renders on it with software OpenGL.
- **Snapshots.** Missing snapshots and BOMs for My Builds are rendered in the background (`gallery.ensure_artifacts`).
- **Timing.** One view takes about 21 s, four views about 31 s, and a GLB export about 7.6 s (`docs/plans/done/deterministic-first/PROPOSAL.md:40`).
