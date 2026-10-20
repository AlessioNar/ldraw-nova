# Data audit: heavy tracked directories

Read-only analysis on `swarm/data-audit`. Sizes are MiB from `git ls-tree -r -l HEAD` (tracked tree total 741 MiB; `.git` pack store about 307 MB). Removing files from HEAD does not shrink existing clones or `.git` unless history is rewritten (`git filter-repo --path ... --invert-paths`), which is a separate, owner-approved step.

Classes: REQUIRED (build, runtime or tests read it), REGENERABLE (derived; command given), EXPERIMENT (an agent run's output, no consumer).

## Summary

| Item | MiB | Class | Remove? | Risk |
|---|---:|---|---|---|
| `app/data/output` + `app/data/chats` (outside the stated targets, found while auditing) | 129 | EXPERIMENT | yes, plus gitignore | none |
| `examples/atlas-crane/{studies,work,research}` | 76 | EXPERIMENT (+ regenerable inputs) | yes | low |
| `examples/sakura-garden` bulk (iterations, proto, research, hero, `*-full.json`) | ~30 | EXPERIMENT / REGENERABLE | yes | low |
| `examples/copper-bean` review dirs and inspections | ~4 | REGENERABLE | optional | low |
| `data/ldraw-info.db-shm`, `-wal` | 0.03 | accidental | yes | none |
| `data/ldraw-info.db` | 25.6 | REQUIRED (runtime) | no; not rebuildable here | high |
| `data/models-annotated` | 387 | REQUIRED (runtime, image) | only the 8 files over 5 MB (141 MiB) as an option | medium |
| `data/categories` | 7 | REQUIRED (runtime) | no | high |
| `examples/{building,technic,reference,spaceship,vehicle,mechanism}-atlas`, `modular-street`, `technic-studies`, `bridge*`, `shadow-snap*` | ~45 | REQUIRED (tests, docs, `search_examples`) | no | high |
| `docs/reports/` | 0.86 total (largest file 0.45) | EXPERIMENT (write-ups) | not worth it | n/a |

## Findings per item

### data/models-annotated (387 MiB, 1821 files) REQUIRED
- Image: `app/Dockerfile:237` does `COPY --from=nova data/ /opt/ldraw-nova/data/`. The root `.dockerignore` does not exclude `data/`. So all 387 MiB are baked in.
- Code: `ldraw_tools/common.py:28` `models_path()` defaults to `data/models-annotated`. `ldraw_tools/resources.py` `search_models` scans it when the db is absent. `ldraw_tools/discovery.py` `DiscoveryIndex.ensure` opens every model named in the db to build the Jev inventory/sha (a missing file is recorded as an error row, not a crash). `prepare-glb.sh:5` uses it for `--model`.
- Docs the agent reads: `instructions.md` (lines 33, 93, 94), `prompts/ldraw-generative-tooling.md:10`, `docs/agent/{reference-discovery,tooling,complex-models,build-manuals,mechanisms}.md`.
- Tests: none read the real directory. `tests/test_resources.py` and `tests/test_discovery.py` use `tmp_path/'models-annotated'` or only assert the path. Removing or trimming it does not fail the suite. It is a runtime and product dependency only.
- Not regenerable in-repo: the headers carry descriptions added by the annotation step (for example line 2 of `10270-1.mpd`). No script in the repo produces them. The source models are OMR files, but the annotations would be lost.
- Compressibility: 8 files over 5 MB total 141 MiB (`8466-1` 34, `8448-1_Racer` 23, `8448-1_Wing-Doors` 20, `8448-1` 19.6, `8448-1_Concept-Car` 19.5, `8448-1_Hot-Rod` 10.7, `42083-1` 6.9, `7191-1` 5.8). Five are alternate builds of set 8448. 25 files over 2 MB total 188 MiB. Text compresses well (about 60 MB gzipped as a whole), so moving the directory to a release archive fetched at image build would cut the repo by most of 387 MiB.
- Candidate: untrack the 8 largest. They stay in the db (554 submodel rows for 8466/8448), so search would return hits with `source_exists:false` and Jev indexing would log errors for them. To remove them cleanly, also delete their rows from the db (see db below). Replacement for the image build: a `curl`/`ADD` of a pinned release asset into `/opt/ldraw-nova/data/models-annotated/` before the COPY.
- Risk: medium. Do not remove the whole directory without that replacement.

### data/ldraw-info.db (25.6 MiB) REQUIRED; `-shm` (32 KB) and `-wal` (0 B) accidental
- Consumers: `common.database_path()`; `search_models` (FTS5 tables `MODELS_DESCRIPTIONS_FTS`, `SUBMODELS_DESCRIPTIONS_FTS`); `DiscoveryIndex` (reads `*_DESCRIPTIONS_JEV`); `docs/agent/reference-discovery.md` and `instructions.md` pass `--db data/ldraw-info.db` to `jev-rerank`. `app/web/backend/tests/test_toolkit.py:36` only asserts the workspace path `data/ldraw-info.db` resolves to the toolkit file (it does not open it). Other tests build their own tiny db.
- Rebuildable by code in this repo: no. `grep` finds no `CREATE TABLE` for `PART_INFOS`, `MODELS_DESCRIPTIONS`, `*_JEV` etc. outside tests. It holds 35k part rows, 1821 model, 28k submodel descriptions and JEV descriptions, which come from an external pipeline (the db and `data/categories` are "supplied" per `ldraw_tools/catalog.py`). Treat as non-regenerable. It also has useless-but-small tables (`PARTS_BRICKLINK_CAT`, `PARTS_REBRICKABLE_CAT`, both 0 rows).
- `-wal`/`-shm`: SQLite side files from a connection that was open at commit time (added in `bfba9888`). The reader opens `mode=ro`, so they are never needed and are harmful if stale. Untrack them and add `data/*.db-wal`, `data/*.db-shm` to `.gitignore`. Zero risk. Before committing, confirm the db is not in WAL mode with unmerged pages: `sqlite3 data/ldraw-info.db 'PRAGMA wal_checkpoint(TRUNCATE)'` (the wal is 0 bytes, so nothing is lost).
- Shrink option (optional, 5-10 MiB likely): `VACUUM` after dropping the two empty tables; FTS tables duplicate the description text and could be rebuilt at first use, but that needs code changes. Not recommended now.

### data/categories (7.2 MiB) REQUIRED
`ldraw_tools/catalog.py:21` reads it (`LDRAW_CATEGORIES` or `data/categories`); `tests/test_resources.py:41` asserts `category_path() == data/categories`. `dimensions.py` (3.8 MiB) and `parts.lst` (1.9 MiB) are generated by py4bricks, not here. Keep.

### examples/* (about 150 MiB)
| Dir | MiB | Consumers | Class | Action |
|---|---:|---|---|---|
| atlas-crane | 76.5 | README.md lines 116-119 link only `atlas-crane.plan.json`, `generate.py`, `atlas-crane.mpd`. No test, code or doc reads `studies/`, `work/`, `research/`. `generate.py` reads only `assets/` (0.1 MiB, kept). Gallery copy `app/models-gallery/atlas-crane.mpd` is byte-identical to the example mpd. | studies 44.6 and work 29.5: EXPERIMENT (agent iteration logs, v4-v7 json, previews). research 1.8: EXPERIMENT | `git rm -r` studies, work, research |
| sakura-garden | 34.7 | No test, code or doc outside its own README. Gallery copy `app/models-gallery/sakura-garden.mpd` is byte-identical. README links `hero/`, `iterations/`, `proto/`, `research/`, `review/`, `modules/`. | iterations 10.6, proto 1.6, research 3.1: EXPERIMENT. `connectivity-full.json` 7.5 and `overlaps-full.json` 3.6: REGENERABLE (below). hero 5.4: REGENERABLE (`render_hero.py`) | remove bulk; keep README, generate.py, plan, mpd, bom, design-brief, visual-review, review/ (1.0) |
| building-atlas | 18.3 | `tests/test_architecture.py` (generate.py, catalog.json, model sha256, guides, previews); `search_examples` | REQUIRED (15.2 MiB are preview PNGs listed in catalog `preview`) | keep |
| technic-atlas | 9.3 | `tests/test_technic_atlas.py`, instructions, docs | REQUIRED | keep |
| reference-atlas | 8.2 | `tests/test_discovery.py:192` (model, plan, guide, preview for 20+ rows) | REQUIRED | keep |
| spaceship-atlas | 6.4 | `tests/test_spaceships.py`, `ldraw_tools/spaceships.py:64` | REQUIRED | keep |
| copper-bean | 4.9 | Only gallery copy (`app/models-gallery/copper-bean.mpd`, identical) and viewer comment. No test. | EXPERIMENT (review and inspection output) | optional: remove `*.inspection.json`, `*.full.json`, `review/`, `shop-review/`, `terrace-review/` (about 4.4) |
| mechanism-atlas, vehicle-atlas, cathedral (3.2, no consumer, gallery has copy), technic-studies (3.0, linked by 9 docs), turntable-frame (2.7, no consumer), modular-street (tests), stadium | 1-4 each | mixed | cathedral and turntable-frame are EXPERIMENT but small | leave for now |

Regeneration (sakura-garden), all per `examples/sakura-garden/README.md`: `ldraw-agent validate --geometry` and `check-model` write the `*-full.json` reports, deterministic for a fixed plan and library; `python examples/sakura-garden/render_hero.py` rebuilds `hero/` using LeoCAD (renders are not guaranteed bit-identical). The mpd itself is rebuilt from the plan with `ldraw-agent build examples/sakura-garden/sakura-garden.plan.json` (deterministic; the generator is `generate.py`). Exact flags are in that README; confirm them before relying on them.

### docs/reports (0.86 MiB total)
Not heavy. Largest is `jev-assembly-discovery-evidence.json` (0.45 MiB). Only `examples/technic-studies/README.md:117` links one file (`technic-knowledge-transfer.md`). All are EXPERIMENT write-ups. Saving is negligible; leave unless the owner wants a clean `docs/`.

### Outside the requested list: app/data (129 MiB)
`app/data/output` (81 MiB) and `app/data/chats` (48 MiB; one `messages.jsonl` is 25 MiB) are runtime agent chats and work folders. `app/.dockerignore` says `data/` is "bind-mounted at runtime, never baked in", and `docker-compose.yml` mounts `./data:/data`. Tests use a tmp dir (`app/web/backend/tests/conftest.py`). Nothing reads the committed copies. `app/data/generated` (1.4 MiB) is the user model collection; not needed by tests or the image either. Largest single saving with zero risk. `app/.gitignore` does not ignore `data/`, which is why they got committed.

## Proposed removal list (ordered, lowest risk first)

Run from the repo root. Each step is independent; run the test suite after steps 3 and 4 (`.venv/bin/python -m pytest tests app/web/backend/tests`).

```sh
# 1. Accidental sqlite side files (0.03 MiB, no risk)
git rm --cached data/ldraw-info.db-shm data/ldraw-info.db-wal
printf 'data/*.db-shm\ndata/*.db-wal\n' >> .gitignore

# 2. Runtime chats and agent work folders (~129 MiB, no consumer)
git rm -r -q --cached app/data/chats app/data/output
git rm -r -q app/data/chats app/data/output 2>/dev/null; mkdir -p app/data/chats && touch app/data/chats/.gitkeep && git add app/data/chats/.gitkeep
printf 'app/data/chats/*\n!app/data/chats/.gitkeep\napp/data/output/\n' >> app/.gitignore
# (optional) app/data/generated if it is only sample user output: git rm -r app/data/generated

# 3. atlas-crane experiment logs (~76 MiB); keeps plan, mpd, generate.py, crane_lib.py, assets/, tools/
git rm -r -q examples/atlas-crane/studies examples/atlas-crane/work examples/atlas-crane/research

# 4. sakura-garden iteration history and regenerable reports (~29 MiB)
git rm -r -q examples/sakura-garden/iterations examples/sakura-garden/proto examples/sakura-garden/research \
  examples/sakura-garden/hero examples/sakura-garden/connectivity-full.json examples/sakura-garden/overlaps-full.json
# README links to hero/, iterations/, proto/, research/ then break: trim lines 11, 23, 34, 35 of examples/sakura-garden/README.md

# 5. (optional) copper-bean review output (~4 MiB)
git rm -r -q examples/copper-bean/review examples/copper-bean/shop-review examples/copper-bean/terrace-review
git rm -q examples/copper-bean/*.inspection.json examples/copper-bean/*.full.json
```

Steps 1-4 save about 235 MiB (of 741) with no test or image-build dependency; step 5 about 4 MiB more. After step 2 the `.gitkeep` handling must keep `app/data/chats/.gitkeep` (the Dockerfile creates `/data/*` itself, so the placeholder only matters for the local bind mount).

### Not in the list, and what would replace them
- `data/models-annotated` whole (387 MiB): do NOT remove without publishing it as a release asset and adding an `ADD`/`curl` to `app/Dockerfile` before line 237 (and a `setup.sh` fetch for local use). With that replacement the repo drops another 387 MiB; image size is unchanged.
- The 8 largest models (141 MiB): only with the same replacement or after deleting their rows from `data/ldraw-info.db` (`DELETE FROM MODELS_DESCRIPTIONS WHERE model IN (...)` on the base, `_JEV`, and `_FTS` tables for models and submodels, then `VACUUM`). Documented as a follow-up, not recommended in this pass.
- `data/ldraw-info.db`, `data/categories`, the six tested atlases: keep. The db has no in-repo generator.
- History rewrite: needed to reduce clone size (307 MB pack). Run after the removals are merged, on a fresh clone: `git filter-repo --invert-paths --path app/data --path examples/atlas-crane/studies --path examples/atlas-crane/work --path examples/atlas-crane/research --path examples/sakura-garden/iterations`. This rewrites all commit ids, so every worktree and branch must be re-based.
