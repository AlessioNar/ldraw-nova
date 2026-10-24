# Natural language in Open Brick Designer: a critical review of the deterministic-first pipeline

Analysis and measurements only. No feature code changed. Branch `nl-review` (from master). Everything below was traced in code and measured in one throwaway container of the `ldraw-nova-app` image (worktree `app/` and `ldraw_tools/` mounted read-only; the running app, its volumes and port 8765 were not touched).

Scripts and raw results sit next to this file: `prompts.json` (54 prompts), `measure_nl.py` -> `results-rules.json`, `results-golden.json`, `results-colours.json`; `probe_router.py` -> `results-probe.json`; `measure_journey.py` -> `results-journey.json`; `prompt_size.py`.

**Live LLM pass: skipped.** There is no model API key in my environment (only `CLAUDE_CODE_EXECPATH`; reading the app's config volume was not allowed). So every "LLM" statement below is either read from code/prompt or tested by feeding hand-written, LLM-shaped `spec.v1` dicts to the router (`probe_router.py`). What the real Haiku call does with the 54 prompts is **not measured**; the first thing to do with a key is `python understand_eval.py --live` (about 70 calls, a cent or two) plus a run of `prompts.json` (54 calls, well under EUR 0.05).

## 0. Verdict in one page

The architecture is right (understand with a cheap call, route by registry, generators build, paid agent only on consent) and the engine under it is quick: a text edit lands in the draft in about 0.5 s warm, a hint costs 0.2 ms. But as a *conversation* it is not there yet. Three things dominate:

1. **Edits only exist for builds that still have a live generator source, and that source is both too narrow and too sticky.** On every AI-built, imported or hand-edited build the person gets nothing useful from "make the roof blue" (hint: "AI build EUR 1-2"; submit: "There is nothing to change yet"), while on a build that once was generated, the stale generator params silently replace the AI's or the person's work.
2. **The pipeline is LLM-first in practice.** The "rules shortcut" answers 10 of 74 golden prompts and 3 of my 54 (all three are the one-role-one-colour pattern). 51 of 54 realistic prompts would pay an LLM call, including "make it longer", "add a chimney", "two more windows". That is cheap (about EUR 0.0003 a call) but it is not what the product promise says, and without a key the app degrades to a keyword reader that silently drops half of a request.
3. **Silent wrongness and silent drops.** The router picks a generator by substring match ("lighthouse" -> house, "scarecrow" -> car), ignores the `edits` of a new-model spec and the extra edits next to a `replace_model` without a note, never passes the spec to the agent on handover, and the notes that explain assumptions/clamps are thrown away on the pages where people first create builds.

## 1. How a request flows today (map)

```
Home / NewBuild DescribeBox            Workspace bottom bar (DescribeBar)            Workspace AI tab (Composer)
  typing: GET /api/describe/estimate     typing: GET /api/describe/estimate            plain chat, no fast path
          ?text&generator?                       ?text&generator=<id|none>             (agent loop, paid)
  submit: POST /api/describe             submit: POST /builds/{id}/draft/describe
          {text, build_id, current?}             {text}  (server fills current from build.source)
          (no previous_text sent)                (no previous_text, no params from UI)
        \                                      /
         fast_path.describe(text, current, previous_text)
            understand.rules_shortcut()  -- presets, undo/revert, "make the <role> <colour>", "again"   (free, regex)
            else understand.model_params() -> ONE litellm call (Haiku 5.5 default), forced tool emit_spec
                 prefix: instructions + generator digest + 42 colours (cached) | tail: CURRENT_SPEC? + REQUEST
                 invalid -> one retry -> heuristic_spec() keywords ("rules_fallback")
            fast_path.route(spec, current)
                 new_model -> _unsupported_reason() -> choose() -> params_from_spec() -> clamp()
                 edit      -> _route_edit() -> edit_params() (generator PARAMS only) -> clamp()
                 question/other -> clarify
         -> {route: generator | edit | needs_ai | clarify, notes, changes, params, estimate}
Home:       generator -> POST /builds/{id}/generate (geometry check, new version)   needs_ai -> confirm -> agent chat with raw text
Workspace:  generator|edit -> _regenerate(): worker build (no check) -> replace_plan (one undo step)   needs_ai/clarify -> inline line, "Ask the AI" moves raw text to AI tab
```

Code: `app/web/backend/understand.py`, `fast_path.py`, `generator_bridge.py`, `routers/describe_api.py`, `routers/workspace_api.py:241-259`, `routers/builds_api.py:227-252`, `ldraw_tools/generators/*`, `frontend/src/components/DescribeBox.tsx`, `frontend/src/components/workspace/BottomBar.tsx`.

## 2. The journeys as they behave

Time and money legend: LLM call = about 2,520 input tokens (5,710 chars prefix + 3,676 chars tool schema + 190 tail, `prompt_size.py`) and about 100-250 output tokens; at Haiku 5.5 prices in `model_catalog_data.json` (0.10 / 0.50 USD per million) that is about EUR 0.0002 with the prefix cached and about EUR 0.0004 cold, 0.6-1.5 s (estimate from the proposal; not measured here). Worker numbers are measured (section 3).

### J1. New build by text ("a small red car", "a cosy little blue house")

```
Person        Home DescribeBox       /api/describe          Haiku           builds_generate        worker             Workspace
  | type         | hint (rules,0.2ms)    |                      |                 |                    |                   |
  |--submit----->|--POST describe------->| rules_shortcut: no   |                 |                    |                   |
  |              |                       |--emit_spec call----->|  ~1 s, EUR 0.0003                    |                   |
  |              |                       |<--spec---------------|                 |                    |                   |
  |              |                       | route+clamp (ms)     |                 |                    |                   |
  |              |<--{generator,params,notes}                   |                 |                    |                   |
  |              |--POST /builds (create) then /generate-------------------------->|--build+geometry--->| 11-19 s warm      |
  |              |                       |                      |                 |<--mpd, warnings----| (17-67 s cold)    |
  |<-------------------------------------------- navigate /build/id, notes DROPPED ----------------------------------------->|
```

Where time goes: of a warm 15 s (car) 14.9 s is the geometry/connection check; the plan itself is 0.3 s. The person looks at a spinner for the check of a model the generator test suite already validates. Cold worker: 17.4 s (starfighter), 33.8 s (car), 66.7 s (house), mostly lazy first-use initialisation inside `build`, not the parts load (3.7-6.3 s).
Money: about EUR 0.0003. Confusion: `DescribeBox.onOutcome` receives `summary` (clamps, "read the size as studs", "tree left out", "no understanding model answered") and `Home.tsx:112-118` / `NewBuild.tsx:59-63` never show it. The person lands on a model that silently differs from the text.
Quality gap: "a cosy little blue house" gives `wall_colour: blue`, roof still dark red, trim white, door reddish brown (`results-probe.json`, "blue house"): the router sets only the roles the text names. "Cosy" and "little" carry no parameters (little = default size, `SCALE_SHARE["small"] = 0.0`).

### J2. Change by text on a generator build ("make the roof blue", "add a chimney")

```
Person    DescribeBar            draft/describe            fast_path                 worker              canvas
 | type      | hint (rules)           |                         |                        |                   |
 |--Enter--->|--POST {text}---------->| current = build.source  |                        |                   |
 |           |                        | rules_shortcut hit? ----> edit (0 EUR, ms)       |                   |
 |           |                        |   else Haiku call (~1 s, EUR 0.0003) w/ CURRENT_SPEC = {family, colours, size}
 |           |                        | edit_params -> clamp    |                        |                   |
 |           |                        |--_regenerate()----------------------------------->| build, no check 0.3-0.5 s warm
 |           |                        |<--replace_plan: ONE undo step; replaced_manual_edits flag
 |           |<--toast "Applied: roof colour: dark red to blue; <notes>" [Undo]
```

Measured: hint 0.2 ms median (79 ms worst, first call), rules-path decision 1-11 ms, worker regenerate 0.3-0.5 s warm, 33 ms on a cache hit. So with the rules shortcut the whole edit is well under a second; with the LLM about 1.5 s. This path is the good news of the review.
Where the person is left confused: (a) the line under the bar reads "**Fast path: Fast path: free, ~15 s. Free, about 1 s.**" (`BottomBar.tsx:45` prepends "Fast path:" to a server hint that already starts with it, and the 15 s is the version-build estimate, not the 0.5 s draft regenerate); (b) the hint never says *what* will change, although the engine knows (the mockup promised "roof colour Blue to Red"; `rules_guess` drops `changes`); (c) with a hand-edited draft the edit is applied on the **stale generator params** and the person's manual edits are replaced, with only a post-hoc toast (see J3).

### J3. Change by text on a non-generator build (AI-built, imported, hand-edited)

Two different failure modes depending on the history of the build.

**3a. Build never had a generator** (`build.source.generator` unset, so `current = None`):

```
Person   DescribeBar          draft/describe        fast_path
 | "make the roof blue"
 |          hint: GET estimate (no generator) -> route needs_ai  -> "No rule fits this. The AI can try: ~EUR 1-2, 15-30 min."   <-- wrong and scary
 |--Enter-->|--POST----------->| current=None, rules_shortcut needs current_spec -> None  -> Haiku call (EUR 0.0003, no CURRENT_SPEC in the prompt)
 |          |                  | LLM says edit -> _route_edit: "There is nothing to change yet. What would you like to build?"  (fast_path.py:437-438)
 |          |                  | (or says new_model -> a NEW generator model REPLACES the draft plan, one undo step)   [inferred, not measured]
 |<--clarify "There is nothing to change yet"  on a build with 112 parts
```

Measured offline (`results-rules.json`): "make the roof blue" without a generator is needs_ai in the hint; all 4 non-generator edit prompts are needs_ai in the hint (EUR 1-2 quoted for recolouring a roof) and clarify with the generic "What would you like to build? For example a small red car" at submit when no model answers. Even in the best case the person is sent to the paid agent for a recolour.
Root cause: `understand.rules_shortcut` requires `current_spec` (`understand.py:255-257`), `current_spec` is derived only from generator params (`fast_path._current_spec`), and `edit_params` can only write generator params. Yet generator plans carry a `role` on every placement (`tag_roles`) and the editor already has a deterministic `recolour` op that accepts a batch of entries; PROPOSAL.md section 3c planned exactly "recolour all placements with role=roof (0.5 s)". It was never built.

**3b. Build that once had a generator, then was changed otherwise** (an AI proposal accepted, a hand edit saved, an import over it): `builds.update(... source=...)` is only called from `_regenerate` and `builds_generate` (`workspace_api.py:228`, `builds_api.py:244-247`). Nothing clears or marks `source.generator` when a proposal is accepted or the person edits. So `_generator(record)` (`workspace_api.py:132-134`) keeps returning the original generator and its **original params**, and "make the roof blue" regenerates the *original house* with a blue roof, replacing the AI's or the person's model as one undo step. The toast says "Applied" (or "Regenerated. Your manual edits were replaced.") after the fact. This is the more dangerous case because it looks like it worked.

### J4. Vague request ("something nice for my desk", "surprise me")

Hint: "something nice for my desk" -> needs_ai with "AI build: ~EUR 1-2, ~15-30 min" (`fast_path.py:509-510` converts any unplaced 3+ word new-model request from clarify to needs_ai). "build something" and "surprise me" -> clarify (blank hint). "a cool thing for my son who likes dinosaurs" -> needs_ai.
With a model the spec has `ambiguities`, but the router uses them only for `clarify` (`_clarify`, `fast_path.py:400-403`), and `_route` reaches clarify for a new model only when `family == other`, no kind and no features (`fast_path.py:428-430`); otherwise it goes straight to needs_ai. "Something nice for my desk" is intent new_model, family other, no kind but with a `desk` feature or none: depends on what Haiku emits; the rules hint conversion shows the design intent: *unplaceable means expensive*, not *ask*.
Fix direction: a clarify with options costs EUR 0.0003; an agent run costs EUR 1-2. The default for "I can't place this" must be to ask (with chips such as "a desk lamp", "a small plane", "a tiny house"), and needs_ai only for a *named, placeable but uncovered* subject.

### J5. Out of coverage ("a helicopter", "a castle", "a train")

Measured with the rules only: helicopter, tower, castle, windmill -> needs_ai; train, tree, sofa, Ferrari, robot -> needs_ai in the hint (clarify at submit without a model). The hint quotes "AI build: EUR 1-2, 15-30 min" (default estimate until 3 finished chats exist, then the quartiles of the owner's past turns, so typically the same numbers). The person is told to expect the cost of a full agent run for a "tree".
On confirm the agent starts with **only the raw sentence**: `Home.tsx:123-127`, `NewBuild.tsx:64-68`, `useWorkspace.ts:237` (`askAi` sets seed text only). The `spec` that `needs_ai` returns (family, kind, colours, size, features), the "nearest generator" and the registry entry are never forwarded; PROPOSAL.md section 3d promised "seeded with spec + closest output, 30-50% cheaper" and that seeding does not exist. The system prompt's continuity block (`system_prompt.py:78-100`) tells the agent the build title and latest version file and nothing about generator params, roles or spec.

### J6. Multi-intent ("a red car and make it longer", "make the roof blue and add a chimney")

| Text | What happens (rules path; the LLM path follows from the router code, probed with LLM-shaped specs) |
|---|---|
| "a red car and make it longer" | new car, body red; **"longer" is dropped with no note** (`route` for `new_model` never reads `spec.edits`, `fast_path.py:432-433`; probe "multi: red car + longer" -> `notes: []`). |
| "make the roof blue and add a chimney" (rules-only reading) | only the chimney is added; **the roof recolour is lost**, hint says "Fast path". (`heuristic_spec` handles only `add/remove` when a build is open, `understand.py:288-295`.) With a model both edits would be emitted and applied. |
| "make it a truck and paint it green" | `replace_model` wins; the colour edit is dropped silently (`fast_path.py:443-451`, probe -> `params {}`, `notes []`). |
| "roof blue and bigger wheels" on a house | roof applied, note "cannot change the wheels of this design" appears (good) but only inside the toast. |
| "a house with a garden and a tree next to it" | house; tree and garden: with a model, note "tree is not supported by the house design; left out" (probe). The offline reading does not even note it. |
| "make it a truck instead" (hint, car open) | clarify; the same phrase **without** a generator hints "generator" (it would start a new truck). |

There is no splitting stage: one spec per message, edits applied in a single `edit_params` pass, unsupported ones either noted (edit route) or dropped (new_model and replace routes).

### J7. Follow-ups ("a bit less", "the other way", "now do the same in red", "again")

The understanding call sees: the registry digest, the colour list, `CURRENT_SPEC` (family, kind, colours, width/depth/length only; no windows, chimney, porch, tiers, courses, door), and the single request. It never sees previous turns, the chat, the last answer, or the current selection in the canvas. The `previous_text` parameter exists and powers "again" but **no caller sends it**: `DescribeBody` accepts it, `wsApi.ts:28` and `workspace_api.py:253` do not pass it, and `api.ts:406-407` does not either. So "again" and "do it again" work only in the unit tests. Offline, all five follow-up prompts clarify with a blank hint; with a model "a bit less" would have no referent (it does not know that the last change was "make it bigger"). "Now do the same in red" cannot be answered without the last turn.

## 3. Measurements

### 3.1 Golden set and rules (zero cost; `results-golden.json`)

| Path | n | answered by | full match (all exercised dimensions) | intent | family | colours | edits |
|---|---|---|---|---|---|---|---|
| rules shortcut, else keyword heuristic | 74 | 10 rules shortcut, 64 heuristic | 46 / 74 (62 %) | 70 % | 90 % (38/42) | 89 % (17/19) | 36 % (10/28) |
| keyword heuristic only | 74 | 74 heuristic | 45 / 74 (61 %) | 69 % | 90 % | 89 % | 32 % (9/28) |

The shortcut answers only: the 2 owner presets and 2 home presets, the 3 single-role recolours, undo, revert, "again". It misses "make the car yellow" (`car` is not in `ROLES`), "make the roof red and the door white" (the regex is anchored to one pair), and every size or feature edit ("make it longer", "two more windows", "add a chimney" are not even attempted by rules in the shortcut; the hint reads `add ...` through the heuristic). The heuristic misses questions, greetings, "Millennium Falcon", "technic gearbox" and 28 of 28 edit phrasings except the colour and add/remove forms: it is a safety net, not a path. Colour vocabulary: the understanding prompt offers 42 names; **2 are unknown to the generators** ("Light Nougat", "Medium Nougat", `results-colours.json`), giving the note "unknown colour ... ignored" (probe: "pink car, light nougat wheels").

### 3.2 Realistic prompt set (54 prompts; `results-rules.json`)

`ctx` = what the UI passes: `new` (no build), `house`/`car` (generator build, defaults), `nongen` (no generator).

| Category (n) | Hint route (what the user sees while typing) | Rules-only decision at submit |
|---|---|---|
| new builds of known kinds (8) | generator 8/8 (conf 0.5-0.7) | generator 8/8 |
| out of coverage (12) | needs_ai 9, generator 3 | needs_ai 4, clarify 5, generator 3 |
| vague (4) | needs_ai 2, clarify 2 | clarify 4 |
| edits on generator builds (12) | edit 5, clarify (blank) 7 | edit 5, clarify 7 |
| same edits, generator **not** passed | needs_ai 11, generator 1 | n/a |
| edits on non-generator builds (4) | **needs_ai 4 (EUR 1-2 quoted)** | clarify 4 |
| multi-intent (3) | generator 2, edit 1 | same, parts dropped |
| typos (3), Italian (3) | needs_ai 4, clarify 2 | clarify 6 |
| follow-ups and chit-chat (5) | clarify 5 | clarify 5 |

- **Would call the LLM at submit: 51 of 54.** Only `make the roof blue`, `paint the door yellow`, `make the wheels red` (generator build open) skip it.
- **Hint and submit disagree for 15 of 54 prompts** (hint says needs_ai/"EUR 1-2", the real decision is clarify or, with a model, probably a generator): all typos, Italian, vague, non-generator edits. The hint is rules-only by design, but it speaks the language of cost for things the EUR 0.0003 call would place.
- **Wrong or doubtful generator picks** (rules reading): "a lighthouse" -> house; "a cargo ship" and "a green sailing boat" -> `harbour-launch` (a small motorboat); "a red fire truck" -> `tipper-truck` (desert palette, red body); "a big yellow school bus" -> `delivery-van` length 28 (acceptable); none of these says that the design is a stand-in.
- Hint latency: median 0.22 ms, max 79 ms (first call). Describe decision without a model: median about 1 ms, max 10.7 ms.
- Coverage today (11 generators): house (width, depth, courses, windows, roof tiers, door side/style, porch, chimney, dormer, 6 colours), 4 road vehicles (length, wheelbase, 6 colours, palette), motorbike, launch, jet (colours only, fixed size), starfighter (length, wingspan, wing style, engines, 5 colours), arcade bay and street lantern (small accent pieces). Not covered by any generator: helicopter, tower/castle/lighthouse/windmill, train, ship (beyond the launch), tree, furniture, robots/figures, animals, named sets. The registry's `coverage` for the house lists "two storeys" but `params_from_spec` says "two storeys is not supported by the house design; left out" (probe), so the registry overstates.

### 3.3 Generator-bound probes with LLM-shaped specs (`results-probe.json`)

- **Substring kind matching** (`fast_path.py:129`: `kind == k or kind in k or k in kind`): lighthouse, treehouse, doghouse, warehouse -> `house`; scarecrow, caravan, carriage, cart -> `grand-tourer`; jetty -> `courier-jet`; shipyard -> `harbour-launch`; "bus stop" -> `delivery-van`; spaceship -> starfighter. (Probed with `family: other`; when the model supplies a family the family score often masks it, but "lighthouse" with family tower still resolves to the house because the kind hit is worth 4 against 2 for the family.)
- Clamping works and is noted (house 40 x 40 -> 24 x 20 with two notes; 7 -> 8). "6 x 8" with `unit_ambiguous` is **resolved silently as studs** with a note; the model's own `ambiguities` question is ignored for generator routes (`route` only calls `_clarify` for intents question/other or an unplaceable family). Size words are mapped by `SCALE_SHARE` over `minimum..maximum` of the first size param found; "large house" = 20 x 16, "tiny" = width 6 (depth default).
- Colour harmony: none. `colours` only touch named roles (`params_from_spec`).
- Required features the generator lacks (balcony) -> needs_ai; optional ones -> noted and left out.

### 3.4 Journey timing with the real resident worker (`results-journey.json`, 8 cores, software only, container)

| Generator request | Describe (rules/offline) | Worker cold: first build (incl. process start) | Same params again (cache hit) | Warm, changed params, no check (draft/regenerate) | Warm, new version with geometry check (`builds/generate`) | Warm, check + GLB |
|---|---|---|---|---|---|---|
| "a small red car" (grand-tourer) | 1.4 ms | 33.8 s (plan 6.5, load 6.3, build 12.5) | 0.03 s | 0.32 s | 15.2 s (check 14.9) | 8.3 s (check 8.1, GLB 5.9) |
| "a 10 x 8 house with a chimney and a porch" | 0.9 ms | 66.7 s (build 53.6) | 0.01 s | 0.45 s | 19.5 s (check 19.1) | 11.6 s (check 11.2, GLB 8.5) |
| "a white and blue starship" (starfighter) | 0.8 ms | 17.4 s | 0.02 s | 0.50 s | 11.3 s (check 11.0) | 3.8 s |

Add the LLM call (1-1.5 s, est.) for any prompt that is not a shortcut. Reading: in a warm app a text edit is under 2 s end to end, a new generator build 12-21 s (almost all validation), a cold worker 17-67 s once. Every generator output carries the same two warnings ("Connection analysis finds multiple groups", "Some parts lack rectangular body checks") which become the version's warnings, so a warning that is always there carries no information. The Home path (`builds_generate`, geometry on) pays the check synchronously; the workspace path (`_regenerate`, geometry off) does not, which is why J2 feels instant and J1 does not. `fast_path.SECONDS` (house 15, car 20) estimates the former and the bar says "about 1 s" for the latter.

### 3.5 Cost

| Item | Cost |
|---|---|
| rules shortcut / hint / generator build | EUR 0 |
| understanding call (Haiku 5.5, about 2.5k in, 100-250 out) | about EUR 0.0002 (cached prefix) to 0.0004 (cold) |
| a whole session of 1 build + 10 edits, all via LLM | about EUR 0.003 |
| AI agent turn (history of the owner's chats, `ai_estimate`) | EUR 1-2, 15-30 min |

The understanding spend is not persisted anywhere: it is returned in `understanding_cost_eur` and logged at INFO; nothing records it per build or per day, so the "how much does the cheap step cost me" question cannot be answered later.

## 4. Engineering critique

**Architecture seams.** Four vocabularies describe the same thing: spec.v1 `colours{role}` and `edits{target}` (free strings), the generators' parameter names (`roof_colour`, `wall_colour`, `body`), the plan's `role` tags (`wall`, `roof`, `window`, `porch`...) and the editor ops (`recolour` by entry id). `fast_path.ROLE_PARAMS`/`FEATURE_WORDS` and `understand.ROLES` hand-map the first to the second; nothing maps to the third. The cleanest seam is already in the data: the plan's `role`. An edit should resolve to *a set of plan entries* (by role, by selection, by geometric class) and be executed as editor ops; the generator-parameter route is then just the preferred way to realise it when a live generator source exists *and matches the draft*.

**Two sources of truth for "what is this build".** The build's `source.generator/params` (regenerate input) and the draft's plan (what is on the canvas) diverge as soon as anything but a regenerate touches the plan (accept, manual edit, save as version). Nothing tracks the divergence (J3b). A content hash of the plan produced by the last generate, stored with the source, would let `draft/describe` know whether the params still describe the canvas.

**Rules vs LLM are not layered, they are alternatives.** `rules_guess` (the hint) uses `heuristic_spec` + `route`; `describe` uses `rules_shortcut` then the LLM; the offline fallback uses `heuristic_spec`. Three readers with different coverage: the hint says things the submit will not do (15 of 54 disagree). The estimate endpoint cannot see the build's params (it sends `params: {}`), so it cannot say what will change.

**Duplicated logic.** Colour vocabulary in three places (`understand.COLOURS`+`COLOUR_ALIASES`, `generators/colours.NAMES`, `fast_path._norm_colour` folding), role words in three (`understand.ROLES`, `fast_path.ROLE_PARAMS`, generator `roles`), family words in two (`understand._FAMILIES`, `fast_path.STATIC_FAMILIES` + registry families). Drift is already visible (Nougat). Role tagging in the generators is keyed by colour (`house.py:111-118` dict literals; `vehicles.py:59-69` `by_colour`), so two roles that share a colour (white walls and white trim, a one-colour car) collapse into one role: any role-based edit built on these tags needs per-placement roles assigned at construction, not recovered from colours. Vehicle `roles` is one shared tuple for all seven designs, including the ones with no roof or wheels.

**Schema mismatches.** `spec.v1.edits[].target` is a free string; `edit-intent.v1` is only checked when `intent == edit`. `unit_ambiguous` and `ambiguities` are produced and then ignored by the router for generator routes. `features[].required` is the only feature field the router reasons about. `constraints.parts_mode` forces needs_ai for "only parts I own", although the draft/regenerate path now has a parts check; the generator could build and then report what is missing. `confidence` from the model is used only to label the hint softness.

**Errors and notes surfacing.** Notes exist and are good ("width 40 adjusted to 24 (6-24, even)") but: dropped on Home/NewBuild (`summary` unused), transient in the workspace toast, never persisted on the version, never shown as chips the person can undo. `understanding_errors`, the retry count and `rules_fallback` appear only in the JSON.

**Determinism and testability.** Generators and `route`/`clamp` are pure and well covered (`test_fast_path.py`, golden set), which is the strong part. Untested: any real-model behaviour (the golden set is run live "once, on purpose"), the UI hint wording, the stale-source scenario (J3b), and the hint/describe agreement. There is no regression fixture for "what did the user type and what did we do", so quality cannot be tracked between releases.

**Latency.** Text edits are fast. The slow parts are avoidable: synchronous geometry validation on new builds (95 % of 15 s), a cold worker (17-67 s after any restart or crash; start it at app launch with a `load` op), and the LLM call (about 1 s) for edits that a handful of regexes would cover.

## 5. Findings ranked by impact for the owner

| # | Finding | Evidence | Impact | Fix (wave) |
|---|---|---|---|---|
| 1 | Edits on any non-generator build (AI, import, hand-edited) cannot be done deterministically; the person is told "AI build EUR 1-2" or "nothing to change yet" for a recolour. Generators already tag roles; the editor already has `recolour`. | `understand.py:255-257`, `fast_path.py:437-438`, `edit_params` writes params only; hint: 4/4 non-generator edits needs_ai (`results-rules.json`); `tag_roles` in every generator; PROPOSAL 3c never built | Highest: the workspace is "edit a build in 3D", and every build that is not a pristine generator output loses the headline feature | W1 role/selection edits on any build |
| 2 | Stale generator source: after an accepted AI proposal or manual edits, a text edit regenerates the old generator params and replaces the person's work, reported only afterwards | `workspace_api.py:132-134, 212-231, 241-259`; `source` written only at `workspace_api.py:228` and `builds_api.py:244-247` | High: silent loss of work (one undo step saves it, if noticed) | W1 divergence guard + preview |
| 3 | Handover to the paid agent passes only the raw sentence; spec, nearest generator and constraints are discarded | `Home.tsx:123-127`, `NewBuild.tsx:64-68`, `useWorkspace.ts:237`; `system_prompt.py:78-100` | High: the promised 30-50 % agent saving is not realised; the agent re-researches what the cheap call already understood | W1 seed the agent |
| 4 | "Can't place it" means "ask the person to spend EUR 1-2": no clarify-first policy, no chips; hint quotes the expensive route for typos, Italian, vague and non-generator edits that a EUR 0.0003 call would place | `fast_path.py:428-431, 509-510`; hint vs submit disagree on 15/54 prompts | High for trust: the first impression of a normal sentence is a price tag | W1 clarify-first + hint honesty |
| 5 | No conversational context: the call sees one sentence plus a thin `CURRENT_SPEC`; `previous_text` never sent; the spec omits windows, chimney, porch, tiers, courses, door | `understand.py:147-158`, `wsApi.ts:28`, `api.ts:406`, `fast_path.spec_from_params` | High: "a bit less", "the other way", "same in red", "again" cannot work; "remove the chimney" cannot know there is one | W1 context |
| 6 | The pipeline is LLM-first: rules cover 10/74 golden, 3/54 realistic; "make it longer", "add a chimney", "two more windows" all pay a call and a second of latency; offline fallback silently drops half a request | `understand.py:210, 246-262, 282-319`; section 3.1 | Medium-high: cost is tiny, but latency and the offline degradation are real, and the story "deterministic first" is not true yet | W2 broaden the rules with the role/edit vocabulary |
| 7 | Silent drops and substring picks: new-model `edits`, extra edits next to `replace_model`, ambiguity questions, "lighthouse" -> house, "scarecrow" -> car, "cargo ship" and "fire truck" -> stand-ins unannounced | `fast_path.py:129, 415-433, 443-451`; probes | Medium-high: wrong results with confidence 0.7 and no note | W1 word-boundary kinds + "stand-in" note; W2 multi-intent split |
| 8 | Notes and assumptions are discarded or transient where it matters (Home/NewBuild never display `summary`; workspace shows a toast) | `Home.tsx:112-118`, `NewBuild.tsx:59-63`, `useWorkspace.ts:205-214` | Medium: people cannot learn what the engine assumed (studs vs bricks, tree left out) | W1 "what I understood" chips on the version |
| 9 | New generator builds wait 11-21 s for validation of a model the generator already guarantees; cold worker 17-67 s; hint text duplicated and inconsistent ("Fast path: Fast path: free, ~15 s. Free, about 1 s.") | `builds_api.py:237`, `results-journey.json`, `BottomBar.tsx:45` | Medium: perceived speed of the very first interaction | W1 show first, check in the background; warm worker at start |
| 10 | No colour harmony: one colour word recolours one role (blue house, dark red roof) | `fast_path.params_from_spec`, probe "blue house" | Medium: the owner's own example | W1 default palettes per generator |
| 11 | Coverage: 11 generators, 4 families people ask for most are absent | section 3.2 | Medium; demand is not measured yet (no telemetry) | W3 by measured demand |
| 12 | No telemetry: understanding spend, route taken, how often the person undid, which prompts fell to the agent | `understand.py:422` only logs | Medium: the improvement loop is blind | W1 local log, W3 dashboard |

## 6. Improvement plan

Effort in person-days of focused agent work, rough. The ordering follows impact per effort and the product rules (deterministic first, LLM only for understanding, paid agent only after an estimate).

### Wave 1: make every build editable by text, and stop the silent failures (about 8-10 days)

1. **Role- and selection-based deterministic edits on any build** (3-4 d). New `edits.py` next to `fast_path.py`: resolve an edit target to plan entries (`role == roof`; the canvas selection sent as `selection: [ids]`; for plans without roles a small geometric classifier: top-most coloured slope parts = roof, transparent colours = windows/glass, wheel part refs = wheels, 4-wide door elements), and emit editor ops (`recolour`, `delete`, scale by regenerate when the generator source is valid). Deterministic, 0.3-0.5 s, works on AI builds and imports; `roles` come from the generators for generated plans. Fixes findings 1, partially 2. Needs per-placement roles in the generators (replace the colour-keyed dict in `house.py:111`, `vehicles.py:59`).
2. **Source/draft divergence guard** (1 d): store the plan hash of the last generate with `source`; `draft/describe` regenerates only when the hash matches the canvas, otherwise uses role edits (item 1) or asks "this will replace your N edits; do you want a preview?". Fixes finding 2.
3. **Conversational context** (1-2 d): send `previous_text`, the last 3 turns (text + the route and changes taken, a few hundred tokens), and a richer `CURRENT_SPEC` (all booleans/enums such as chimney, porch, windows, roof style, plus the role list actually present in the draft, plus the selection summary) into the understanding call. The prefix stays cached; the tail grows by about 300-500 tokens (cost about EUR 0.00005 more). Fixes finding 5; makes "again", "a bit less", "same in red" possible.
4. **Clarify-first policy with quick-reply chips** (1 d): needs_ai only for a named, placeable subject that no generator covers, or for explicit "build properly" requests; unplaceable or vague -> clarify with 2-4 chips generated from the ambiguities or from the registry ("a small car", "a house", "a spaceship", "tell me more"). Hint honesty: replace "AI build EUR 1-2" by "I'll read this first (free-ish, about 1 s)" for anything the hint cannot place; show the price only after the real decision. Fixes finding 4.
5. **Seed the agent** (1 d): on confirm, pass `spec`, the nearest generator entry (params, roles, what it cannot do), the current draft summary and the person's notes as the first message and a short system block; use it to cap the budget (the estimate becomes a limit). Fixes finding 3.
6. **Correctness of picks and notes** (1 d): word-boundary matching with the kinds list (no `kind in k`), a "closest design" note when the generator is a stand-in ("a cargo ship is built as the harbour launch"), notes for dropped edits and ignored `ambiguities`, and a "what I understood" chip row persisted on the version and shown on Home/NewBuild (finding 8). Colour vocabulary generated once from `generators/colours.py` into the prompt (drops the Nougat mismatch and the duplicate lists).
7. **Colour harmony defaults** (0.5-1 d): per-generator palettes keyed by the main colour (blue walls -> white trim, grey or red roof; red car -> black wheels, grey trim); applied only to roles the text did not name, and listed in the chips so they can be undone. Deterministic table, no LLM.
8. **Show the model first** (0.5 d): `builds_generate` returns after the build and runs the geometry check in the background (the version's parts-check chip updates later); start the worker and `load` at app start. Perceived 15 s -> under 1 s warm, cold start hidden.
9. **Local log of the pipeline** (0.5 d, see section 7).

### Wave 2: more of the work without an LLM, and bigger requests (about 6-8 days)

1. **Broaden the rules with the role/size vocabulary** (2 d): "make the <role|part|it> <colour>", multi-pair ("roof red and door white"), "bigger/smaller/longer/taller/wider (by N)", "add/remove <feature>", "two more windows", "undo", "again", in English and Italian/Spanish/French keyword tables for the 30 most common words; the rule path resolves to the same edit executor as item 1. Target: at least 50 % of follow-ups with no call, measured on the telemetry from wave 1, with the LLM as the fallback for the rest.
2. **Multi-intent splitting** (1-2 d): the understanding schema returns an ordered `steps[]` (new_model then edits; edit then edit); the router executes them in order on a scratch plan and reports each step's result. Replaces today's "first wins, rest dropped". Includes "a red car and make it longer".
3. **Preview before apply for big changes** (1-2 d): a change is "big" when it replaces the model (new generator, stale source, size change > 30 %, role edit touching > 40 % of parts, any edit on a draft with manual edits). The draft/describe answer then returns `{route: "preview", summary, plan_url}`; the canvas shows it as a proposal (the machinery exists: `proposals`, Keep / Preview / Discard). Small recolours apply directly with Undo.
4. **Mid-confidence handling** (0.5 d): below a confidence threshold or with `ambiguities`, apply nothing and ask with chips (studs or bricks), instead of resolving silently.
5. **Errors as chips** (0.5 d): `rules_fallback`, "no model configured", unknown colour, clamp: one inline row of dismissible chips, not toasts.

### Wave 3: coverage and the one-chat UX (about 8-12 days, measured by demand)

1. **One chat** (the owner's UX decision): the right-panel chat sends every message to `/draft/describe` first; the fast answer appears as a message with its chips and an Undo; "needs the AI" appears as an inline card with the estimate and "Ask the AI" (agent context = the seeded block from wave 1). The same thread feeds the last-turns context. Needs the thread store to record route, changes and notes per message (also the telemetry).
2. **Generators by measured demand** (each 1-3 d): the telemetry decides; priors from the proposal's 1,821-model keyword counts and my coverage table: (a) tower/castle/lighthouse/windmill (the Home suggestion "spiral staircase around a tower" and my four "needs_ai" building prompts), (b) tree and garden set (finishes the house story: "house with a garden and a tree" is already a request people make), (c) train (locomotive + n carriages; 106 reference models mention trains), (d) helicopter and small plane variants (parametrise the jet), (e) ship (a real cargo/sailing ship rather than the launch stand-in), (f) simple furniture (sofa, table, bed). Parametrise the fixed designs first (motorbike, launch, jet have no size params): more coverage per day than new families.
3. **Role-aware retrieval for the rest**: reference models and imports get roles from the geometric classifier (wave 1 item 1) so recolour and remove work on them too.
4. **Eval in CI-lite**: `prompts.json` plus the golden set become a recorded regression: mocked-provider tests for routing, a live run on demand with the cap used in this review.

Effort total about 22-30 days; wave 1 alone moves findings 1-5, 7, 8, 9, 10.

## 7. Metrics and telemetry (local, privacy-preserving)

Append one JSON line per describe call to `CONFIG_DIR/telemetry/describe.jsonl`; no cloud, rotate at 5 MB, off switch in Settings, never store the sentence unless the person opts in (store `sha256(text)[:12]`, the length, the language guess, and the spec/route fields):

`{ts, surface (home|workspace|chat), build_kind (generator|ai|import|edited|empty), stale_source (bool), route, decided_by (rules|llm|rules_fallback), family, kind, generator, n_edits, n_notes, notes_kinds[], clarified (bool), chips_used (bool), model, tokens_in, tokens_out, cost_eur, latency_ms, applied (bool), undone_within_60s (bool), went_to_agent (bool), agent_cost_eur}`

Derived views (a small "NL health" page in Settings): share answered with no call; share of needs_ai and the top 20 uncovered kinds (this is the coverage-demand ranking); undo rate per route (the best quality signal we have without asking the person); clarify rate and chip-use rate; hint-vs-decision disagreement rate; understanding spend per day/build; p50/p95 latency per route; fall-through to the agent and its cost. Opt-in "save this prompt as a test" from the chat message menu appends to a local `prompts.local.json` that the eval harness reads.

## 8. Open questions for the owner

1. **Stale source policy:** when the canvas has diverged from the generator (accepted AI proposal, hand edits) should "make the roof blue" (a) recolour by role on the canvas (my recommendation, keeps all work), (b) offer "regenerate from settings, replacing N edits" with a preview, or (c) both with (a) as default?
2. **Clarify budget:** how many questions is acceptable before you would rather the system just guess and show a stand-in? (I propose at most one question per request, with chips, and a "just pick for me" chip.)
3. **Language:** is Italian (and other languages) a first-class input, so that rules tables and chips are localised, or is English with LLM-understood other languages enough?
4. **Telemetry:** are you comfortable with the local, hashed-text log above (and an opt-in to keep sentences for your own test set), or should nothing about prompts be stored?
5. **Generator stand-ins:** is it acceptable to build the nearest design for an uncovered subject (cargo ship as a launch, lighthouse as a tall house) when the result is labelled "closest design" with an "Ask the AI for a better one" button, or should uncovered always mean clarify/needs_ai?
