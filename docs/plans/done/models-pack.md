# Shipping the reference models compressed

`data/models-annotated/` (1,821 `.mpd`, 388 MiB raw) was the bulk of every clone, checkout and image-build context. The repo now stores it as `models-packed/` (130 `.tar.xz` shards, 35.6 MiB, plus a 0.5 MB `manifest.json`) and everything that reads the models keeps its path: `data/models-annotated/*.mpd` is materialised by `tools/models_pack.py unpack` and gitignored. No reader changed.

## Measurements

All on the 1,821 files of `origin/master`, Python 3.13 `tarfile` + `lzma` (xz preset 6), 4 cores, one thread per experiment (pack time is single-threaded CPU time).

| Layout | Stored size | Objects | Largest object | Notes |
| --- | --- | --- | --- | --- |
| raw `.mpd` (before) | 387.9 MiB | 1,821 | 34 MiB | tree 503 MiB in total |
| one solid `tar.gz -6` | 57 MiB | 1 | 57 MiB | owner's figure, not re-measured |
| one solid `tar.xz -6` | 34.9 MiB | 1 | 34.9 MiB | any change re-stores all of it; 232 s |
| per-file `.mpd.xz` | 40.9 MiB | 1,821 | 3.2 MiB | +17%: no sharing between files; 174 s |
| shards by set number // 5000 | 35.0 MiB | 14 | 17.7 MiB | |
| shards by set number // 2000 | 35.0 MiB | 19 | 14.8 MiB | |
| shards by set number // 1000 | 35.0 MiB | 29 | 14.7 MiB | |
| **shards by set number // 100 (chosen)** | **35.6 MiB** | **130** | **11.7 MiB** | median 50 KiB, 126 of 130 under 1 MiB |

Unpack of everything (130 shards, 4 threads, sha256 of each file checked): 3.3 s. A second `unpack` on an unchanged tree: 0.08 s (stat only). `pack` of everything: 2 min 13 s on 4 threads. `zstd` is not in the build image and not in the Python stdlib, so it was not compared; `xz` is in both. Other xz presets were not measured; preset 6 is the default and already within 2% of the best layout.

## Why shards by set number, in blocks of 100

- **Git churn is the deciding trade-off.** A solid archive is the smallest but every added or corrected model re-stores 35 MiB, and git cannot delta xz output. Per-file `.xz` has zero churn but costs 17% more and puts 1,821 opaque blobs in the tree. Related models (a set and its B-models, neighbouring set numbers of one theme) share parts, subassemblies and comments, so grouping by set-number block keeps almost all of the solid ratio: 35.6 against 34.9 MiB.
- **100 over 1000.** Coarser blocks cost 0.6 MiB less but a change re-stores a bigger object (a 1000-block holds up to 15 MiB). With 100, adding a typical model rewrites a shard of tens of KiB; the median shard is 50 KiB. The heavy shards come from a few huge sets, not from the grouping: `s08400` (8466-1 and 8448-1, 11.7 MiB), `s42000` (5.2 MiB, 82 models, several of 3 to 7 MB raw), `s10200` (2.5 MiB), `s42100` (1.7 MiB). Touching one of those re-stores that shard, but they hold the models that rarely change.
- **Random access is not needed.** Nothing reads individual models from the archive; the folder is unpacked once. Whole-shard decompression is the cheap case (3.3 s for everything), so no per-file index, seek table or `zstd` frames were built.
- **Standard tools only.** `.tar.xz` opens with `tar xJf`, `xz` and `tarfile`; the tool is stdlib Python and runs in the image's `fetch` stage (Ubuntu's `python3`) and in a fresh clone.

## Determinism

`pack` is a pure function of the file contents: members sorted by name, fixed mtime (2000-01-01), uid/gid 0, empty owner names, mode 0644, PAX format, xz preset 6 with CRC64, one stream per shard. The manifest is sorted and written one line per shard and per model, so a change is a one-line diff. Packing twice into separate folders gives byte-identical shards and manifest (checked with `diff -r`). Because a `liblzma` upgrade may change encoder output, `pack` and `add` also compare the decompressed tar of the stored shard with the new one and leave the file untouched when the content is equal, so git stores nothing new even across xz versions.

## Manifest

`models-packed/manifest.json` (uncompressed, 0.5 MB): per shard its file, sha256 and size; per model its file name, shard, sha256 of the raw file, raw size, licence and author. The licence and author are the distinct `0 !LICENSE` and `0 Author:` header values of the file (some sets carry both CCAL 2.0 and "Free for non-commercial use."), or the `--licence` / `--author` given to `add`. `unpack` refuses a shard, or a file, whose sha256 differs from the manifest; `verify` checks all of it and the unpacked folder too; CI runs `verify --packed-only`.

## Other readers

Nothing changed: `prepare-glb.sh`, `instructions.md`, `docs/agent/*`, `examples/technic-studies/sources.json`, the `examples` / `study` / `sections` / `mechanism` commands and discovery all read `data/models-annotated/*.mpd`, which exists after `unpack`.

- `data/ldraw-info.db` stores model names relative to the folder (`MODELS_DESCRIPTIONS.model`, e.g. `10270-1.mpd`): no absolute paths, no mtimes, and it is not built from the folder. It stays valid as committed. The discovery catalog (`.cache/discovery/<hash>/`, built by `ldraw-agent index` and on first search) is keyed by each model's relative path, size and mtime and by the absolute models path. Unpacked files all carry the same fixed mtime, so it is stable across unpacks, and it is rebuilt in a new checkout exactly as before.
- `setup.sh` unpacks before `doctor` / `index`.
- `app/Dockerfile`: a new `models` stage (`FROM fetch`, which has `python3`) copies only `tools/models_pack.py` and `models-packed/` from the `nova` context, unpacks and verifies. The runtime stage copies `/out/models-annotated` to `/opt/ldraw-nova/data/models-annotated` right after the other unpacked downloads and before uv, the toolkit and the app files, so that 390 MB layer is rebuilt only when a shard or the tool changes (the `models` stage has the same single trigger). The final image holds the same files at the same path, no archives and no stamp file. The root `.dockerignore` keeps a locally materialised `data/models-annotated` out of the context, since `COPY --from=nova data/` would otherwise copy it twice. The build context shrinks by about 350 MiB. (The Dockerfile change was checked by running its unpack step by hand; the image itself was not built.)
- Tests: `tests/conftest.py` has a session fixture `reference_models` that unpacks on demand; `tests/test_models_pack.py` covers the tool and the shipped manifest. The root suite in CI runs inside the image, which already contains the folder.
- `.gitignore`: `data/models-annotated/` and its `.stamp` file. `.pre-commit-config.yaml`: `models-packed/` is exempt from the 1000 kB large-file check, like `data/`.

## Adding models

```bash
python3 tools/models_pack.py add path/to/12345-1.mpd [--licence '...' --author '...']
git add models-packed && git commit
```

`add` checks that the file is UTF-8 LDraw (only line types 0-5 with enough fields, at least one geometry or sub-file line) and that it has a `0 !LICENSE` header or an explicit `--licence`. It rewrites only the shard of its set number (about 40 s of CPU for a 5 MiB shard) and the manifest, and unpacks the result. The shipped models themselves are packed as they are: a few have a malformed line, which `pack` stores unchanged so the bytes stay identical.

## Not covered

History. Committing the shards and removing the raw folder shrinks the tree (503 MiB to 152 MiB of blobs; a new checkout and the image-build context lose about 350 MiB) but not the objects already in `.git`. The history rewrite is a separate step.
