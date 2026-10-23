# Deterministic-first build pipeline: the LLM understands, code builds

Design study with measurements. No product code changed, no LLM call and no API key used. All timings come from a throwaway container of the app image (amd64, 8 cores, software GL, unprivileged `agent` user), `docs/plans/deterministic-first/measure.py` and `measure2.py` (kept; `coverage_models.py` counts reference models). Raw numbers for four cases are in `timings/`.

Direction (owner): the LLM stays first-class for **natural-language understanding** of every request and every follow-up; it stops doing the **building**. Code chooses, builds, validates, repairs and edits. The full agent loop is only the fallback.

## 1. What the toolkit already does without an LLM

| Capability | Where | Parameters | Serves |
|---|---|---|---|
| 7 vehicle generators | `vehicles.py`, `vehicle_families.py` (`vehicle plan NAME --palette P`) | design (grand-tourer, delivery-van, pickup, tipper-truck, touring-motorcycle, harbour-launch, courier-jet); palette = one of 3 vehicle palettes (`heritage-racing`, `coastal-delivery`, `desert-utility`, 9 colour roles each). Length and wheelbase are constants per design. | car, van, pickup, truck, motorbike, small boat, small jet |
| 6 vehicle fittings | `vehicle_details.py` | cockpit, mirror, cargo chest, nav lights, engine pod | add-ons |
| Building vocabulary (Python API only) | `architecture.py`: `room(w,d,rows,door,windows,material,timber...)`, `gable(w,d,tiers,dormer)`, `flat_roof`, `porch`, `window4`, `door4`, `tree`, `fence`, `stairs`, `fountain`, `bench`, `lamp`... | integers for size, any colour code | houses, shops, towers, sheds, gardens. No CLI/generator wrapper exists. |
| 21 building examples + 18 details | `examples/building-atlas/generate.py` | fixed designs (cottage, museum, fire station, lighthouse, gatehouse...), only `--outdir` and render flags | whole-building "starting points", not parametric |
| 3 architectural palettes, 4 detail plans | `design-palettes.json`, `details.py` | palette, detail name | trim/shopfront accents |
| 2 reference recipes | `reference_recipes.py` | `height` 2-10, colour, accent | arcade bay, street lantern |
| 4 Technic structure recipes | `technic_recipes.py` (`technic plan`) | levels, colour, accent | reinforced frame, box chassis, frame tower, service platform |
| 10 Technic + 6 mechanism studies | `examples/technic-atlas`, `mechanism-atlas`: prebuilt MPD + generator | none | engines, gearbox, four-bar lift, suspension, differential, worm drive, turntable |
| 3 spaceship briefs + 8 studies | `spaceships.py`, `examples/spaceship-atlas` | brief only; **no spaceship generator**. 1 whole-ship study that can be adapted (B-wing, 434 parts), 2 "inspiration" studies with source errors (not exportable), 5 sub-modules (wing, nacelle, canopy, armour, greebles) | spaceships only by retrieval |
| 1,821 annotated reference models | `data/models-annotated`, `ldraw-info.db` (FTS5), `search models`, `discover search`, `study`, `extract` | free text; real sets, mostly large | retrieval |
| Set catalog | `sets.py`, `sets search` | name/theme/year/parts | part lists only, no geometry |
| Plan engine | `builder.build_plan`, `snap`, `editor.apply` (`add move rotate recolour delete snap`, inverse history, `rebuild`), `catalog` symbols (`@bricks.*`, `@colours.*`) | plain JSON plans | the substrate for every edit |
| Checks | `validation.py`, `geometry.analyze_geometry` (AABB + oriented bounds + SAT), `connectivity.py`, `vehicle_review.py`, `technic_review.py` | | validate/repair loop |

Reality check on the reference models. The 1,821 files are real LEGO sets (hundreds to thousands of parts, minifigures, many with source errors). They can be **retrieved and recoloured** (colour remap on type-1 lines is exact and free), and sub-assemblies can be swapped through `extract` + `editor`, but they cannot be rescaled (parts are rigid, no scaling or mirroring) and strict validation of a retrieved set often fails on the source's own transforms (see B-wing below). Rough subject frequency in the descriptions of the 1,821 models (regex on text, overlapping, indicative only): technic mechanisms 327, cars 305, buildings 244, trucks/buses/vans 211, aircraft 139, spaceships 126, trains 106, boats 64, motorbikes 57, robots/animals 70.

## 2. Measurements (zero tokens)

Steps are the real CLI commands an agent or the app calls, each a fresh process (`build`, `validate --geometry`, `render --views home front right top`). Plan generation is an in-process call to the generator.

| Request (how served) | Plan | Build | Validate | Render (4 views) | Total | Parts | Result of checks |
|---|---|---|---|---|---|---|---|
| "small red car" = grand-tourer + a red palette, no LLM | 2.4 s | 25.8 s | 19.2 s | 31.4 s | **78.7 s** | 97 | pass; 9 components, 2 warnings |
| "a truck" = tipper-truck | 0.0 | 26.4 | 21.8 | 31.8 | **80.0 s** | 113 | pass; 11 components |
| "6x8 cottage, door, windows, sloped roof" = `room`+`gable` prototype (throwaway code in `measure.py`) | 0.0 | 21.1 | 13.2 | 28.3 | **62.6 s** | 71 | pass; 3 components |
| "small white and blue starship" = retrieve reviewed B-wing, recolour 71 to white and 72 to blue | 0.0 | (n/a) | 80.9 | 63.1 | **144 s** | 434 | **fails**: 2 `technic.invalid_transform` errors from the source model, 47 components |
| "technic suspension" = retrieve independent-suspension | 0.0 | (n/a) | 51.2 | 47.0 | **98 s** | 259 | **fails**: `technic.invalid_seating` (source), 207 components |
| "technic structure" = service-platform recipe (levels 2, colour 4) | 2.7 | 19.2 | 19.1 | 32.5 | **73.5 s** | 55 | pass; 5 components |

What the same work costs in a resident process (`measure2.py`, tipper truck, parts index loaded once): import 2.5 s, load parts 1.8 s, build 4.0 s, full geometry with all contacts 14.4 s. A follow-up "make the cab roof blue" (11 `recolour` editor ops, parsed by rule): apply 0.16 s, rebuild 0.34 s, re-check 7.8 s. Retrieval: `search models` 3.1 s, `discover search ... --engine fts` 13.1 s (CLI, cold), `sets search` 3.3 s, `part` x4 5.6 s, `examples` 3.0 s. Rendering has a fixed cost: 1 view 21.3 s, 4 views about 31 s (LeoCAD); the GLB export for the web viewer is 7.6 s (`prepare-glb.sh`).

Conclusions on speed:

- Each CLI command pays 15-25 s of process start-up and library loading that the work itself does not need (build 4 s in process vs 21-26 s as a command). A resident worker brings a car from about 79 s to roughly 20 s (build 4 + geometry 14 + GLB 7.6 for the live viewer; add 20-30 s only when PNG renders are wanted).
- Even unoptimised, the deterministic path is 62-144 s against 24-35 minutes for the Sonnet 5.5 runs (about 15-30x faster; 20-40x for the generator cases) and costs no tokens against EUR 1.5-2.05 (100k-145k output tokens). Compute is about 80 CPU-seconds on one core.
- The LLM runs spent their time on large output steps, exploration and review rounds; none of these exist here. Tool time was never the bottleneck (validate 6 s, render 17 s in those runs); it only becomes the bottleneck once the LLM is removed, hence the resident-worker task.

### Looking at the results (PNGs in `screenshots/`)

- `car-red.png` (grand-tourer, red body, black roof): recognisable low coupe with arched bonnet, glazed cabin, four wheels, grey bumpers. Acceptable for "small red car". Weak points: blocky grey fascias, black roof dome reads as a hump; not a car with a 2-seat interior. The wheels are black as the owner asked; the owner's chat also asked for "2x12 red plate", which the generator ignores.
- `truck.png` (tipper-truck, orange/tan): clearly a tipper truck; cab, mirrors, bucket, rails readable. Acceptable. Proportion is long and thin (wheelbase fixed at 14 studs).
- `cottage.png` (throwaway house prototype, 8 x 6 studs, 6 courses): readable as a small house with door, window and red stepped roof, but crude: tall and narrow, the roof ends in an awkward stepped hip, only one front window (a 4-wide window plus a 4-wide door already fill an 8-wide front; `room` adds one window per side wall). "Two windows" needs a wider wall or side windows, and the "6 x 8" in the prompt is ambiguous (studs or bricks), which is exactly what the understanding step must resolve. Acceptable as a starting point, not as a finished cottage.
- `starship-white-blue.png` (B-wing recoloured): the best-looking result of the study. A 434-part detailed starfighter, convincing white and blue colouring from a two-colour remap. But it is a Star Wars ship, not a "small" starship, it is not buildable by default (validation errors inherited from the source) and has attribution and licence duties. Retrieval gives quality that no generator here reaches, at the price of fit and checks.
- `technic-suspension.png` (retrieved mechanism): excellent look, not what a casual user asked for; strict validation fails on seating.
- `technic-service-platform.png` (4-recipe structure): a frame with a plate on top, unrecognisable as a subject. The Technic recipes are teaching structures, not request-shaped generators. Ugly for any user request.

Honest summary: vehicles and the (prototype) house already look like something a user can accept; Technic recipes and strict validation of retrieved sets do not.

## 3. Pipeline design

```
user text / follow-up
   |
   v
[0 rules shortcut]  exact preset names, "make the roof blue" patterns, "again" -> spec/edits, free
   |  else
   v
[1 UNDERSTAND]  one cheap single-shot structured call (Haiku-class) -> Spec  (+ clarifying question if underdetermined)
   |
   v
[2 ROUTE]  deterministic: registry lookup by spec.family/subject -> generator | reference model | none
   |            score >= threshold: go;  tie/low: ask the user (one LLM-written question) or show 2-3 option cards
   v
[3 BUILD]  generator.plan(params) | retrieve+recolour+swap -> build_plan -> checks (geometry, connectivity, family review)
   |            bounded repair (max 3 passes, deterministic), then 
   v
[4 PRESENT]  GLB preview (7 s) + parameter form + optional single-shot LLM explanation; PNG renders on demand
   |
   v follow-up -> back to [0]/[1] with the current spec as context; [3] re-runs only changed parts
   |
   v  no generator/reference covers it, or repair fails, or the user asks for something novel
[5 FALLBACK]  full agent loop, seeded with spec + closest output
```

### 3a. Understanding step and routing

One call per user message (first request and every follow-up), rules first when trivial. The LLM never sees parts, plans or tools. Prompt = fixed cacheable prefix (role, the **registry digest**: one line per generator/reference family with its parameter names and enums, the colour-name list, the schema) + variable tail (user text, current spec for follow-ups, parts mode/budget from the chat settings). Enforced with a JSON-schema tool call so output is parseable or retried once.

**Spec schema** (`spec.v1`):

```json
{
  "type": "object", "required": ["intent", "subject"], "additionalProperties": false,
  "properties": {
    "intent": {"enum": ["new_model", "edit", "question", "other"]},
    "subject": {"type": "object", "required": ["family"], "properties": {
      "family": {"enum": ["car", "truck", "van", "motorcycle", "boat", "aircraft", "spaceship", "house", "building", "tower", "garden", "technic", "mechanism", "scene", "other"]},
      "kind":   {"type": "string", "description": "free noun, e.g. 'cottage', 'tipper truck', 'starfighter'"},
      "style":  {"type": "array", "items": {"type": "string"}, "description": "e.g. ['classic','sporty']"}
    }},
    "size": {"type": "object", "properties": {
      "scale": {"enum": ["micro", "mini", "small", "medium", "large"]},
      "studs": {"type": "object", "properties": {"width": {"type": "integer"}, "depth": {"type": "integer"}, "length": {"type": "integer"}, "height_courses": {"type": "integer"}}},
      "unit_ambiguous": {"type": "boolean"}
    }},
    "colours": {"type": "object", "description": "role -> LDraw colour name from the supplied list",
      "properties": {"body": {"type": "string"}, "roof": {"type": "string"}, "trim": {"type": "string"}, "wheels": {"type": "string"}, "glass": {"type": "string"}, "accent": {"type": "string"}},
      "additionalProperties": {"type": "string"}},
    "features": {"type": "array", "items": {"type": "object", "required": ["name"], "properties": {
      "name": {"type": "string"}, "count": {"type": "integer"}, "where": {"type": "string"}, "required": {"type": "boolean"}}},
      "description": "chimney, window x2, open cockpit, tow hook, ..."},
    "constraints": {"type": "object", "properties": {
      "parts_mode": {"enum": ["off", "prefer", "only"]}, "max_parts": {"type": "integer"}, "budget_eur": {"type": "number"},
      "must_use": {"type": "array", "items": {"type": "string"}}, "avoid": {"type": "array", "items": {"type": "string"}}}},
    "edits": {"type": "array", "items": {"$ref": "#/$defs/edit"}},
    "reference_hint": {"type": "string", "description": "named real set or franchise, if any; routes to retrieval"},
    "ambiguities": {"type": "array", "items": {"type": "object", "properties": {"field": {"type": "string"}, "question": {"type": "string"}, "options": {"type": "array", "items": {"type": "string"}}}}},
    "confidence": {"type": "number", "minimum": 0, "maximum": 1}
  },
  "$defs": {"edit": {
    "type": "object", "required": ["op", "target"], "additionalProperties": false,
    "properties": {
      "op": {"enum": ["set_colour", "set_param", "scale_param", "add_feature", "remove_feature", "swap_part", "replace_model", "undo", "revert"]},
      "target": {"type": "string", "description": "role or feature: roof, body, wheels, cab, bed, door, window, chimney, whole"},
      "value": {"type": ["string", "number", "boolean", "null"]},
      "delta": {"type": "string", "description": "bigger | smaller | longer | shorter | wider | taller | lower, with optional amount"},
      "amount": {"type": "number"}, "unit": {"enum": ["studs", "courses", "percent", "steps"]},
      "where": {"type": "string"}, "count": {"type": "integer"}
    }}}
}
```

Examples of what it emits: "Make a small white and blue starship" gives `{family: spaceship, kind: starship, size.scale: small, colours: {hull: White, accent: Blue}}`; "make the roof blue" gives `{intent: edit, edits: [{op: set_colour, target: roof, value: Blue}]}`; "bigger wheels, and add a chimney" gives two edits (`scale_param wheels`, `add_feature chimney`). "6 x 8 cottage" sets `unit_ambiguous: true` and a `ambiguities` entry with options ("6x8 studs", "6x8 bricks") unless the rules know the chat default.

**Cost and latency of the step** (priced with `PRICE_BOOK`: Haiku 5.5 $0.10 in / $0.50 out per 1M, cache read 0.1x, cache write 1.25x; Mistral Small 4 $0.15 / $0.60). Estimates, not measured; token counts are my sizing of a realistic prompt:

| Part | Tokens |
|---|---|
| fixed prefix: instructions 300 + registry digest (about 25 families x 40 tokens) 1,000 + colour list 350 + schema 650 | about 2,300 |
| first request tail: user text 30 + chat settings 40 | about 70 |
| follow-up tail: current spec 250 + text 20 + settings 40 | about 310 |
| output: new request spec | 200-300 |
| output: edit-only follow-up | 60-120 |

| Call | Haiku 5.5 | Mistral Small 4 |
|---|---|---|
| new request, cold cache: 2,370 in, 250 out | $0.00036 (0.036 cents) | $0.00051 |
| new request, prefix cached: 2,300 x 0.1 + 70 in, 250 out | $0.00015 | $0.00040 (no cache discount assumed) |
| follow-up edit, prefix cached: 2,300 x 0.1 + 310 in, 90 out | $0.00010 | $0.00039 |
| clarifying question or explanation, 2,700 in, 150 out | $0.00035 | $0.00050 |

A session of one request, five follow-ups, one clarification and one explanation costs about $0.0012 on Haiku (0.12 cents) and about $0.003 on Mistral Small; the recorded agent runs cost EUR 1.5-2.05, i.e. 1,000-2,000x more. Latency: a 300-token structured output on a small model is about 0.6-1.5 s time-to-completion (estimate; to be measured with the real providers in task 2). The dominating time is then the deterministic path (about 20-80 s), not the LLM. Guard rails: schema validation with one retry, a deterministic fallback to rules, a per-message token cap (3k in, 500 out), and a cached registry digest that is rebuilt only when the registry changes.

**Rules as a free shortcut**, never as the main path: exact preset ids from the UI, the "make X colour" pattern (`make|paint|colour the <role> <colour>`), `undo`, `again`, and repeats of an identical previous message. Everything else goes to step 1. Measured value: the shortcut saves about 0.01-0.04 cents and about 1 s, so it matters for latency of the most common edits, not cost.

**Routing** (deterministic, over the spec): registry entries declare `families`, `kinds` (synonyms), `params`, `roles`, `coverage` (what features they can honour) and a quality note. Score = family match, kind synonym match, feature coverage, size fit, constraint fit. Order of preference: generator with parameters, then curated atlas example, then reference-model retrieval (FTS5 over `MODELS_DESCRIPTIONS` plus `sets search`, filtered by part count and by `source_checks_passed`), then fallback. If two candidates tie or required features are uncovered, step 1's `ambiguities` become a form (below) or one LLM-written question; the user never gets a silent wrong model.

### 3b. Deterministic build, validation, repair

1. `generator.plan(params)` returns a plan with `role` tags on every placement (new optional field; roles such as `body`, `roof`, `wheel`, `glass`, `door`).
2. Resident worker: `build_plan` (4 s), `analyze_geometry` (14 s with all contacts; contacts can be limited for first preview), family review where it exists.
3. Bounded repair, at most 3 passes, all deterministic, each logged: (a) re-snap floating parts with `snap` candidates from `connectivity.snap_report` (the toolkit already ranks candidates and applies them via `editor.apply` `snap`); (b) for each disconnected component, snap its lowest-clearance part to the largest component; (c) drop a decorative placement that collides with a structural one if tagged `optional`; (d) if still failing, return the model with the warnings listed and offer the fallback agent. The car/truck/house above produced 9, 11 and 3 components with only warnings: "disconnected_evidence" is advisory in this toolkit (`physical_validity: not_proven`), so the repair target is **no errors**, and components are reported rather than forced to 1.
4. Retrieved references use a separate policy: pass if the curated atlas `source_checks` passed; otherwise label "source model has known issues" and never claim buildability. B-wing and the suspension fail strict checks only because of their source transforms.

### 3c. Deterministic iteration (follow-ups)

Edit intents (schema `$defs.edit` above) map to code, not text:

| Intent | Executor |
|---|---|
| `set_colour roof Blue` | `editor.recolour` on all placements with `role=roof` (measured 0.5 s plus 8 s re-check); palette-level colour sets re-generate instead |
| `scale_param wheels bigger` | switch wheel pack `classic` to `touring` (exists), regenerate |
| `set_param length +2` / "longer" | generator parameter (needs the generators to expose length/wheelbase; today constants) |
| `add_feature chimney` / "add a tree" | registry of add-on modules (`vehicle_details`, `details.py`, `architecture.tree/fence/porch`) placed with `attach` to a named anchor (`roof`, `doorstep`, `deck`); the anchors already exist |
| `remove_feature` | delete placements by feature tag |
| `swap_part` | `editor` add/delete with `catalog` symbol lookup and a part check |
| `replace_model` | back to routing with the same spec |

Unrecognised target or a feature with no module gives the user an honest "not supported by generator X; ask the builder agent?" choice, rather than a guess.

### 3d. Fallback to the full agent, cheaper

Trigger: no candidate above threshold, spec features marked `required` that no module covers, repair failure, or the user choosing "build it properly with the agent". The agent receives (1) the spec JSON, (2) the closest output as an editable plan/MPD plus its render and check report, (3) the registry entry (what the generator can and cannot do), and is told to adapt rather than research. This targets the biggest cost slice in `docs/plans/swarm-subagents/MEASUREMENTS.md` section 2 (research 39-65%): with a ready starting model, the exploration steps (part search, reference browsing, first generator draft) mostly vanish. Estimate, not measured: 30-50% lower cost for requests close to a generator, little gain for truly novel ones. The agent's final plan can be saved back as a new registry entry (3f).

### 3e. UI: options as a first-class path

- Home: a grid of **family cards** (Car, Truck, House, Spaceship, Boat, Plane, Technic) with a thumbnail each, next to the free-text box. Choosing a card opens a form generated from the generator's JSON parameter schema: design (preset), palette (presets plus colour pickers per role), size sliders (studs), features as checkboxes (chimney, porch, tree), parts mode. "Build" calls the deterministic path with no LLM at all.
- Free text goes through the understanding step; its output **fills the same form** ("I understood: House, 8 x 6 studs, red roof, door, 2 windows"), editable chips for each field, and the clarifying question is just a highlighted field. The form is the single source of truth, the chat is another way to edit it.
- Result view: 3D viewer (GLB, 7.6 s), the form on the side so changing a slider re-runs only the affected steps (0.5 s edit + checks), undo/redo from the editor history that exists already, and an explicit "Ask the agent to refine this" button (3d).
- Honest badges: "generated from a template" vs "adapted from set 6919-1" with attribution, and "source model has known issues".

### 3f. Caching and reuse

- Plan cache key: sha256(canonical(generator id, version, params)). Same spec gives the same plan; the built MPD, check report and GLB are stored beside it, so a repeated request costs 0 s and 0 tokens. Generators must be pure (they are).
- Understanding cache: key sha256(registry digest version, normalised text, spec-in-context); identical messages reuse the spec.
- Prefix cache for the LLM call (registry digest first).
- Sub-assembly library: every module that passes checks (wheel axle, cab, porch, chimney, wing) is stored as a section with its anchors, envelope, colour roles and source; indexed by FTS over names and descriptions (the same mechanism as `SUBMODELS_DESCRIPTIONS`). Agent runs that end well contribute theirs. Library entries are what the add-feature edits and the seeded fallback draw from.

## 4. Coverage estimate

Sample: the owner's recorded prompts (3 distinct first messages in `app/data/chats`: red car with four black wheels; 6 x 8 cottage with a door, two windows and a sloped roof; small white and blue starship), the 4 home-page suggestions (red car, 6 x 8 cottage, spiral staircase around a stone tower, harbour crane with long boom and cargo dock), and the example families. The first two home suggestions repeat the owner's prompts, so there are 6 distinct requests.

| Request | Today, deterministic only | After the proposed work |
|---|---|---|
| small red car | **served** (grand-tourer + red palette, needs a palette-from-colour helper; measured) | served, with size and wheel options |
| cottage 6 x 8, door, 2 windows, sloped roof | **partial**: no CLI generator; prototype from existing helpers works (71 parts, crude) | served by a parametric `house` generator |
| small white and blue starship | **served by retrieval** (B-wing recolour; impressive, not small, source errors) | served by retrieval plus a small parametric `starfighter` generator |
| spiral staircase around a tall stone tower | not served (no tower or stair generator; `stairs` is a straight 3-step helper) | partial with a `tower` generator (round or square, stair as option) |
| harbour crane with long boom and cargo dock | not served (an `atlas-crane` example exists but is a fixed study, no dock) | partial: crane study retrieval plus a quay base |
| "a truck" | **served** (tipper, pickup, van) | served with length and colour options |

Counting: 2 of 6 clearly served today (33%), 1 more partially; about 5 of 6 with the first generators, leaving the tower and crane as partial or agent work. Extrapolating with the keyword frequencies of the 1,821 reference descriptions (cars 17%, technic 18%, buildings 13%, trucks 12%, aircraft 8%, spaceships 7%, trains 6%, boats 3.5%), this is a small sample and the estimate is a judgement, not a measurement:

- Today: about 30% of plausible casual requests (cars, trucks, vans, bikes, small boats and jets, plus anything near the 3 spaceship and 21 building examples) get an acceptable deterministic answer; about 25% of the rest get a retrieved but ill-fitting model.
- After tasks 3 and 4 (house, tower/castle, boat, plane, spaceship generators, retrieval policy): about 60-70% of casual requests, i.e. most vehicles, houses and spaceships. The remaining 30-40% (novel subjects, specific real sets, scenes combining several things, mechanisms with a stated function, animals and figures, trains) stay with the agent, seeded by the closest output.
- Technic and mechanism requests: only retrieval of the 16 studies; the existing structural recipes are not request-shaped. Counted as agent territory.

Generators to add first, ordered by (share of requests x ease): (1) parametric `house` from `room`/`gable`/`porch` (about 13% buildings, helpers exist, 1-2 days); (2) vehicle parameter exposure and colour-from-name palettes (cars, trucks: about 30% with the 5 road designs, existing code, most of the gain per day); (3) `spaceship` generator (starfighter/freighter with hull, wing, engine count, canopy; 7% share, the one family with no generator and the owner's recorded prompt); (4) `boat` and `plane` families (about 11%, partly done in `vehicle_families`: launch and jet exist but are fixed); (5) `tower/castle` (spiral stair, battlements) for the staircase prompt; (6) trains. 

## 5. Implementation plan

Six tasks. 1, 2, 3, 4 can start in parallel against a one-page contract (the `spec.v1` schema above plus the registry entry format); 5 follows 3 (roles); 6 follows the contract and consumes 2 and 3 incrementally.

**Task 1. Resident build worker and cache** (parallel).
Files: new `ldraw_tools/pipeline.py` (build, check, convert in one process with parts loaded once), `app/web/backend/` worker integration (a long-lived subprocess or thread pool; render pool of LeoCAD calls), content-hash cache under the chat output dir; GLB as the default preview.
Acceptance: red car end to end (plan, build, geometry, GLB) under 25 s warm and under 1 s on a cache hit; same MPD bytes as the CLI path; no change to CLI behaviour; unit test for cache key stability. Measured baseline: 79 s.

**Task 2. Understanding step and rules shortcut** (parallel).
Files: `app/web/backend/understand.py`, `spec.v1.schema.json`, registry-digest builder, golden set of about 60 prompts (the 3 owner prompts, 4 home suggestions, follow-ups) with expected specs, mocked-provider tests, token and cost logging through `usage.py`/`PRICE_BOOK`.
Acceptance: with a mocked provider every golden prompt validates against the schema and malformed output is retried once then falls back to rules; prompt prefix is byte-stable (cache friendly); on a real provider (run once by the owner) at least 90% of golden specs match on family, colours and edits, with median cost below $0.0005 and median latency reported.

**Task 3. Generator registry and parameter exposure** (parallel; top priority).
Files: new `ldraw_tools/generators/` with a uniform interface (`id`, `title`, `families`, `kinds`, JSON-schema `params`, `roles`, `plan(params)`), wrappers for the 7 vehicles (expose length, wheelbase, wheel pack, palette-from-colours), reference recipes, building `house` (width, depth, courses, door side/style, window count and sides, roof tiers and colour, porch, chimney, dormer) built on `architecture.py`, then `spaceship` (starfighter, freighter), `boat`, `plane`; `role` tag in plan.schema.json (optional); `generate`/`list` CLI subcommands.
Acceptance: each generator builds with no validation errors for its documented parameter ranges (a property test sweeps the corners); house 8 x 6 with door and two windows passes and renders recognisably; palette from names ("red", "white", "blue") works for every road design; a registry digest function lists all entries in under 1,500 tokens.

**Task 4. Retrieval, recolour and swap for reference models** (parallel).
Files: new `ldraw_tools/retrieve.py` (FTS plus set catalog plus part-count/size filters and `source_checks` status), colour-remap by role (cluster dominant colours of the model, map to spec colours), `extract` + `editor` based sub-assembly swap, attribution manifest, report of known source issues.
Acceptance: "white and blue starship" returns B-wing with the remap applied in under 15 s without the CLI start-up cost; attribution and licence travel with the result; a model with unresolved source errors is labelled, not hidden; 20 queries produce a ranked shortlist whose top-3 contain the expected model for at least 15 of them.

**Task 5. Edit-intent executor and bounded repair** (after task 3 roles; editor-only part can start now).
Files: new `ldraw_tools/edits.py` (intent to `editor` ops or generator-param change; add-feature module registry with anchors), `repair.py` (the 3-pass loop over `connectivity.snap_report`), tests.
Acceptance: the five common follow-ups ("make the roof blue", "bigger wheels", "longer", "add a chimney", "remove the porch") run on the house and truck with no LLM, each under 12 s warm including checks; unsupported intents return an explicit "not supported" result; repair fixes an injected floating part in at most 3 passes or reports failure without altering unrelated placements.

**Task 6. UI: option form, spec chips, fallback hand-off** (starts from the schemas with mock data; integrates 2 and 3 as they land).
Files: `app/web/frontend/src/pages/Home.tsx` (family cards), new `OptionsForm.tsx` generated from generator parameter schema, spec-chip editor, result badges, "Ask the agent to refine" action that seeds `agent_loop.py` with spec + closest plan + check report.
Acceptance: a user builds a red car and a cottage with the form alone (no free text, no LLM call); typing free text fills the same form; changing a slider re-runs only the changed steps; the seeded agent run receives the plan and spec in its first message; screenshot tests for the form on desktop and phone width.

Order of value: 3 (largest coverage gain), 1 (largest speed gain), 2 (the path for free text), then 4, 6, 5.

## 6. Risks and honest caveats

- Generator quality is the product now: the recorded agent runs produced a bespoke ship; the deterministic ship is a recoloured existing model and the cottage is a prototype. Visual review of every generator output (renders opened by a person) must stay in the acceptance tests.
- Constants such as vehicle length and wheelbase were designed per model; exposing them needs per-design validity ranges, otherwise sliders produce invalid cars.
- Strict validation fails on some retrieved references; the policy above avoids false claims but a user may see "issues" on beautiful models.
- Validate and render timings above include CLI start-up in a Docker container on a Windows host; absolute numbers will differ, but the ratios (80 s vs 24-35 min, 4 s vs 21-26 s for the same build) are robust.
- LLM cost and latency of the understanding step are estimates from token sizing and the price book, not live calls.
- Parts availability (the owner's chat 1 notes missing black wheels and a 2x12 red plate) is not handled by any generator; `parts_mode` needs a deterministic substitution step (existing `inventory.check_model` substitutions) in task 3 or 5.
