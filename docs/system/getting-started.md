# Getting started as a developer

This page takes you from a fresh clone to making and shipping a change. It covers the tools you need, how to run the app in dev mode, how the code is organised, and how work flows from an issue to a merged pull request.

## What you need

| Tool | Why | Version |
|---|---|---|
| Git | Clone and branch | any recent |
| Docker with Compose | The app and its database run in containers; the backend tests need the image (LeoCAD, the LDraw library) | Docker Desktop or Engine with `docker compose` |
| Node.js and npm | Build and type-check the frontend; viewer browser tests | Node 24 (same major as the image) |
| Python | Run the toolkit and its tests outside Docker, lint, ontology checks | 3.12 or newer |
| [uv](https://docs.astral.sh/uv/) (optional) | Installs the toolkit's locked environment from `uv.lock` | any recent |
| [ruff](https://docs.astral.sh/ruff/) | Lint (there is no formatter) | 0.17.1 |

The first image build needs about 5 GB of disk and takes a while.

## First run

```bash
git clone https://github.com/AlessioNar/open-brick-designer.git
cd open-brick-designer/app
docker compose build
docker compose up -d
```

Open <http://localhost:8765>, go to **Settings** and add a key for at least one LLM provider (Anthropic, OpenAI, Mistral or OpenRouter). Without a key the app still opens and generators still work from the forms, but typed requests cannot be read by the understanding model. See [Settings and providers](settings-and-providers.md).

## Dev mode

Dev mode mounts the backend and viewer from your checkout and runs the server with auto-reload, so most edits apply without rebuilding the image (`app/docker-compose.dev.yml`).

```bash
cd app
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d
```

To make plain `docker compose up -d` use dev mode on your machine, put these two lines in `app/.env` (which is gitignored):

```
COMPOSE_PATH_SEPARATOR=,
COMPOSE_FILE=docker-compose.yml,docker-compose.dev.yml
```

For the frontend, either run the Vite dev server with hot reload, or build once and reload the page on port 8765:

```bash
cd app/web/frontend
npm install
npm run dev      # http://localhost:5173, proxies /api to the backend
# or
npm run build    # writes dist/, which the backend serves on :8765
```

What needs what after a change:

| You changed | Then |
|---|---|
| Backend Python (`app/web/backend/`) or viewer files (`app/web/viewer/`) | Nothing in dev mode: the server reloads |
| React frontend (`app/web/frontend/src/`) | Nothing with `npm run dev`; otherwise `npm run build` and reload |
| Toolkit (`ldraw_tools/`), `models-packed/`, `requirements*.txt/.lock`, `Dockerfile` | `docker compose up -d --build`, because the toolkit is baked into the image |

More in [Operations](operations.md).

## Working on the toolkit without Docker

The toolkit runs on plain Python. `setup.sh` unpacks the reference models, installs the environment and builds the search index:

```bash
./setup.sh            # uses uv if present, otherwise a .venv
./ldraw-agent --help
uv run pytest -q      # toolkit tests in tests/
```

Rendering commands need LeoCAD installed locally; inside the container they are already there. See [Toolkit and CLI](toolkit.md).

## How the code is organised

The repository map is on the [overview page](README.md#repository-map). The short version:

- **Product code** is in `app/`: the backend (`app/web/backend/`), the frontend (`app/web/frontend/`) and the 3D pages (`app/web/viewer/`).
- **Modelling code** is in `ldraw_tools/`: everything that knows about bricks, from geometry to generators. The backend calls it through long-lived worker processes and the `./ldraw-agent` CLI.
- **Data that ships with the app** is in `data/`, `models-packed/`, `ldraw_tools/data/` and `ontology/`.

Conventions you will see everywhere:

- Backend paths in the docs are from the repo root. Functions are named as `file` plus `function()`, not line numbers.
- The plan JSON is the source of truth for a model's contents; MPD files are produced from it.
- Generators are pure functions of their parameters, so `(generator id, parameters)` is a cache key.
- Anything that costs money goes through a budget and is recorded in the cost history.
- The app is desktop only. Do not spend effort on phone layouts.

## How work flows

1. **Start from an issue.** Every change, however small, has a GitHub issue. If none exists, create one. Label it `in-progress` while you work on it, and check it is not already assigned or in progress by someone else first. Epics group issues; issues labelled `owner-decision` wait on the owner.
2. **Branch from `master`.** `master` is the default branch. An old `main` branch exists; never target it.
3. **Make the change and run the fast checks locally:** `ruff check` at the repo root, `npm run build` in `app/web/frontend`, and the tests for what you touched (see [Testing and CI](testing-and-ci.md)).
4. **Update the docs.** If your change alters how a part works, update its page in this manual in the same pull request.
5. **Open a pull request against `master`** that says `Closes #N` for its issue.
6. **Merge when the checks are green.** The required checks are `quick`, `ontology` and `audit`. The `image` job (full build plus backend and toolkit tests) runs on pushes to `master` and on manual dispatch, not on pull requests, because of Docker Hub rate limits. Run it by hand from the Actions tab for risky changes.
7. **Check on `master`.** The owner does visual checks of merged work on `master`.

Commit messages are short, in the present tense, and often start with the area: `Chat: ...`, `Toolkit: ...`, `Docs: ...`.

## Where to go next

- [Request lifecycle](request-lifecycle.md): the main flow through the app.
- [Frontend](frontend.md) and [Backend and API](backend.md): the two halves of the web app.
- [Testing and CI](testing-and-ci.md): what to run before you push.
