# <img src="img/open-brick-designer.svg" width="40" alt=""> Open Brick Designer

Open Brick Designer is a web app for designing LEGO© models as [LDraw](https://www.ldraw.org/) files, in a 3D workspace, with rules and generators doing most of the work and an AI agent as an opt-in extra. It is a fork of [anteloc/ldraw-nova](https://github.com/anteloc/ldraw-nova); the toolkit it packages keeps that name (`ldraw_tools`, `./ldraw-agent`).

## What's in it

- **App shell:** a slim top bar with **Home**, **My Builds**, **My Parts** and **Settings**.
- **Builds:** every model is a build with numbered versions and one editable draft. Start one empty, describe it in words, or import a model file.
- **Workspace:** an always-3D editor where you place and edit parts, with the build's chat beside the canvas. You can view, play step by step, render, and export (`.mpd`, Blender-ready `.glb`).
- **Deterministic first:** one cheap LLM call reads your request, then generators build what they can for free (buildings, houses, vehicles, spaceships). The paid AI agent runs only after you agree to a price.
- **Building grammar:** one compositional generator for buildings, with 24 presets from cottages and shops to pagodas, lighthouses and modular buildings.
- **Roles and templates:** parts in any model get roles (wall, roof, trim, glass…), so you can edit by role ("make the roof red") and stretch templates derived from real builds.
- **My Parts:** your own collections, imported from Brickit/Pileometer, added by set number from an offline catalogue of about 20k LEGO sets, or by hand. Builds can check what you are missing.
- **Reference library:** 1,821 annotated real LEGO models that the generators and the agent learn from.
- **LLM providers:** Anthropic, OpenAI, Mistral and OpenRouter via LiteLLM, with per-request cost tracking.

How it all fits together is in [docs/system/](docs/system/README.md). Plans and their status are in [docs/plans/](docs/plans/README.md); open work is tracked in [GitHub issues](https://github.com/AlessioNar/ldraw-nova/issues).

## Installation

You need [Git](https://git-scm.com/downloads) and [Docker](https://docs.docker.com/get-started/get-docker/). The first build takes a while and needs about 5 GB of disk.

```bash
git clone https://github.com/AlessioNar/ldraw-nova.git
cd ldraw-nova/app
docker compose build
docker compose up -d
```

Open **http://localhost:8765**, then add an LLM provider key in **Settings**. Stop with `docker compose down`.

- The app only listens on `localhost`. To reach it from other devices, set `LDRAW_NOVA_WEB_BIND=0.0.0.0` in `app/.env`. There is no login, so only do this on networks you trust.
- Builds, chats and parts live in the Docker volume `ldraw-nova-docker_data`, and the request log and cost history in PostgreSQL. `docker compose down -v` deletes both; `bash scripts/backup.sh` backs them up (see [docs/system/database.md](docs/system/database.md)). Copy models out with `docker cp ldraw-nova-app:/data/generated ./my-models`.
- A [TypeSafe](https://typesafe.ai/) key (optional, in **Settings**) turns on [Jev](https://github.com/anteloc/jev-rerank) semantic search for the AI agent's part and model lookups. Without it, search falls back to full-text (BM25) ranking.

## Reference models

The reference models are stored compressed in `models-packed/` (about 36 MB of `.tar.xz` shards plus `manifest.json` with licence and author for each model). `setup.sh`, the Docker build and the tests unpack them into `data/models-annotated/`, which is not in git.

```bash
python3 tools/models_pack.py unpack                  # shards -> data/models-annotated/
python3 tools/models_pack.py add my-set-12345-1.mpd  # add or replace a model
```

`add` refuses files that are not valid `.mpd` or have no `0 !LICENSE` header unless you pass `--licence` (and `--author`). Details: [docs/plans/models-pack.md](docs/plans/models-pack.md).

## Development

- Lint with [ruff](https://docs.astral.sh/ruff/) 0.17.1: `ruff check` from the repo root (rules in `ruff.toml`), or `pre-commit run --all-files`.
- Frontend types: `npm run build` in `app/web/frontend`.
- CI builds the image and runs the backend and toolkit tests inside it on every pull request. Dev mode and common tasks are in [docs/system/operations.md](docs/system/operations.md).
- Guides for the AI agent (vehicles, Technic, mechanisms, geometry, validation…) start at [instructions.md](instructions.md) and [docs/agent/](docs/agent/).

## Acknowledgements

- [anteloc/ldraw-nova](https://github.com/anteloc/ldraw-nova), the toolkit this project started from.
- [The LDraw Community](https://www.ldraw.org/), and all the LDraw model, library and tool authors whose work is used here.
- [LDView](https://tcobbs.github.io/ldview/) (Travis Cobbs), [LeoCAD](https://github.com/leozide/leocad) (Leonardo Zide), [LDCad](https://www.melkert.net/LDCad) and the Shadow Library (Roland Melkert), [ldraw.rs](https://github.com/segfault87/ldraw.rs) (Park Joon-Kyu), [pyldraw3](https://github.com/hbmartin/pyldraw3) (Harold Martin), and their contributors.
- [Rebrickable](https://rebrickable.com/): the set catalogue (`ldraw_tools/data/set-catalog.bin`, `ldraw-agent sets`) and the part and colour mapping derive from Rebrickable's database downloads. **Data from Rebrickable, rebrickable.com** (free for personal and commercial use provided Rebrickable is acknowledged as the data source).

Licences: see [LICENSE](LICENSE), [CC-BY-SA-4.0](CC-BY-SA-4.0) and [ATTRIBUTION.md](ATTRIBUTION.md).

## Trademarks

**LEGO®** is a trademark of the **LEGO Group** of companies, which does not sponsor, authorize or endorse this software. Set names, numbers and parts lists in the set catalogue are factual data from Rebrickable; Rebrickable is not affiliated with this project either.
