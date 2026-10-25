# Testing and CI

This page lists every test suite, how to run it, and what the GitHub Actions jobs check. The rule of thumb: run the fast checks locally before you push, and run the tests of the part you touched.

## The fast checks (run these before every push)

| Check | Command | Where |
|---|---|---|
| Lint | `ruff check` (ruff 0.17.1, rules in `ruff.toml`) | repo root |
| Frontend types and build | `npm run build` (`tsc -b && vite build`; type errors fail it) | `app/web/frontend` |
| Pre-commit hooks | `pre-commit run --all-files` (YAML check, merge-conflict markers, large files, ruff) | repo root |
| Reference model shards | `python tools/models_pack.py verify --packed-only` | repo root |

There is no code formatter. Ruff is lint only.

## Test suites

| Suite | Where | How to run | Needs |
|---|---|---|---|
| Backend | `app/web/backend/tests/` (about 70 files) | Inside the container: `docker compose exec ldraw-nova-app bash -c "pip install -q --break-system-packages -r /app/web/backend/requirements-dev.txt && cd /app/web/backend && pytest -q"` | The image (LeoCAD, the LDraw library, indexes) |
| Toolkit | `tests/` (about 45 files) | `uv run pytest -q` at the repo root, or in the image as CI does (below) | The LDraw library for most files (`tests/conftest.py`) |
| Ontology | a subset of `tests/` plus the `tools/*.py --check` scripts | See the `ontology` job below; runs without the LDraw library | `pip install -e ".[ontology,test]"` |
| Viewer and editor in a browser | `app/web/viewer-deps/test/` (Playwright) | `npm test`, `npm run test:editor`, `npm run test:canvas` in `app/web/viewer-deps`, against a throwaway container | A running app |
| Editor core | `app/web/viewer-deps/test/editor-core.test.mjs` | `npm run test:core` | Node only |
| Workspace end to end | `app/web/viewer-deps/test/workspace.e2e.mjs` | By hand, against a throwaway container | A running app with data |
| Understanding model eval | `app/web/backend/understand_eval.py` with `understand_golden.json` | See [Request lifecycle](request-lifecycle.md) | An LLM key; costs a little |

How the backend tests isolate themselves (`app/web/backend/tests/conftest.py`): before any app module is imported, `/data` and `/config` are redirected to a temporary folder (`LDRAW_NOVA_DATA_DIR`, `LDRAW_NOVA_WEB_CONFIG_DIR`), and the storage is forced to JSON files. To run the suite against PostgreSQL instead, set `LDRAW_NOVA_TEST_STORAGE=postgres` and `LDRAW_NOVA_TEST_DATABASE_URL`; tests marked `files_only` then skip.

Never point tests at the running `ldraw-nova-app` container or its volumes. Use a throwaway container and, for live LLM tests, a small budget.

## CI jobs

The workflow is `.github/workflows/ci.yml`. It runs on every pull request, on pushes to `master` and on manual dispatch (Actions tab, CI, Run workflow). A newer push cancels the older run.

| Job | Runs on | What it checks | Time |
|---|---|---|---|
| `quick` | every PR and push | `npm ci` and `npm run build` for the frontend, `ruff check`, reference model shards against the manifest | about 1 minute |
| `ontology` | every PR and push | Generated files are in sync with `ontology/*.ttl` (`tools/build_role_vocab.py --check`, `tools/build_obd.py --check`, `tools/export_ontology.py --check`), every grammar preset passes the SHACL shapes (`tools/validate_shapes.py --presets`), and the ontology tests | about 1 minute |
| `audit` | every PR and push | Known vulnerabilities in runtime dependencies: `npm audit --omit=dev` for the frontend and viewer, `pip-audit` for `app/requirements.lock` and the toolkit's `uv.lock` | about 1 minute |
| `image` | pushes to `master` and manual dispatch, **not** pull requests | Builds the Docker image, runs smoke checks (LeoCAD, bun, mpd2glb, jev-rerank), the backend tests and the toolkit tests inside it | up to 60 minutes |

The `image` job does not run on pull requests because the build pulls many base images and runs into Docker Hub rate limits. A pull request can merge when `quick`, `ontology` and `audit` are green. For a change that could break the image or the tests that need it, dispatch the CI workflow on your branch by hand first.

A second workflow, `.github/workflows/docs.yml`, builds this site with MkDocs Material on pull requests that touch `docs/`, `mkdocs.yml` or `docs-requirements.txt`, and publishes it to GitHub Pages from `master`. It also checks that the ontology Turtle files are published under `ns/` and that `docs/security/` is not. To preview locally:

```bash
pip install -r docs-requirements.txt
mkdocs serve      # http://127.0.0.1:8000
```

## Dependency updates

Dependabot (`.github/dependabot.yml`) opens weekly pull requests for the backend (`app/requirements.txt`), the toolkit (`uv.lock`), the frontend and viewer npm packages, and the GitHub Actions. A backend bump also needs `app/requirements.lock` regenerated (the Dockerfile installs it with `--require-hashes`); the header of that file says how.

## Related

- [Getting started](getting-started.md): the flow from issue to merged pull request.
- [Operations](operations.md): dev mode, logs, rebuilds.
