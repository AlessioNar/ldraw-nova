# Overnight improvement cycles

Goal: a smaller codebase that is quick to build, easy to navigate and fast to run.

## Baseline (measured 2026-10-20)

- Source is small: ~19k lines of py/ts/js/html/sh/css. The biggest files are
  `app/web/viewer/viewer.html` (775), `ldraw_tools/cli.py` (740),
  `app/web/backend/main.py` (707) and `agent.py` (519).
- Weight is data: `app/data` is 130 MB of the 134 MB under `app/` (chat logs,
  validation JSON/logs, generated `.mpd`). `examples/` is 17 MB. `.git` is 299 MB.
- `app/Dockerfile`: 300 lines, 18 sections, one runtime stage. The pinned
  downloads (LeoCAD, LDraw library, bun, mpd2glb, uv, jev-rerank, ldraw-player)
  are the slow part of a cold build.
- Backend tests: 15 fail outside the image (need LeoCAD / the LDraw library).
  Treat that list as the baseline; never add to it.

## Rules for every cycle

- One branch and one PR per cycle, off `master`. Never merge; never force-push
  `master`. Commits authored as the repo owner, no co-author trailers.
- Run backend tests, `npm run build` in `app/web/frontend`, and the root
  `pytest`; report before/after numbers, not impressions.
- Behaviour-preserving only. If a change needs a product decision, leave it out
  and list it in the PR description.
- Do not delete data. Moving it out of git is fine; destroying it is not.

## Cycle 1: weight (what is in the repo)

1. Stop tracking `app/data/{chats,output}` (keep `.gitkeep`, and a handful of
   small representative runs under `examples/`). Files stay on disk.
2. Audit `examples/` (17 MB) and `docs/reports/` for generated artefacts that
   can be regenerated; list candidates, remove the clearly derived ones.
3. Dead code: `verify_endplate.py`, `VARIANT_REPORT.md`, `check-model.sh`,
   `.obsidian`/`.DS_Store` leftovers, unused `app/scripts`, unused CSS and
   exports (`knip`/`vulture` reports, reviewed before deleting).
4. Output: size table before/after, plus the list of everything removed.
   Decision left to the owner: rewrite history to shrink `.git` (299 MB) with
   `git filter-repo`, which changes every commit ID.

## Cycle 2: Docker builds

1. Measure first: cold and warm `docker compose build` times per stage
   (`--progress plain`), image size by layer (`docker history`).
2. Cache mounts for apt, npm, pip and uv; order layers so edits to
   `app/web/backend` or the frontend never rebuild the downloads.
3. Combine and slim: one apt layer for related packages, drop build-only tools
   from the runtime stage, move unpacking steps (LeoCAD, mpd2glb, ldraw-player)
   into throwaway stages and `COPY --from` the results.
4. `.dockerignore` audit for both contexts; check `docker compose up --watch`
   still syncs edits without rebuilds.
5. Needs working network to Docker Hub and the pinned download hosts. Run it
   somewhere that has them (the sandbox hit a registry rate limit and a TLS
   proxy error on the LeoCAD download).

## Cycle 3: code organisation and readability

1. `app/web/backend/main.py` (707 lines): split into FastAPI routers by area
   (llm models, chats, files/gallery, static mounts). No behaviour change; the
   existing API tests are the safety net.
2. `agent.py`: separate the turn loop, history building (`llm_history`,
   `bounded_history`) and prompt assembly into modules; keep `providers.py` as
   the only place that knows provider prefixes.
3. `viewer.html` / `player.html`: move inline JS and CSS into files so they can
   be linted and cached; extract the code the two pages share.
4. `ldraw_tools/cli.py` (740): one module per command group.
5. Delete duplicated helpers found along the way; add a short
   `app/web/backend/README.md` module map.

## Cycle 4: agent loop performance

Needs a live API key and a fixed benchmark prompt, so run it only when one is
configured. Uses the per-step usage telemetry already on this branch.

1. Baseline: for a fixed 3-prompt benchmark, record steps, wall time, input /
   output / cached tokens and cost per run.
2. Confirm prompt caching: `cache_read_input_tokens` must be non-zero from the
   second step. If not, find what changes per request in the prefix.
3. Reduce context growth: tool output truncation limits, image retention policy
   (`KEEP_IMAGE_MESSAGES`), and how history is trimmed before it hits the model.
4. Tool latency: time each tool call; cache repeated LDraw lookups; batch
   independent tool calls in one step where the provider supports it.
5. Report before/after per metric. Anything that changes model behaviour (not
   just cost or speed) goes in the PR as an option, not a default.

## Order and cadence

Cycles 1 and 3 need nothing external and can run any night. Cycle 2 needs
network access; cycle 4 needs an API key. Suggested order: 1, 3, 2, 4.
