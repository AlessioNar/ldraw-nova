# Build templates: any good build becomes a parametric starting point

Status: prototype on branch `build-templates`, measured, not wired into the UI. No LLM call, no API key and no new dependency was used. Everything below was run in the worktree with the real library and LeoCAD.

Owner direction: today's generators are few, hand-coded and rigid. A good build (the owner's own, or one of the 1,821 annotated reference models) should be usable as a **template** whose variations come from the model itself: recolour by role, stretch or shrink sections by repeating or removing slices, drop optional sub-assemblies, swap parts for safe equivalents. And "make the roof blue" must work on **any** build by changing the model in place, never by regenerating over the person's edits. Deterministic first; the LLM only understands the request.

## 1. How it works

Three layers, each usable alone.

**Roles (`ldraw_tools/roles.py`).** Every placement of a plan carries a `role`. Generators now stamp it at construction (house, spaceship, vehicles, recipes: plans are byte-identical apart from the role, guarded by `tests/fixtures/generator_plan_hashes.json` and `tools/plan_hashes.py`). For anything else (imports, reference models, hand edits) `roles.classify` scores each physical part from four signals: the part's library description (kind: slope, window, wheel, plate...), its position in the model (relative height, share of footprint, distance from the centre line), the model's domain (building, vehicle, aircraft or generic, from parts plus the title/description hint), and submodel names (only for the surest words: roof, window, door, wheel, wing, engine, glass; plus porch/fence/balcony as trim). It returns a role with a confidence and a reason; submodel instances take the dominant role of what they hold. Vocabulary: `roof wall window door base wheel tyre chassis body glass wing engine trim detail structure`.

**Role edits (`ldraw_tools/role_edits.py`).** `recolour_role`, `recolour_selection`, `recolour_like_selection`, `remove_role`, `remove_selection` return ordinary `editor` ops, so they go through the same history, undo and rebuild as hand edits. Windows and glass keep their transparency when recoloured. Removals report what they disconnect (`apply_with_connectivity_note`). `editor.py` is untouched. This is what makes "make the roof blue" work on an AI-made, imported or hand-edited build: the edit is computed from the model's current roles and selection, never from a regeneration.

**Templates (`ldraw_tools/templates/`).** `derive.derive(plan)` flattens a model to its physical parts, assigns roles, and then **proposes knobs from the geometry alone** and keeps only those whose extreme values it has actually built and checked (no validation or geometry error beyond what the source has, and no more disconnected components than the source). The result is one small JSON (about 12-20 KB per model) that holds poses, roles and the proven edits; `generate(params)` is a pure function of it (about 1 ms, no library needed) that returns a normal plan, with per-placement roles, so role edits keep working on the output.

| Knob | How it is derived | Safe range |
|---|---|---|
| `colour_<role>` | one per role present; default = the role's commonest colour; transparent parts stay transparent | any colour the generators accept |
| `x_delta`, `z_delta` (studs) | find a cut plane that few parts cross and that has a left and a right side; every crossing part must have a longer or shorter library equivalent (same description family, same footprint on the other axes, e.g. 1x2 brick to 1x4 brick; flat parts may be turned a quarter); parts beyond the cut move; the model re-centres | only values that built clean; the largest tried value that fails is dropped, and cuts bridged by fewer than three parts are capped at 4 studs |
| `courses_delta` | a one-course slice (24 LDU) of wall that nothing crosses is repeated or dropped | repeated up to the verified maximum, dropped by 1 |
| `swap_<set>` | equivalent sets whose library boxes agree: 1x2 brick (plain, grille, embossed, clear), 1x4 brick (plain, log), 1x2 tile (groove, grille) | the codes in the set |
| `include_<group>` | sub-assemblies nothing rests on (a submodel instance, or all details/engine parts) | on/off, verified off |

Registry: `ldraw_tools/templates` has the same surface as the generator registry (`find`, `get`, `generate`, `digest`, `entry`) plus `candidates(family, kind)` that ranks generators and templates together. Three prototypes ship in `ldraw_tools/data/templates/` (declared in `pyproject.toml` package data), and `app/web/backend/generator_bridge.py` now dumps template entries next to the generator entries (`templates()` data and `template_digest`), so the backend fast path can read them from the same cached dump. The cache signature covers the template files.

## 2. Measured classifier accuracy

Reproduce: `python docs/plans/build-templates/eval_roles.py out.json` and `... --reference 30 out.json`. Results are in `eval_generators.json` and `eval_reference.json`.

**Against generator ground truth** (995 placements, 11 generators, 17 parameter sets; the generators stamp the role they intended; the classifier sees the plan with roles stripped):

| Mode | Accuracy |
|---|---|
| parts + geometry only (submodel names ignored) | 75.6 % |
| + submodel names | 76.3 % |
| + names + family hint (what the understanding step knows) | 76.5 % |

Per role (names + hint): body 94 %, wall 80 %, window 100 % (13), door 100 % (6), engine 100 % (6), wheel and tyre 100 % (20), glass 88 %, detail 73 %, chassis 77 %, roof 73 %, base 71 %, wing 67 %, trim 36 %. Per generator: grand-tourer 94 %, tipper-truck 89 %, delivery-van 85 %, house 82 %, pickup 79 %, motorcycle 62 %, starfighter 52 %, harbour-launch 44 %, courier-jet 41 %, arcade-bay 0 %, street-lantern 0 %. Calibration holds: confidence 0.8-1.0 is right 98 % of the time, 0.6-0.8 about 87 %, 0.4-0.6 about 66 %, below 0.4 never. So low confidence is a usable "ask the person" signal.

Top confusions: wall->roof 36 (gable-end bricks that the generator calls wall but that sit in a submodel named roof), roof->body 36 (a jet's "roof" is its upper hull: vocabulary, not a real error), trim->body 33, trim->roof 24, wing->body 22, trim->wing 21, chassis->body 14. Most of the remaining error is **trim**, which is a colour accent rather than a physical kind, and the aircraft and recipe generators whose own vocabulary (roof on a jet, body on an arcade bay) differs from the classifier's. Real, fixable errors found and fixed in this pass: "Tail Plane" and "Train Wheel Bogie" parts, porch posts inside a porch submodel, `~Moved to` library stubs that hid a part's family, and stepped brick gables (a roof with no slopes, found by the colour that only the top of the building has: 544's roof went from 9 to 32 parts).

**Against reference models (weak gold, treat as a lower bound).** The 1,821 annotated models have no per-part labels. The gold used here is the role word in the innermost submodel name above a part (a part inside a submodel called "Roof" or "Wheels" is gold roof/wheel). The classifier runs with names switched off so the labels are independent. 30 models (every k-th of 331 with such a submodel, 40-500 parts), 794 scored parts: **38.3 %** (tyre counted as wheel). Per gold role: chassis 70 %, roof 67 %, wing 39 %, wheel 32 %, base 26 %, engine 15 %, glass 15 %, door 0 % (26), window 0 % (19). Reading the misses, this number is dominated by gold noise: a "Wheels" or "Door" submodel is an assembly of plates, pins and clips, and the classifier correctly calls those plates body or chassis. The genuine misses are engines (bushes and round bricks inside an "Engine" submodel are not recognisable by themselves), wings built from ordinary plates, and doors/windows built from technic hardware. The honest summary: **reliable for the parts that identify themselves (tyres, wheels, windows, doors, slopes, glass: 90-100 %) and for the big structural split (wall/roof/base, body/chassis/wing); unreliable for the long tail of plates and hardware whose role only the submodel structure or a person knows.** The visual check on the role renders (below) agrees: houses, trucks and tractors look right; the aeroplane's propeller blades are classed wing.

## 3. The three prototypes

Sources are reference sets, derived with `derive_templates.py` from `templates.spec.json` (8-12 s each; no hand-written code per model). Every offered variant passes the same validation as the generators' tests (`tests/test_templates.py`: no error beyond the source, no more components than the source).

| Template | Source | Parts, roles | Can vary | Cannot vary |
|---|---|---|---|---|
| `small-house` | set 544 house, 107 parts | wall 62, roof 32, window 11, door 1, base 1 | colour of each of 5 roles; wall courses -1 to +6 (repeats the slice below the gable; checked at -1, +1, +6, 1 component each); swap 1x2 brick and 1x4 brick for grille, embossed, clear or log equivalents | width and depth (windows and the door straddle every cut and have no longer equivalent; roof slopes cannot change width); no optional sub-assembly was found (the porch holds the roof up) |
| `flatbed-truck` | set 4915 flat-bed, 59 parts | body 22, chassis 17, wheel 6, tyre 6, trim 4, glass 3, roof 1 | colour of 7 roles; wheelbase `z_delta` -2, -1, +2, +4 studs (one long chassis plate bridges the gap, so capped at 4); swap 1x2 tile; drop the whole rear bed (22 parts) | width; the axle count; a longer bed beyond +4 |
| `tractor-trailer` | set 4096, 57 parts | chassis 17, body 14, wheel 8, tyre 8, roof 6, glass 4 | colour of 6 roles; length `z_delta` -2 to +8 (verified at the extremes and +-2); swap 1x2 brick and 1x2 tile | width; wheel size; the hitch geometry. The source itself has 13 optimistic components (loosely coupled), and variants do not add any |

Also tried and not shipped: set 31048 cabin (190 parts) and 31009 cottage (270 parts): the sources already have 44 and 36 components and 9 and 7 validation errors, so there is nothing sound to vary. The derivation refuses nothing here, it just offers only colours, swaps and one optional sub-assembly; a quality gate on the source ("errors = 0, components <= N") should decide whether "Make this a template" is offered.

## 4. Renders

`renders/` (LeoCAD, home view). Role-coloured legend: roof red, wall tan, window blue, door brown, base green, wheel yellow, tyre black, chassis dark grey, body orange, glass teal, wing purple, engine magenta, trim white, detail pink, structure light grey.

- Role renders of 544, 4915, 4096 and the 31049 aeroplane: `544-1_House-home.png` and the others. Judged critically: house, truck and tractor roles look right (gable, porch roof, windscreen, cab, load area, wheels vs tyres). The aeroplane marks propeller blades and tail plane as wing (acceptable), and the canopy as glass.
- Variants: `strip-house.png` (source, +3 courses with blue roof and white walls, -1 course with embossed bricks and yellow walls), `strip-truck.png` (source, +4 studs and blue body, short with the bed dropped and green), `strip-tractor.png` (source, +6 studs red), `strip-edit-house.png` and `strip-edit-plane.png` (role edits on the imported model: roof to dark green and door to red; wings red and body white; the edit changes only the model in place). Individual frames are in `renders/variants/`.
- Critique: the recolours and role edits look right. The stretched truck has a long bare chassis plate between cab and bed (valid, connected, but plain); the tractor stretch looks natural; the taller house repeats a wall slice inside the stepped gable, which works but makes the roof steeper. Recolouring a role recolours every colour in it: 544's walls are red, yellow and white and become one colour (expected from a role-level knob, but a "keep the stripes" mode would be better).

## 5. Turning a build into a template (UI idea)

"Make this a template" in the workspace's build menu. The person picks a build (AI-made, imported or their own) and presses it. Steps, all local, 10-30 s in the build worker:

1. Classify roles (`tag_plan`), show a role-coloured preview with a legend; the person can click a part or a group and correct its role (a `role` edit, stored on the placement, never overwritten by the classifier).
2. Run `derive.derive(...)`; show what was found as a short list ("recolour roof, wall, door; taller by 1 to 6 courses; swap bricks; drop the porch") with a thumbnail per extreme value, and what was rejected and why ("cannot widen: the windows cannot be lengthened").
3. Name it, choose family and kind words (suggested from the title/description), save the JSON under the person's data directory (`templates.load_dir`), and it appears next to generators in the picker and the digest for the understanding step. Deleting it deletes the file.

Licence: the template stores the source's author and licence in `source`, and `generate` writes them into the plan header, so derived builds stay attributed.

## 6. How the fast path chooses template, generator or AI

`templates.candidates(family, kind)` returns generators and templates best first. Score: a kind match (4 exact, 3 contained) plus 2 for a family match. On a tie a generator wins (parametric from scratch, tested at its corners); a template wins for a kind only it covers (a named real model, "my cottage"). Routing:

1. **Edit of an existing build** ("make the roof blue", "remove the chimney", "windows white"): role edits on the current model; no generation. Role confidence below 0.5, or an unknown role word, becomes a one-line clarification ("I found 14 parts that might be the roof, show them?"), not an AI call.
2. **New build, request names a kind a template or generator covers**: pick the top candidate; the understanding step (rules first, LLM only if the rules cannot parse) fills params; they are validated against the entry's JSON schema and clamped with a note, exactly as for generators.
3. **New build, nothing covers it**: retrieval of a reference model, imported with roles (so recolour and remove already work), then offered "Make this a template"; otherwise the AI builder as today.

Wired so far: the registry and bridge dump only (the backend can read `templates` entries and the digest); fast_path.py itself is unchanged (the backend tests were not run here, see Limits).

## 7. Relation to the building grammar

Another branch (`build-grammar`) may add `ldraw_tools/grammar/roles.py` with roof, wall, window, door, base, tower, detail, glass, trim, structure. It was not pushed when this was written, so nothing was aligned in code. The vocabularies share eight of those ten names with the same meaning; ours adds `wheel tyre chassis body wing engine` (vehicles and aircraft) and lacks `tower`. Proposal: one shared vocabulary module, with `tower` as an alias of `wall` in the classifier (a tower is a wall with a footprint and height ratio; keep it as a role only when the grammar emits it), and `canonical()` as the single place where generator-specific roles (porch, chimney, accent, hull, canopy) fold into it. The grammar produces roles **by construction** (exact), the classifier produces them **by inference** (approximate); templates and role edits accept either, and the stored role always wins over an inferred one. A grammar-built house is then a template source whose roles are exact, so its derived knobs (colours by role, courses) are the best case.

## 8. Limits

- Widening a building is mostly blocked: windows, doors and slopes have no longer equivalent and straddle every cut. Vehicles lengthen (chassis plates are plain), buildings gain height. A slice-repeat along x/z (add a bay of wall, window and roof) would need part-level knowledge of bays.
- Role accuracy on the long tail is weak (section 2); trim is the weakest role. A role is only as good as the signal; confidence is calibrated, so use it.
- Sources with poor connectivity or validation errors give poor templates (the gate above).
- Template variants are validated at the extremes, not every value (the checked ones are the largest, +-2 and +-1); a gap in the middle is not excluded, only unlikely, since the cut logic is the same for all values.
- Not run: the backend tests (the sandbox has no `fastapi`), `pytest tests/test_generators.py -k parameter_range_corners` (excluded by instruction), the frontend, any LLM path.
- Rendering worked (software GL); the old LeoCAD fails its BOM step after writing the PNGs, which the scripts tolerate.

## 9. Plan to productionise (two waves)

**Wave 1 (5-7 days): roles and role edits everywhere.** (a) Ship `roles` + `role_edits` as the executor of the chat fast path's recolour/remove commands on any build, with the confidence rule and the clarify card; roles stored on the placement. (b) Source/draft divergence guard from the NL review so role edits replace regeneration. (c) Role-coloured preview toggle and "fix this part's role" in the workspace. (d) Role eval in CI: the generator ground truth set (fails on a drop of more than 2 points) and a growing hand-labelled reference set (start with 10 models labelled by the owner through the preview). (e) Grammar vocabulary alignment (`tower`, one `canonical()`).

**Wave 2 (7-10 days): templates.** (a) "Make this a template" with the quality gate, role correction and a thumbnail per extreme value; storage in the workspace data directory; `candidates()` in the fast path next to generators; template digest into the understanding prompt. (b) More knobs: slice-repeat along x/z for repeated bays, window/door count (add or remove a bay), symmetric mirror; derived from the same verify loop. (c) Derive in bulk over the reference library overnight (10 s a model): keep those with zero errors and a sound connectivity, rank by how many knobs survive, publish the best 50-100 as the template catalogue. (d) Telemetry on which knobs people use.

## 10. Open questions

1. Is a role-level colour knob enough, or should a template keep per-colour "stripes" inside a role (544's red/yellow/white walls)? Stripes need a per-colour sub-role, which makes the UI longer.
2. Who owns the role vocabulary: the building grammar (exact, by construction) or the classifier (inferred)? I propose one module and `tower` as an alias, but the grammar branch may need roles I do not have.
3. Licence of derived templates from the reference sets (CCAL, attribution kept in the file): may they be shipped in the product catalogue, or only generated on a person's machine?
4. Should the AI be allowed to correct roles (one cheap call on the low-confidence parts), or must they stay deterministic plus a person's click?
