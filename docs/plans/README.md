# Plans: index and status

One page to see every plan and where it stands. Updated 2026-10-25.
For how the system works today, read [docs/system/README.md](../system/README.md); the security pass is in [docs/security/](../security/).

**Tracking moved to GitHub:** [epics](https://github.com/AlessioNar/ldraw-nova/issues?q=is%3Aissue+label%3Aepic) with sub-issues, and [owner decisions](https://github.com/AlessioNar/ldraw-nova/issues?q=is%3Aissue+label%3Aowner-decision). The docs below stay as the specs; the issues are the source of truth for status and next steps.

Status words: **Active** (work running or next), **Done** (built and merged; the doc is the record), **Planned** (agreed, not started), **Reference** (background or analysis, nothing to build), **Superseded** (replaced by a later plan).

## Active

| Plan | What it is | Status | Next step |
|---|---|---|---|
| [nl-review/](nl-review/REVIEW.md) | Critical review of how text requests flow; 3 improvement waves | Wave 1 **in progress** (free edits on any build, divergence guard, context, matching, colours, honest hint, faster saves) | Waves 2–3: more rules, multi-intent, preview before apply; new generators by demand |
| [postgres/](postgres/PROPOSAL.md) | Move app state from JSON files to PostgreSQL (SQLAlchemy + Alembic) | Phase 1 **done** (db service, request log, cost history, backups); owner decisions recorded | **Phase 2 next**: chats, builds, parts into the db + importer, tested on a copy of real data; phase 3: retention, backups drill, cutover |
| [build-grammar/](build-grammar/PROPOSAL.md) | One compositional generator for buildings ("ontology of LEGO builds"), 20 presets | Prototype **merged** and live as the "building" generator | Productionise: component-tree UI ([ui/](build-grammar/ui/)), details and roof variety, then vehicles/spaceships; open questions in the proposal |
| [build-templates/](build-templates/PROPOSAL.md) | Roles for any model, role edits, templates derived from real builds | Prototype **merged** (6 templates); role edits wired into the chat by nl-review wave 1 | Wave 2: "Make this a template", template catalogue from the reference library; 4 open questions |
| [parts-catalogue/](parts-catalogue/PROPOSAL.md) | My Parts as real collection management | Stage 1 **done** (sources, undo, previews, sets by number, quick add, browser, review queue); owner decisions recorded | Stage 2: BrickLink/Rebrickable import + export, remaining readers behind the storage module (after Postgres phase 2); stage 3: reservations, shortage lists |

## Done (the record of what was built)

| Plan | What it delivered |
|---|---|
| [build-workspace/](build-workspace/README.md) | The always-3D build workspace: draft + versions, canvas, panels, one chat, chat routing, resizable panels; [CONTRACT.md](build-workspace/CONTRACT.md) is the API/canvas protocol; [editor-performance.md](build-workspace/editor-performance.md) the speed work |
| [phase2/](phase2/fast-path.md) | Fast-path decision engine (`/api/describe`, the free hint) and builds as projects |
| [deterministic-first/](deterministic-first/PROPOSAL.md) | The study behind "rules and generators first, AI only when agreed"; waves 1–2 built (understanding step, generators, build worker, fast path) |
| [design-system/](design-system/applied/README.md) | Blueprint design system with Toy box touches, applied to the app ([studio/](design-system/studio/), [toybox/](design-system/toybox/), [blueprint/](design-system/blueprint/) are the three proposals) |
| [manual-builder/](manual-builder/README.md) | The first manual 3D editor (now the workspace canvas) |
| [swarm-subagents/](swarm-subagents/README.md) | Cheap worker agents under one budget for the AI builder (end-to-end run not done) |
| [models-pack.md](models-pack.md) | Reference models shipped compressed |
| [set-catalog.md](set-catalog.md) | Offline catalogue of real LEGO sets |
| [rebrickable-mapping.md](rebrickable-mapping.md) | Rebrickable ↔ LDraw part and colour mapping |

## Reference (analysis, audits, background)

| Doc | What it is |
|---|---|
| [journeys/](journeys/01-first-run-and-building.md) | User-journey audits (first run, parts and editing, collection and cost); most top findings since fixed |
| [loop-perf/](loop-perf/REPORT.md) | Where the AI agent loop spends tokens and time (cache, history, reasoning fixes) |
| [data-audit.md](data-audit.md) | Heavy tracked directories in the repo |
| [overnight-cycles.md](overnight-cycles.md) | Earlier toolkit maintenance plan |

## Superseded

| Plan | Replaced by |
|---|---|
| [chat-redesign/](chat-redesign/PROPOSAL.md) | The build workspace and the four-screen app (chat lives in the build) |

## Not yet written as plans (agreed directions)

- Wave 3 polish: editor shortage list, instant "what can I build" (today ~5 min warm-up), cost-history page (data now recorded), guided first run.
- Bins and drawers for parts (after notes per part).
- Own part detection from photos (later).

## Housekeeping

Folders are not moved into `done/` / `active/` subfolders yet: code comments, tests and data files reference these paths (e.g. tests read `build-grammar/ontology.schema.json` and `nl-review/prompts.json`). Do the move when no agent is running, together with a path rewrite and a test run.
