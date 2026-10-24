# Roadmap: done, in progress, planned

The statuses below are inferred from the plan documents, the code and the git history. The plan docs rarely carry a status line themselves. **PLANNED** means not in the code.

## Done

| Area | What | Plan |
|---|---|---|
| Reference models packed | 1,821 models as 130 xz shards, verified by sha256 | [models-pack.md](../plans/models-pack.md), [data-audit.md](../plans/data-audit.md) |
| Rebrickable mapping | Part and colour maps for parts list imports | [rebrickable-mapping.md](../plans/rebrickable-mapping.md) |
| Agent loop performance | Rolling cache breakpoint, volatile state after history, masking of old tool output | [loop-perf/](../plans/loop-perf/REPORT.md) |
| 3D editor core | Plan editing, palette, colours, agent handover | [manual-builder/](../plans/manual-builder/README.md) |
| Chat redesign | Sidebar, four-control composer, build header, grouped transcript (the doc header still says "nothing implemented") | [chat-redesign/PROPOSAL.md](../plans/chat-redesign/PROPOSAL.md) |
| Design system | "Blueprint with a hint of Toy box" applied; follows the OS light/dark theme | [design-system/applied](../plans/design-system/applied/README.md) |
| Deterministic first, core | Generator registry, understanding step, resident build worker with cache, fast path | [deterministic-first/PROPOSAL.md](../plans/deterministic-first/PROPOSAL.md) (tasks 1–3) |
| Fast path API | `POST /api/describe`, selectable understanding model | [phase2/fast-path.md](../plans/phase2/fast-path.md) |
| Build workspace | Builds, versions, one draft, always-3D workspace, proposals, editor speed-ups | [build-workspace/](../plans/build-workspace/README.md), [CONTRACT.md](../plans/build-workspace/CONTRACT.md) |
| Brickit Pileometer import | Including colour-family review | [parts-catalogue/PROPOSAL.md](../plans/parts-catalogue/PROPOSAL.md) (pre-existing part) |
| Security pass and hardening | Firewall, Host allowlist, separate Jev key, hash locks | [security pass](../security/2026-10-24-security-pass.md) |
| UX journeys | Three audits with ranked friction lists; many fixes delivered since | [journeys/](../plans/journeys/01-first-run-and-building.md) |
| Sets catalogue data | About 20k sets with a CLI (**not wired into the app**) | [set-catalog.md](../plans/set-catalog.md) |

## In progress, or built but not verified

| Area | State | Plan |
|---|---|---|
| Swarm of worker models | The code is in (`subagents.py`, budget ledger, isolation, UI cards). **The end-to-end cost/quality run was never done**, and the worker capability measurement was partial. | [swarm-subagents/](../plans/swarm-subagents/README.md), [E2E.md](../plans/swarm-subagents/E2E.md) |
| NL pipeline quality | The review is done and the fix plan is written. No fixes yet, and the live LLM evaluation has not run. | [nl-review/REVIEW.md](../plans/nl-review/REVIEW.md) |

## Planned (not built)

### NL pipeline, REVIEW.md section 6 (about 22–30 person-days)

- **Wave 1, about 8–10 days:**
  - role- and selection-based text edits on **any** build;
  - a guard against stale generator sources;
  - conversation context;
  - clarify-first with chips and honest hints;
  - seeding the agent with the spec and the nearest generator;
  - word-boundary kind matching with "closest design" notes;
  - colour harmony;
  - show the model first and check it in the background, with the worker warmed at start;
  - a local pipeline log.
- **Wave 2, about 6–8 days:**
  - broader rules (target: 50% of follow-ups with no LLM call);
  - multi-intent requests;
  - preview before big changes;
  - errors as chips.
- **Wave 3, about 8–12 days:**
  - every chat message goes through the fast path first;
  - **new generators by measured demand**: tower/castle/lighthouse/windmill, tree/garden, train, helicopter/plane, ship, furniture;
  - role-aware retrieval;
  - CI-lite evaluation.
- **Open owner questions** (REVIEW.md:269-275): the stale-source policy, how many clarifying questions to allow, language support, telemetry, and whether stand-in designs are acceptable.

### Deterministic first, remaining tasks

- **Task 4, retrieve + recolour reference models.** This is what "templates" would be. It is the natural place for FTS, and optionally Jev, outside the agent. See [jev-and-search.md](jev-and-search.md#recommendation).
- **Task 5, an edit executor with bounded repair** (`edits.py`, `repair.py`).
- **Task 6, a family-card option form.**

### Parts catalogue, waves 1–4 (owner decisions 2026-10-24)

- collection page structure;
- adding whole sets;
- loose parts;
- BrickLink and BSX import;
- exports;
- part notes, then locations;
- reservations for built models;
- "maybe" counts for colour families;
- a rule-based quick add;
- part thumbnails;
- later, own photo-based part detection.

### Other

- Wiring the sets catalogue into what-can-I-build.
- Dependabot or Renovate and audits in CI.
- Telemetry: a local JSONL log of describe decisions, as proposed in REVIEW.md:261-267.
