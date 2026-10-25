# Plans: index and status

One page to see every plan and where it stands. Updated 2026-10-25.
For how the system works today, read [docs/system/README.md](../system/README.md); the security pass is in [docs/security/](../security/).

**Tracking moved to GitHub:** [epics](https://github.com/AlessioNar/ldraw-nova/issues?q=is%3Aissue+label%3Aepic) with sub-issues, and [owner decisions](https://github.com/AlessioNar/ldraw-nova/issues?q=is%3Aissue+label%3Aowner-decision). The docs below stay as the specs; the issues are the source of truth for status and next steps.

Status words: **Active** (work running or next), **Done** (built and merged; the doc is the record), **Reference** (background or analysis, nothing to build), **Superseded** (replaced by a later plan).

Folders match the status: active plans live in [active/](active/), everything else in [done/](done/).

## Active

| Plan | What it is | Status | Next step |
|---|---|---|---|
| [active/nl-review/](active/nl-review/REVIEW.md) | Critical review of how text requests flow; 3 improvement waves | Wave 1 **in progress** (free edits on any build, divergence guard, context, matching, colours, honest hint, faster saves) | Waves 2\u20133: more rules, multi-intent, preview before apply; new generators by demand |
| [active/postgres/](active/postgres/PROPOSAL.md) | Move app state from JSON files to PostgreSQL (SQLAlchemy + Alembic) | Phase 1 **done** (db service, request log, cost history, backups); owner decisions recorded | **Phase 2 next**: chats, builds, parts into the db + importer, tested on a copy of real data; phase 3: cutover, restore drill and retention checks **done** ([CUTOVER.md](active/postgres/CUTOVER.md)); JSON write paths and the parts tables (#47) remain |
| [active/build-grammar/](active/build-grammar/PROPOSAL.md) | One compositional generator for buildings ("ontology of LEGO builds"), 20 presets | Prototype **merged** and live as the "building" generator | Productionise: component-tree UI ([ui/](active/build-grammar/ui/)), details and roof variety, then vehicles/spaceships; open questions in the proposal |
| [active/build-ontology/](active/build-ontology/PROPOSAL.md) | The build ontology as RDF (OWL, SKOS, SHACL): one vocabulary for the grammar, the classifier and the templates | Epic #89 under way (#91\u201392 merged; #93\u201397 next) | #93 SHACL shapes, #94 schema generation, #95 templates as RDF, #96 vehicles, #97 publishing |
| [active/build-templates/](active/build-templates/PROPOSAL.md) | Roles for any model, role edits, templates derived from real builds | Prototype **merged** (6 templates); role edits wired into the chat by nl-review wave 1 | Wave 2: "Make this a template", template catalogue from the reference library; 4 open questions |
| [active/parts-catalogue/](active/parts-catalogue/PROPOSAL.md) | My Parts as real collection management | Stage 1 **done** (sources, undo, previews, sets by number, quick add, browser, review queue); owner decisions recorded | Stage 2: BrickLink/Rebrickable import + export, remaining readers behind the storage module (after Postgres phase 2); stage 3: reservations, shortage lists |

## Done (the record of what was built)

| Plan | What it delivered |
|---|---|
| [done/build-workspace/](done/build-workspace/README.md) | The always-3D build workspace: draft + versions, canvas, panels, one chat, chat routing, resizable panels; [CONTRACT.md](done/build-workspace/CONTRACT.md) is the API/canvas protocol; [editor-performance.md](done/build-workspace/editor-performance.md) the speed work |
| [done/phase2/](done/phase2/fast-path.md) | Fast-path decision engine (`/api/describe`, the free hint) and builds as projects |
| [done/deterministic-first/](done/deterministic-first/PROPOSAL.md) | The study behind "rules and generators first, AI only when agreed"; waves 1\u20132 built (understanding step, generators, build worker, fast path) |
| [done/design-system/](done/design-system/applied/README.md) | Blueprint design system with Toy box touches, applied to the app ([studio/](done/design-system/studio/), [toybox/](done/design-system/toybox/), [blueprint/](done/design-system/blueprint/) are the three proposals) |
| [done/manual-builder/](done/manual-builder/README.md) | The first manual 3D editor (now the workspace canvas) |
| [done/swarm-subagents/](done/swarm-subagents/README.md) | Cheap worker agents under one budget for the AI builder (end-to-end run not done) |
| [done/models-pack.md](done/models-pack.md) | Reference models shipped compressed |
| [done/set-catalog.md](done/set-catalog.md) | Offline catalogue of real LEGO sets |
| [done/rebrickable-mapping.md](done/rebrickable-mapping.md) | Rebrickable \u2194 LDraw part and colour mapping |

## Reference (analysis, audits, background)

| Doc | What it is |
|---|---|
| [done/journeys/](done/journeys/01-first-run-and-building.md) | User-journey audits (first run, parts and editing, collection and cost); most top findings since fixed |
| [done/loop-perf/](done/loop-perf/REPORT.md) | Where the AI agent loop spends tokens and time (cache, history, reasoning fixes) |
| [done/data-audit.md](done/data-audit.md) | Heavy tracked directories in the repo |
| [done/overnight-cycles.md](done/overnight-cycles.md) | Earlier toolkit maintenance plan |

## Superseded

| Plan | Replaced by |
|---|---|
| [done/chat-redesign/](done/chat-redesign/PROPOSAL.md) | The build workspace and the four-screen app (chat lives in the build) |

## Not yet written as plans (agreed directions)

- Wave 3 polish: editor shortage list, instant "what can I build" (today ~5 min warm-up), cost-history page (data now recorded), guided first run.
- Bins and drawers for parts (after notes per part).
- Own part detection from photos (later).

## Housekeeping

Folders have been moved into `active/` / `done/` (issue #69): code comments, tests and data files now reference the rewritten paths (e.g. tests read `active/build-grammar/ontology.schema.json` and `active/nl-review/prompts.json`). When a plan finishes, move its folder from `active/` to `done/` and update this index.
