# Build templates: any good build becomes a parametric starting point

Status: working prototype on branch `build-templates`. Toolkit code (`ldraw_tools/roles.py`, `role_edits.py`, `templates/`), four derived templates in `ldraw_tools/data/templates/`, a small wiring change so the fast path and the build worker know templates, tests, measurements and renders. No frontend code changed. No LLM was called and no API key was used: everything here is deterministic.

Owner's direction, restated: the generators are too few, hand-coded and rigid. Any good build (the owner's own, or one of the 1,821 annotated reference models) should be usable as a **template** whose variations come from the model itself: recolour by role, stretch or shrink by repeating or removing slices, drop optional sub-assemblies, swap parts for safe equivalents. And "make the roof blue" must work on **any** build by changing the model in place, never by regenerating over the person's edits.

## 1. In one page

| Piece | What it does | Where |
|---|---|---|
| Role classifier | Gives every part of any model a role (`roof wall window door base wheel tyre chassis body glass wing engine trim detail structure`) with a confidence, from the part's library description, its colour, its place in the model and the submodel it sits in | `ldraw_tools/roles.py` |
| Role edits | "make the roof blue", "remove the chimney", "recolour the selection": pure functions plan -> editor ops, so the editor stays the only thing that changes a plan (one undo step, works on AI, imported and hand-edited builds) | `ldraw_tools/role_edits.py` |
| Template derivation | Turns a model into a template: colours per role, length knobs (plane cuts and stepped seams, longer equivalent parts or family fillers), repeatable wall courses, optional sub-assemblies, safe part swaps. Every offered value is built and checked before it is offered | `ldraw_tools/templates/derive.py` |
| Template runtime | `generate(params) -> plan`, pure and fast (no parts library), same surface as a generator | `ldraw_tools/templates/core.py`, `__init__.py` |
| Registry and routing | Templates appear next to the generators as `template:<id>` for the fast path; the build worker builds them; a generator wins every tie | `app/web/backend/generator_bridge.py`, `fast_path.choose`, `ldraw_tools/pipeline.py` |

Measured (details in section 3): on generator models the classifier gets **81.1 %** of placements right from geometry and part descriptions alone (**87.1 %** on the physical roles, leaving out generator colour slots such as "accent"), up from 75.1 % / 80.6 % on the interrupted branch; wings went from 65 % to 98 %, base from 71 % to 100 %, glass from 88 % to 100 %, chassis from 77 % to 83 %, trim from 14 % to 33 %. Its confidence is calibrated: placements scored 0.8-1.0 were right 150/150 times. On 24 reference models it agrees with what their authors' submodel names imply on 44 % of parts (proxy labels, see 3.2) and picks the right object type for 22/24.

The four prototype templates (section 4) are derived without a line of model-specific code: a reference house (LEGO 5891), a reference truck (4915), the owner's Willowbank Cottage, and a reference railway car (10183). Each has colours per role, at least one verified length or height knob, optional sub-assemblies and part swaps, and `generate()` takes 2-30 ms.

## 2. How it works

```
any build (plan, MPD import, AI result, reference model)
   |
   |  roles.tag_plan           part kind (description) + colour + geometry (height band, footprint, wheels, glazing,
   |                           fuselage width) + submodel names; scenes split into objects; roles kept where present
   v
role-tagged plan  ----------------------------->  role_edits.*  ->  editor ops  ->  editor.apply   ("make the roof blue")
   |
   |  templates.derive          flatten to physical parts (leaves) with world boxes
   |    colours                 most common colour per role -> colour_<role>
   |    length (x/z)            cuts: a plane, or a seam stepping around parts that cannot be lengthened; parts crossing
   |                            the cut get their longer sibling (1x4 -> 1x6, 2x2 -> 2x3 turned a quarter), or keep
   |                            their size and get a filler of their family after them (1x3 + 1x2); holes where
   |                            material ran across the cut are closed the same way; parts beyond the cut move
   |    sections (x/z)          (fallback) repeat or remove a slice that only whole parts sit in
   |    courses                 repeat or drop one 24-LDU course of the main mass (garden and scenery stay put)
   |    optional groups         sub-assemblies at any depth nothing rests on (chimney, porch, trailer, bush, lamp)
   |    swaps                   equivalent part sets with identical boxes (plain/grille/log bricks, grooved/grille tiles)
   |    verification            every offered value is built and measured: no validation or geometry error that the
   |                            source did not have, and no more disconnected components than the source
   v
template JSON (ldraw_tools/data/templates/<id>.json: leaves + knobs + params schema + source/licence)
   |
   |  Template.generate(params)   pure: drop groups -> swaps -> stretch -> sections -> courses -> colours -> plan
   v
plan with a role on every placement  ->  build worker (generator id "template:<id>")  ->  workspace
```

Design rules that came out of the work:

- **Never trust a knob that was not built.** Derivation walks out from zero in each direction (`+1, +2, ...` and `-1, -2, ...`) and stops at the first value that fails, so an offered range is contiguous and every value in it was checked. The report next to each template (`reports/<id>.report.json`) lists what was verified, what was rejected and why, and how the extreme combination of all length knobs fares.
- **No holes.** A plain stretch opens a gap wherever nothing crosses the cut. The seam solver treats every height band and every row across the model (a car's two side walls share a band) separately and only accepts a cut when every place where material ran continuously across it gets a lengthened part or a filler. This was invisible to validation (a gap is connected and collision-free) and only showed in renders, so it is now part of the derivation itself.
- **Moved and alias parts are real parts.** Of the 761,435 part references in the reference models, 6.6 % point at a `~Moved to` stub and 15.1 % at an `=` alias or `_` physical-colour part (1,698 of 1,821 models have some). The classifier and the stretch families now follow them to the real part.
- **Roles are data, not colours.** Generators stamp a per-placement `role` at construction (the colour-keyed tagging is gone), `tools/plan_hashes.py` and `tests/fixtures/generator_plan_hashes.json` prove that the 17 generator plans checked are byte-identical to master's once the role field is removed.

## 3. Classifier accuracy

Scripts: `eval_roles.py` (generator ground truth), `eval_reference.py` (reference models). Raw results: `results-generators.json`, `results-reference.json`, and the same before this round of fixes in `*-before.json`.

### 3.1 Generator models (ground truth = the roles the generators stamp; 995 placements, 17 parameter sets of 11 generators)

| Mode | Accuracy | Physical roles only |
|---|---|---|
| parts + geometry (submodel names ignored) | 75.1 % -> **81.1 %** | 80.6 % -> **87.1 %** |
| + submodel names | 73.6 % -> 79.6 % | 79.0 % -> 85.5 % |
| + names + a family hint (what the understanding step knows) | 74.2 % -> 80.0 % | 79.2 % -> 85.5 % |

"Physical roles only" leaves out placements whose generator role is a colour slot (`accent`, `metal`, `wood`): a stripe on a wing is a wing to the classifier and an accent to the generator. Submodel names make things slightly worse on generator models (all of it on the house: 324 -> 309 of 373) because the house generator's `house-roof` submodel also holds the gable walls, which is why names only override geometry for specific roles and only in a domain where the role exists.

Per role (parts + geometry):

| role | before | after | | role | before | after |
|---|---|---|---|---|---|---|
| base | 10/14 | **14/14** | | roof | 116/174 | 117/174 |
| body | 254/270 | 257/270 | | trim | 17/123 | **41/123** |
| chassis | 53/69 | **57/69** | | tyre | 10/10 | 10/10 |
| detail | 24/33 | 24/33 | | wall | 170/184 | 170/184 |
| door | 6/6 | 6/6 | | wheel | 10/10 | 10/10 |
| engine | 6/6 | 6/6 | | window | 13/13 | 13/13 |
| glass | 15/17 | **17/17** | | wing | 43/66 | **65/66** |

Main confusions left: `trim -> wing` 30 (generator accent plates on wings: a label artefact), `roof -> body` 30 (the delivery van's two-tone upper body is "roof" to the generator), `trim -> body` 24 (accent tiles on cars), `roof -> trim` 16, `wall -> trim` 14 (gable base plates). `trim` stays the weak role (33 %): to the generators it is a colour slot as much as a place. Calibration: confidence 0.8-1.0 right 150/150, 0.6-0.8 right 86 %, 0.4-0.6 right 74 %; below 0.4 rarely used and usually wrong (0/16), which is what `tag_plan`'s low-confidence list is for.

### 3.2 Reference models (24 models: 9 buildings, 9 road/rail vehicles, 6 aircraft and spacecraft)

The reference library has no per-part labels. Its authors do name submodels ("Roof", "Door LH", "Wing right", "Front wheel"), so every part inside a role-named submodel gets that role as a proxy label, loose details (hoses, figures) are not scored, and the classifier runs **without** submodel names so the names are not seen twice. The proxy is noisy in both directions: a "Roof" submodel also holds the gable bricks and the plates the roof sits on, a car's "Door" submodel is mostly plates and hinges, a "wheels" submodel of a train holds the whole bogie.

| | before | after |
|---|---|---|
| agreement with the authors' submodel names | 40.6 % | **44.0 %** (1,827 parts) |
| object type (building / vehicle / aircraft) right | 21/24 | **22/24** |
| wing | 41 % | **64 %** |
| wheel | 7 % | 19 % |
| roof | 56 % | 58 % |

Main confusions: `roof -> wall` 202 (bricks in "Roof" submodels below the roof line: gable walls and supports, arguably walls), `roof -> body` 110 (a house in a scene classified as a vehicle, see below), `door -> chassis/body` 115 (car doors built from plates), `wheel -> chassis` 53 (bogies), `window -> roof` 48 (dormer windows in "Roof" submodels). The two object-type misses are scenes the splitter cannot separate: in 5771 the car stands inside the house's footprint, so the two overlap in plan; in 5766 the cabin and the boat are one submodel, and the boat's hull outvotes the cabin.

The role-coloured renders in `renders/roles/` (section 5) are the better check for reference models.

### 3.3 Fixes made in this round (all in `roles.py`)

1. `~Moved to` stubs are followed to the real part, and `=` / `_` alias parts are classified like their base part (they were `other`).
2. Scenes: the root's sub-assemblies are merged into objects when their footprints overlap; when objects of different types stand apart (a house and a truck), each is scored in its own frame and vocabulary.
3. Aircraft: a plate is a wing when it reaches beyond the fuselage (the typical half-width of the bricks and slopes), not when it is "far from the centre line".
4. Buildings: plates in the bottom layer are base; tiles on top of a flat roof are roof; bricks standing outside the house (garden walls, gate posts, tree trunks) and wall courses in a contrasting colour (a band between storeys, a plinth) are trim, as in the building grammar, so "make the walls white" leaves the garden and the bands alone.
5. Vehicles and aircraft: transparent slopes and bricks are glass (only small transparent plates and tiles are lamps); bricks above the glazing are roof; train wheels are wheels (they were "details"); bogies, buffers and couplings are chassis; big wheels no longer turn the cab front into chassis (only flat parts, or parts starting below the axles, are chassis level with the wheels); door parts are doors on vehicles too.
6. Boats count as vehicles (a hull part decides).

## 4. The prototype templates

Derived by `derive_templates.py` from `templates.spec.json`; stored in `ldraw_tools/data/templates/`; the derivation reports (what was verified, what was rejected, the extreme combination) are in `reports/`. "Verified" below means: built with the toolkit's builder and `analyze_geometry` (all contacts), no validation or geometry error the source did not already have, and no more disconnected components than the source.

| Template | Source | Parts | Varies (all verified) | Cannot vary | Derivation | `generate()` |
|---|---|---|---|---|---|---|
| `small-house` | LEGO 5891 Small House, by Marc Giraudet (CCAL 2.0); house-like reference model | 390 | colours of 6 roles (base, door, roof, trim, wall, window); depth **+2 studs** (`z_delta`); wall courses **-1..+1** (`courses_delta`); 4 optional groups (chair, lamp, plant, both bushes); 3 part swaps (1x2 bricks: plain, grille, embossed, clear; 1x4 bricks: plain, log; 1x2 tiles: grooved, grille) | width (the two cuts found along x add a collision with a neighbouring part at +2 studs; every other cut crosses a door, a window or an inverted slope with no longer sibling); +2 courses (one more disconnected part than the source); the swing (its axle assembly rests on it, so it is not offered on its own) | 4.6 min | 9 ms |
| `flatbed-truck` | LEGO 4915 Flat Bed, by Ken Drew (CCAL 2.0); vehicle | 59 | colours of 8 roles (body, chassis, door, glass, roof, trim, tyre, wheel); trailer length **+1..+4, +6, +8, +10, +12 studs** (`z_delta`; +5, +7, +9, +11 have no part set); the trailer (`include_part_trailer_p2`); 1x2 tile swap | width; shorter (no shorter siblings across the cut); the cab alone | 0.5 min | 2 ms |
| `willowbank-cottage` | the owner's Willowbank Cottage (building atlas build in `app/data/generated/`) | 1,163 | colours of 7 roles (base, detail, door, roof, trim, wall, window); depth **+2 studs** (`z_delta`); wall courses **-1..+6** (`courses_delta`, the extreme combination with +2 depth also verified); 6 optional groups (entry porch, ridge chimney, vegetable field, bench, two fence runs); 2 part swaps (1x2 and 1x4 bricks) | width (no cut along x avoids the windows, dormer and porch); depth +4 (failed the check); the two trees (at most six optional groups are offered, and the derivation stops there) | 27 min | 26 ms |
| `passenger-car` | LEGO 10183 passenger car, by Bert Van Raemdonck (CCAL 2.0); one more from the reference library | 389 | colours of 8 roles (body, chassis, detail, door, glass, roof, wheel, window); length **+1 and +4 studs** (`z_delta`: a seam that steps around the window panels and closes the gaps with filler bricks); the four end-platform filler panels (`include_part_padding_all`); 3 part swaps | +2, +3 (no common set of longer parts at the chosen seam); width; a second seam (it shares parts with the first) | 1.6 min | 10 ms |

What the knobs do, in the template's own terms (`ldraw_tools/templates/core.py`):

- `colour_<role>`: every part of the role takes the colour (a transparent part stays transparent: "blue" glass is Trans-Blue).
- `x_delta`, `z_delta`: studs added along an axis. Parts crossing the cut become their longer sibling (a 1x4 brick becomes a 1x6; a 2x2 brick becomes a 2x3 turned a quarter), or stay and get a filler of their family after them (a 1x3 brick plus a 1x2); parts beyond the cut move. Holes where material ran across the cut are closed the same way. The model is re-centred.
- `courses_delta`: one 24-LDU slice of the main mass repeated or removed; what stands above moves up or down; scenery around the building stays.
- `x_repeat`, `z_repeat`: (implemented, not offered by these four) copies of a slice that only whole parts sit in, for a model with a clean bay.
- `include_<group>`: a sub-assembly nothing rests on, at any depth of the model's submodels; identical instances (two bushes, four filler panels) are one knob.
- `swap_<set>`: every part of an equivalent set (identical library box) becomes another member.

Other reference models tried while building this (not shipped): the 31049 propeller plane (an unverified fuselage-length cut, colours and swaps), the 10183 tram and caboose (colours and swaps only: every cut along them crosses door, window or curved parts without siblings, with nothing to close the gap).

## 5. Renders

Rendering worked in this environment: LeoCAD 21.06 (`apt install leocad`) under `xvfb-run`. This LeoCAD has no BOM switch, so `snap.py` calls only the image export with the toolkit's switches. All pictures are rebuilt by `render_roles.py` and `render_variants.py variants.spec.json`; `renders/variants/summary.json` lists for every picture its part count, validation errors, geometry errors and connected components (template variants have exactly the source's numbers).

**Roles** (`renders/roles/`, classifier output on models it has never seen roles for; colours: red roof, tan wall, blue window, brown door, green base, yellow wheel, black tyre, dark grey chassis, orange body, turquoise glass, magenta engine, purple wing, white trim, pink detail, light grey structure):

| House 4956 | Truck 4993 | Passenger car 10183 |
|---|---|---|
| ![](renders/roles/4956-1_House-with-Arch-home.png) | ![](renders/roles/4993-1_Truck-home.png) | ![](renders/roles/10183-1_Passenger-car-home.png) |
| **Willowbank Cottage** | **Aircraft 31045** | **House with truck 4954 (a scene)** |
| ![](renders/roles/willowbank-cottage-v1-home.png) | ![](renders/roles/31045-1_Aircraft-home.png) | ![](renders/roles/4954-1_House-with-Truck-home.png) |

What they show: roofs, walls, windows, doors and bases come out right on houses (on 4956 the gable ends inside the roof zone and the chimney count as roof, so "make the roof blue" repaints them too, see below); wheels, tyres, glass and bogies come out right on vehicles; the truck's grille and bumper are "chassis" because they sit level with very large wheels; on the passenger car the window strip between the panes is body. The same pictures for three generator models (roles removed first) are `generator-*-home.png`.

**Template variants** (`renders/variants/`, before and after):

| Source | Variant | Variant |
|---|---|---|
| ![](renders/variants/flatbed-truck-source.png) flatbed truck | ![](renders/variants/flatbed-truck-z+6.png) `z_delta: 6` | ![](renders/variants/flatbed-truck-z+12-red.png) `z_delta: 12`, body, door, trim red |
| ![](renders/variants/passenger-car-source.png) passenger car | ![](renders/variants/passenger-car-z+4.png) `z_delta: 4` (a stepped seam; the gaps are closed with filler bricks) | ![](renders/variants/passenger-car-z+4-blue.png) `z_delta: 4`, blue body, grille bricks |
| ![](renders/variants/small-house-source.png) small house | ![](renders/variants/small-house-z+2.png) `z_delta: 2` (the arch, its pillar and the back garden wall move with the house) | ![](renders/variants/small-house-courses+1.png) `courses_delta: 1` |
| ![](renders/variants/small-house-courses-1.png) `courses_delta: -1` | ![](renders/variants/small-house-red-roof-no-bushes.png) red roof, tan walls, no bushes, no lamp | |
| ![](renders/variants/willowbank-cottage-source.png) Willowbank Cottage | ![](renders/variants/willowbank-cottage-z+2.png) `z_delta: 2` | ![](renders/variants/willowbank-cottage-courses+3.png) `courses_delta: 3` |
| ![](renders/variants/willowbank-cottage-recoloured-no-porch-chimney-field.png) dark red roof, white walls, no porch, chimney or vegetable field | | |

**Role edits on imported reference models** (no template, no generator: import, classify, `role_edits`, `editor.apply`):

| Before | "make the roof blue" |
|---|---|
| ![](renders/variants/edit-4956-house-source.png) | ![](renders/variants/edit-4956-house-roof-blue.png) |
| **Before** | **"make the body lime"** |
| ![](renders/variants/edit-4993-truck-source.png) | ![](renders/variants/edit-4993-truck-body-lime.png) |
| **Before** | **"make the wings red"** |
| ![](renders/variants/edit-31045-aircraft-source.png) | ![](renders/variants/edit-31045-aircraft-wings-red.png) |

Problems the renders caught that validation did not, all fixed in the derivation: holes where a stretch opened a gap nothing crossed (the railway car's far side and roof); an arch whose top moved with the house while its pillar stayed in the garden (now the parts a house carries move with it); a fence behind a house that the growing house ran into (the garden is now tried both moving with its side and staying, and verification picks).

## 6. Making a template from a build

How a person does it (UI idea, not built): in the workspace, the build menu gets **Make this a template**.

1. The person clicks it on a saved version. The server runs `templates.derive` on that version in the build worker as a background job (0.5-40 minutes depending on size; the verification builds dominate) and shows progress ("checking longer versions: 4 of 9").
2. The result is a card: a name (pre-filled from the build), the words it should answer to (kinds, pre-filled from the build's title and the understanding step's reading of it), and the knobs it found, each with a thumbnail of its extreme value ("length +12 studs", "2 more courses", "no chimney"). Knobs can be switched off; nothing can be switched on that was not verified.
3. Saving writes `<data>/templates/<id>.json` for that person (`templates.load_dir`), and the registry dump picks it up (the bridge signature includes the templates folder). From then on "another one of those, but longer and in green" is a free fast-path build.
4. The template keeps the source build's id and version, author and licence. A template from a reference model keeps the model's author and licence line, and the UI shows it.

Today the same thing is one command: `python docs/plans/build-templates/derive_templates.py OUTDIR SPEC.json` (see `templates.spec.json`).

## 7. Template vs generator vs AI in the fast path

The understanding step already turns a sentence into `spec.v1` (family, kind, sizes, colours, features). Routing stays deterministic:

1. **Edit of an existing build** ("make the roof blue", "remove the chimney", "make it longer"): role edits first, on any build (roles from the build's own placements, else from `roles.tag_plan`), as editor ops; a template or generator build may instead change its params when the draft still equals the last generated plan (REVIEW wave 1, item 2).
2. **New model**: `fast_path.choose` scores generators and templates together (kind match 4, family 2, primary family 1, exact kind 1). On a tie a **generator wins**: it is parametric from scratch and tested at its corners. A template wins only a kind no generator matches as well: "flatbed truck", "passenger car", "house with garden". The cottage template answers "cottage" only when asked by name, because the house generator owns "cottage". `templates.candidates()` implements the same rule for the toolkit side.
3. Template params come from the spec like generator params do. Colours by role work today (`fast_path._colour_param` also looks for `colour_<role>`); features ("without the chimney" -> `include_<group>`) and size words (`x_delta`, `z_delta`, `courses_delta`) are wave 1 work; until then a template build starts from its defaults plus colours.
4. **Needs AI** only for subjects neither covers, and only after the price is shown.

A template is a weaker promise than a generator in one respect: knobs were verified one at a time (plus the extreme combination of all length knobs, recorded in the report), not every combination. Colours, swaps and group removals do not interact with geometry; lengths and courses can, which is why the extreme combination is checked.

## 8. Relation to the building grammar (branch `build-grammar`)

Templates derive variations from an existing model; the grammar generates buildings from rules. They meet at roles, and the vocabularies now line up:

| this branch | grammar (`ldraw_tools/grammar/roles.py`) | note |
|---|---|---|
| roof, wall, window, door, glass, base, trim, structure | same names, same meaning | |
| detail | detail | grammar: chimney, porch, balcony, steps, sign; here: loose accessories (figures, plants, lamps). Both mean "not structure", so "remove the details" is safe on both |
| (none) | tower | added to `ROLES` and `ROLE_WORDS` ("tower", "turrets"); folds into `wall` in `CANONICAL`, as the grammar proposal suggests |
| wheel, tyre, chassis, body, wing, engine | (buildings only) | the grammar's vehicle extension plans to reuse these names |

The words people use were also merged (`brickwork`, `masonry`, `panes`, `glazing`, `cornice`, `plinth`...). One deliberate difference: "chimney" and "porch" are not mapped to a broad role here; on a plan that has no `chimney` role, "remove the chimney" must not fall back to "remove the roof" (the interrupted branch did exactly that).

How the two fit together: a grammar building has exact roles, so it is the ideal template source (and ground truth for the classifier on houses); a template's sub-assemblies (a window bay, a porch) are candidates for the grammar's "harvested components"; and both produce role-tagged plans, so role edits and the "make it longer" vocabulary work on both.

## 9. Limits

- **Stretch needs lengthenable parts.** Brick, plate, tile, slope, panel and curved-brick families stretch; windows, doors, glass and one-off parts (a jet engine, a dish) do not, and a seam must step around them. Plain or slightly varied models stretch well (truck +1..12 studs, railway car +1 and +4); dense models with garden scenery all round may offer only courses.
- **Shrinking is rare.** A negative length needs shorter siblings for every crossing part and nothing in the removed strip; most cuts only grow.
- **Knob combinations** are verified at the extremes, not exhaustively.
- **Sizes are coarse.** One length knob per axis, and the cut position is the solver's choice, not the person's ("make the trailer longer" and "make the cab longer" are the same knob today).
- **Validation is the toolkit's.** "Connected" means the toolkit's optimistic contact graph, and the reference sources themselves carry errors (5891 has 26 validation errors as shipped); a template promises no regression, not perfection.
- **Classifier**: `trim` is weak (33 % against generator labels), gable walls in roof submodels are walls, car doors made of plates are body, scenes where objects overlap are one object.
- **Derivation is slow on big models** (the cottage, 1,163 parts, takes tens of minutes): every check is a full build plus geometry analysis.
- **Licensing**: reference models are CCAL 2.0 redistributable; the template keeps author and licence, but shipping derived templates from many sets is an owner decision (question 3).

## 10. Productionising in two waves

**Wave 1: role edits everywhere, templates on the fast path (about 6-8 days).**
1. Role edits in the workspace on any build (REVIEW wave 1, item 1): `draft/describe` resolves `set_colour`/`remove_feature` edits with `role_edits` when the build has no live generator source or the draft diverged; the canvas selection becomes `selection`; low-confidence roles are shown as chips the person can correct (2-3 d).
2. Templates in routing: the bridge already lists them; teach `params_from_spec` the size words for `x_delta`/`z_delta`/`courses_delta`/`*_repeat` and the feature words for `include_*`; show "starting from: Flatbed truck (4915), by <author>" in the result (1-2 d).
3. Derivation as a background job in the build worker with progress, cached by (model hash, toolkit version); per-person template folder; "Make this a template" card (2-3 d).
4. Run the backend suite once in the container. Here it ran in a throwaway environment with the app's locked dependencies: the same 18 failures as on master (tests that need the container: LeoCAD with a display, mpd2glb, the baked library and its indexes), and the new routing test (`test_a_template_wins_only_a_kind_no_generator_owns`) passes.

**Wave 2: more models, better knobs (about 6-10 days).**
1. Derive templates for the best 50-100 reference models (by part count, `source_checks_passed`, subject demand); spot-check renders; prune knobs that look wrong.
2. Named length knobs: give each accepted cut a name from the roles it lengthens ("trailer", "cab", "wall between the windows") so "make the trailer longer" picks the right one; offer two cuts per axis where both verify.
3. Window/door assemblies as units (frame + glass + shutters move and repeat together), which unlocks "add a window" by repeating a bay.
4. Classifier: per-row trim detection (bands, cornices), door assemblies on vehicles, use the grammar's exact roles as extra ground truth; scene splitting for overlapping objects by connectivity rather than footprint.
5. Faster verification: an incremental geometry check of only the changed region instead of a full rebuild per value.

## 11. Open questions for the owner

1. **Which reference models should ship as templates?** Deriving all 1,821 is a batch job of days and many sources have errors; a curated 50-100 by subject demand is the proposal.
2. **Should a template beat a generator when the person names its kind loosely?** Today a generator wins every tie ("cottage" builds the house generator, not the Willowbank template). The alternative is to prefer the person's own templates over everything.
3. **Licensing and attribution** of templates derived from CCAL reference models: ship them in the image with author lines, or derive them on demand from the library the person already has?
4. **How much may a template change the look?** Length and course knobs keep the model's style; part swaps (log bricks, grille tiles) change it visibly. Should swaps be offered by default or only on request?

## Files

- `ldraw_tools/roles.py`, `ldraw_tools/role_edits.py`, `ldraw_tools/templates/{__init__,core,derive}.py`, `ldraw_tools/data/templates/*.json`
- `ldraw_tools/pipeline.py` (builds `template:<id>`), `app/web/backend/generator_bridge.py` (lists templates), `app/web/backend/fast_path.py` (ties go to generators; `colour_<role>` params)
- `tests/test_roles.py`, `tests/test_role_edits.py`, `tests/test_templates.py`, `tests/fixtures/generator_plan_hashes.json`, `tools/plan_hashes.py`
- This folder: `eval_roles.py`, `eval_reference.py`, `derive_templates.py` + `templates.spec.json`, `render_roles.py`, `render_variants.py` + `variants.spec.json`, `snap.py`, `results-*.json`, `reports/`, `renders/`
