# Ontology of LEGO buildings

One grammar, many buildings. A house, a cottage, a shop, a townhouse, a tower and a castle keep are not six generators: they are six configurations of the same tree, each a small JSON document. Code is in `ldraw_tools/grammar/`; the machine-readable form is [`ontology.schema.json`](ontology.schema.json) (JSON Schema 2020-12 with `default`, `minimum`/`maximum`, `enum` and two extra keywords: `x-component` names the ontology node a group belongs to, `x-colour` marks a colour parameter). The parameter tables below are generated from it by `export_schema.py`.

```
Building
├── Footprint   rect · L · U · courtyard
├── Storeys ×N  height in courses · floor bands · setbacks
├── Walls       style brick/timber/stone · thickness 1-2 · bond · colours · plinth · accent courses
├── Openings    door (plain, smooth, glazed, barred) · windows (rect, small, tall, arch, slit, wide) · rhythm · sizes from real parts
├── Corners     plain · pillars · towers (square or round, height, flat+battlements or cone/pyramid)
├── Roof        gable · hip · flat (+parapet or battlements) · overhang · verge · pitch · ridge
├── Details     chimney · porch · balcony · sign band
└── Base        plate · ground colour · path
```

## Units and coordinates

Studs (x right, z back; the front is the low-z side) and plates (1 brick = 3 plates, 1 plate = 8 LDU). The engine works on a grid of stud cells: a footprint is a set of cells, a wall is the band of cells within `thickness` of the outside, and every placement occupies whole cells at whole plate heights. Nothing is placed by guessing a bounding box: `canvas.py` records occupancy per cell and plate and refuses a second part in the same space (a `Conflict` is a bug, not a warning).

## What each component builds

**Footprint.** A set of cells. L, U and courtyard are the rectangle minus a notch or a yard. Upper storeys can step in (`setback`, rectangles only).

**Storeys.** Each storey is `courses` brick courses followed by a floor band: one layer of plates round the walls (the string course, optionally projecting one stud as a cornice) and, with `floors`, a second crossing layer over the interior so the slab holds together. The top storey always gets the two-layer slab: it is the roof deck or the ceiling the roof sits on.

**Walls.** Per course, the wall cells are cut into runs and each run is cut into real brick lengths (1x1, 1x2, 1x3, 1x4, 1x6, 1x8) by a small dynamic programme that punishes any vertical joint that lines up with a joint of the course below, so every straight wall is a running bond. Corners interlock: a corner cell belongs to the wall running along x on even courses and along z on odd ones. Thick walls (2) are two staggered rows. Styles only change colour decisions, not geometry: `timber` colours posts every 4 studs, jambs and corners; `stone` mixes two colours brick by brick; `plinth` and `accent_every` recolour whole courses.

**Openings.** Facades are straight runs of outer wall. The door sits on `door_facade` (centre, left or right); windows are laid out per facade so that gaps between them are at least the rhythm's pier and the spare room is shared evenly (a row is symmetric). Upper storeys reuse the centres of the ground floor, so windows line up, with an optional window above the door. Window kinds are real parts: `rect` 1x4x3 with two casement panes, `small` 1x2x2, `tall` 1x2x3, `arch` 1x2x2⅔ with rounded top on a one-plate sill, `slit` an open 1-stud gap two courses high (arrow slit), `wide` two 1x4x3 side by side. A kind that does not fit the span or the storey height falls back to the next smaller one (`wide` → `rect` → `tall` → `small`). Doors are the 1x4x6 frame with a leaf (`plain` 4-pane, `smooth`, `glazed`, `barred` gate). Panes and leaves are placed on the hinge positions of the part library's own connector data, so the toolkit sees them as connected.

**Corners.** `pillars` colour the corner cells. `towers` add a tower at each selected convex corner of the footprint: square (4-6 studs, part of the footprint union so the walls bond naturally) or round (a stack of 4x4 round bricks with a round plate at every floor band). Towers rise `tower_extra_courses` above the main building and end flat with battlements or in a pyramid of hip slopes (square) or a 4x4x2 cone (round).

**Roof.** `gable`: hollow shell of 45° slopes (2x1 to 2x4), each tier standing on the studs of the one below, stepped gable-end walls in the wall colour, a ridge of plates with tiles on top, optional eave overhang and verge. `hip`: rings of slopes with double-convex corner slopes, the same ring logic gives tower pyramids. `flat`: the deck is the top slab; `parapet` adds a plain ring or battlements (alternating merlons, corners always a merlon, pattern symmetric along each wall).

**Details.** `chimney` stands on plates in the ridge; `porch` is a floor, two pillars of 1x1 bricks and a canopy of plates and tiles; `balcony` is a floor cut together with the first band (so its plates straddle wall and balcony and hold on) with a row of round 1x1 rails; `sign` turns the top ground-floor course on the front into a coloured band.

**Detail (#52).** All off by default. `openings.sills` replaces the wall under a rect, small, tall or wide window with two plates and a 2xN plate whose outer half overhangs the wall by one stud. `openings.shutters` recolours the wall bricks either side of a window for its height. `openings.keystones` recolours the two bricks in the course above an arch. `details.flower_boxes` puts a two-plate planter with 1x1 round-plate flowers on each overhanging sill (and turns sills on). `details.fence` runs 1x1 posts under 1x3 rails round the edge of the base plate, open wherever the path already stands. `roof.gable_windows` leaves a 2x2 window in each gable end of a gable roof. Every piece stands on studs of the wall, sill or base plate, so the model stays one piece. Not built: 33 degree slopes, dormers, inverted-slope eaves, finer round towers.

**Modular buildings.** `base.kind = modular` makes the module: a 32x32 plate (two crossed layers) with a tiled sidewalk and kerb in front (`sidewalk`, optional `lamps`), the building behind it. The rules keep everything inside the module (`modular-fits`): width 32, depth at most 32 minus the sidewalk, no roof overhang, no towers or eave roofs, a cornice only on the front. Floors are full plates that lift out (each rests on the storey below by studs only), `storeys.interior = partition` builds a bonded partition wall with a 4-stud doorway on every storey, and `details.connectors` puts two Technic bricks (with pin holes) into each side wall, near the top of every storey, so neighbouring modules can be pinned together.

**Base.** Two layers of plate strips laid across each other (a base of one layer would be loose plates); `path` is tiles from the door to the edge.

## Roles

Every placement carries one `role` (vocabulary in `ldraw_tools/grammar/roles.py`; the plan schema already has the optional field):

| role | what it is |
|---|---|
| `roof` | slopes, ridge, flat deck, tower cones |
| `wall` | wall bricks, gable-end walls, merlons |
| `window` | window frames |
| `door` | door frame and leaf |
| `glass` | transparent panes |
| `base` | base plates and path |
| `tower` | tower bodies, tower floor slabs, tower caps |
| `detail` | chimney, porch, balcony, sign band, shutters, flower boxes, fence |
| `trim` | floor bands, cornice, corner pillars, plinth, accent courses, timber posts, arch sills, window sills, keystones |
| `structure` | hidden supports (reserved; floors take the band's `trim` role) |

Roles are a property of the construction, not a guess from colour or part number, so "make the roof blue" selects `role == roof` whatever the roof is made of.

## Parameters

#### Footprint  (`footprint`)

Plan shape of the ground floor.

| parameter | values | default | meaning |
|---|---|---|---|
| `shape` | `rect` / `L` / `U` / `courtyard` | `rect` | rect, L, U (two wings round a notch) or courtyard (ring round an open yard) |
| `width` | 4..40 | `12` | Studs along x (the front width) |
| `depth` | 4..32 | `8` | Studs along z |
| `notch_width` | 2..20 | `4` | L/U: width of the cut-out |
| `notch_depth` | 2..16 | `4` | L/U: depth of the cut-out |
| `notch_corner` | `back-right` / `back-left` / `front-right` / `front-left` | `back-right` | L: which corner is cut away |
| `notch_side` | `back` / `front` | `back` | U: which side the notch opens to |
| `court_width` | 2..24 | `4` | Courtyard: width of the open yard |
| `court_depth` | 2..20 | `4` | Courtyard: depth of the open yard |

#### Storeys  (`storeys`)

Storeys stacked on the footprint; each is wall courses plus a floor band.

| parameter | values | default | meaning |
|---|---|---|---|
| `count` | 1..6 | `2` | Number of storeys |
| `courses` | 3..14 | `6` | Wall height of each storey in brick courses (a door needs 6) |
| `top_courses` | 0..14 | `0` | Courses of the top storey; 0 = same as the others |
| `setback` | 0..3 | `0` | Studs each upper storey steps in on every side (rect footprints) |
| `floors` | yes / no | `true` | Fill the interior with floor plates (off = ring bands only) |
| `band_projection` | 0..1 | `0` | Studs the floor bands project as a cornice |
| `eaves` | yes / no | `false` | A small hip roof round every storey but the top (pagoda, temple); needs setback 1 or more |
| `band_colour` | colour name or code | `white` | Floor bands, floors and cornice |
| `interior` | `none` / `partition` | `none` | Rooms: a partition wall with a doorway on every storey (rectangular footprints) |

#### Walls  (`walls`)

Wall construction.

| parameter | values | default | meaning |
|---|---|---|---|
| `style` | `brick` / `timber` / `stone` | `brick` | brick; half-timber (posts and infill); stone (two-tone random bricks) |
| `thickness` | `1` / `2` | `1` | Wall thickness in studs; 2 uses staggered rows |
| `colour` | colour name or code | `tan` | Main wall colour |
| `alt_colour` | colour name or code | `light grey` | Second colour (stone mix, timber infill accent) |
| `timber_colour` | colour name or code | `reddish brown` | Timber posts and corner studs |
| `plinth` | yes / no | `false` | First course in a plinth colour |
| `plinth_colour` | colour name or code | `dark grey` | Plinth colour |
| `accent_every` | 0..6 | `0` | Every n-th course in the accent colour (0 = none) |
| `accent_colour` | colour name or code | `red` | Accent course colour |

#### Openings  (`openings`)

Doors and windows, sized from real parts.

| parameter | values | default | meaning |
|---|---|---|---|
| `door` | `none` / `plain` / `smooth` / `glazed` / `barred` | `plain` | Door leaf style (4 studs wide, 6 courses tall) |
| `door_facade` | `front` / `back` / `left` / `right` | `front` | Facade the door is on |
| `door_position` | `centre` / `left` / `right` | `centre` | Where the door sits along the facade |
| `door_colour` | colour name or code | `reddish brown` | Door leaf |
| `frame_colour` | colour name or code | `white` | Window and door frames |
| `glass_colour` | colour name or code | `clear` | Glass |
| `ground_windows` | `none` / `rect` / `small` / `tall` / `arch` / `slit` / `wide` | `tall` | Window kind on the ground floor (wide = two 4-stud windows side by side) |
| `upper_windows` | `none` / `rect` / `small` / `tall` / `arch` / `slit` / `wide` | `tall` | Window kind on upper floors |
| `rhythm` | `dense` / `regular` / `sparse` | `regular` | Spacing: minimum pier 1, 2 or 3 studs between openings |
| `count` | 0..12 | `0` | Windows per facade (0 = as many as fit) |
| `sides` | any of `front`, `back`, `left`, `right` | `["front", "back", "left", "right"]` | Facades that get windows |
| `above_door` | yes / no | `true` | A window above the door on upper floors |

#### Corners  (`corners`)

Building corners.

| parameter | values | default | meaning |
|---|---|---|---|
| `style` | `plain` / `pillars` / `towers` | `plain` | plain interlocking corners, coloured pillars, or corner towers |
| `pillar_colour` | colour name or code | `white` | Pillar/quoin colour |
| `tower_shape` | `square` / `round` | `square` | Square tower (any size) or round tower (4x4 round bricks) |
| `tower_size` | 4..6 | `4` | Square tower size in studs (round towers are 4) |
| `tower_proud` | 0..2 | `1` | Studs the tower sticks out of the corner |
| `tower_extra_courses` | 0..12 | `4` | Courses the tower rises above the top storey band |
| `tower_at` | `all` / `front` / `back` / `left` / `right` / `diagonal` | `all` | Which corners get a tower |
| `tower_roof` | `flat` / `cone` | `flat` | Flat with battlements, or cone/pyramid |
| `tower_colour` | colour name or code | `light grey` | Tower walls |
| `tower_roof_colour` | colour name or code | `dark red` | Tower cone/pyramid |
| `tower_slits` | yes / no | `true` | Arrow slits in the tower |

#### Roof  (`roof`)

Roof or top finish.

| parameter | values | default | meaning |
|---|---|---|---|
| `style` | `gable` / `hip` / `flat` / `none` | `gable` | gable, hip, flat deck (with optional parapet), or none |
| `colour` | colour name or code | `dark red` | Roof slopes / deck |
| `ridge` | `auto` / `x` / `z` | `auto` | Ridge direction (auto = along the longer side) |
| `overhang` | 0..1 | `1` | Eave overhang in studs |
| `verge` | 0..1 | `0` | Gable-end overhang in studs |
| `tiers` | 0..9 | `0` | Slope courses; 0 = as steep as the depth allows |
| `finial` | yes / no | `false` | Hip roofs: a cone finial on the peak |
| `parapet` | `none` / `plain` / `battlement` | `none` | Flat roofs: parapet or battlements |
| `merlon` | 1..2 | `1` | Battlement merlon width |
| `gap` | 1..2 | `1` | Battlement gap width |
| `parapet_courses` | 1..2 | `1` | Parapet height in courses |
| `crenel_colour` | colour name or code | `tan` | Parapet and merlon colour |

#### Details  (`details`)

Add-ons.

| parameter | values | default | meaning |
|---|---|---|---|
| `chimney` | `none` / `left` / `centre` / `right` | `none` | Chimney on the ridge (gable/hip) |
| `chimney_colour` | colour name or code | `dark grey` | Chimney bricks |
| `chimney_courses` | 2..6 | `3` | Chimney height in courses |
| `porch` | yes / no | `false` | Pillared porch over the door |
| `porch_colour` | colour name or code | `white` | Porch pillars and floor |
| `balcony` | yes / no | `false` | Balcony over the door (needs 2+ storeys) |
| `balcony_colour` | colour name or code | `white` | Balcony floor and railing |
| `sign` | `none` / `fascia` | `none` | Shop sign band above the ground floor |
| `sign_colour` | colour name or code | `red` | Sign band colour |
| `connectors` | yes / no | `false` | Technic pin bricks in the side walls near the top of every storey, to bolt modules together |

#### Base  (`base`)

Ground.

| parameter | values | default | meaning |
|---|---|---|---|
| `kind` | `none` / `plate` / `modular` | `plate` | none, a plate round the building, or a 32x32 modular-building plate with a sidewalk |
| `margin` | 0..8 | `3` | Studs of ground around the building |
| `colour` | colour name or code | `green` | Ground |
| `path` | yes / no | `true` | Path from the door to the edge |
| `path_colour` | colour name or code | `light grey` | Path |
| `path_width` | `2` / `4` | `2` | Path width |
| `sidewalk` | 0..8 | `6` | Modular: studs of sidewalk in front of the building |
| `lamps` | yes / no | `false` | Modular: two street lamps on the sidewalk |

#### Validity rules

| rule | what it says |
|---|---|
| `door-height` | A door is 6 courses tall, so the ground storey needs 6 or more courses |
| `pitched-needs-rect` | Gable and hip roofs need a rectangular footprint; other shapes get a flat roof |
| `towers-flat` | Corner towers need a flat main roof (a pitched roof would collide with them) |
| `setback-rect` | Setbacks only apply to rectangular footprints |
| `thick-walls` | Wall thickness t needs a footprint at least 2t+2 studs wide and deep |
| `notch-fits` | L and U notches and courtyards must leave at least 4 studs of wall |
| `door-fits` | A door needs a facade at least 8 studs long (door plus corner piers) |
| `window-height` | A window needs its height plus one course of wall in the storey; otherwise a smaller kind is used |
| `chimney-pitched` | A chimney needs a gable or hip roof |
| `porch-door` | A porch needs a front door |
| `balcony-storeys` | A balcony needs a door and at least 2 storeys |
| `parapet-flat` | Parapets and battlements only exist on flat roofs and tower tops |
| `modular-fits` | A modular building is 32 studs wide, at most 32 minus the sidewalk deep, with no overhang and lift-out floors |
| `interior-rect` | Interior partitions only go into rectangular footprints |
| `eaves-setback` | Eave roofs round storeys need each upper storey to step in by 1 or more |
| `setback-fits` | The top storey must stay at least 4 studs wide after all setbacks |
| `eaves-rect` | Eave roofs only go round rectangular storeys |
| `sign-shop` | A fascia sign needs a door and a ground storey of 7 or more courses |

## LEGO-correct construction

| rule | how the engine enforces it | checked by |
|---|---|---|
| Studs up | every part is placed upright; slopes and corners are rotated about y only | geometry check: no error diagnostics |
| Running bond | the tiling programme avoids joints that line up with the course below and the neighbouring row | `test_wall_courses_form_a_running_bond` |
| Corners interlock | corner cells alternate owner by course parity; reflex corners are filled | `test_walls_follow_the_boundary_and_corners_alternate...` |
| Caps | walls end in a band of plates; slopes end in a ridge of plates and tiles; flat roofs in a deck and parapet | previews and course maps |
| Everything connected | a stud-and-socket graph is built while placing (floating pieces fail the engine's own check); panes sit on their frame's hinge positions | `components == 1` in every test; toolkit `optimistic_component_count == 1` |
| No collisions | cell occupancy; a second part in a cell raises `Conflict` | every build; toolkit `assembly.body_overlap` is an error |
| Real parts only | the catalogue (`catalog.py`) lists the codes and their local boxes, checked against the library | `tests/test_grammar_validation.py` |

## Valid combinations

The rules are listed in `CONSTRAINTS` and applied by `schema.normalise`; each change is reported in `notes` and nothing is silently ignored. Examples: a door raises the storey to 6 courses; pitched roofs need a rectangle (L, U and courtyard get a flat roof); towers need a flat main roof; a chimney needs a pitched roof; a sign needs 7 courses; setbacks only on rectangles. Impossible requests (walls too thick for the footprint, unknown colour) raise `ValueError` with the parameter path.

## Natural-language addressing

`grammar.nl.apply_text(config, text)` turns short phrases into config patches without a model call, and returns what it understood and what it did not. The AI route (understanding model) is for the second list. Role words map to colour parameters, so every role is addressable.

| phrase | patch |
|---|---|
| "add a tower at each corner" | `corners.style=towers, tower_at=all` |
| "round towers with cone roofs" | `tower_shape=round, tower_roof=cone` |
| "arched windows" / "arrow slits" / "tall windows" | `openings.ground_windows` and `upper_windows` = `arch` / `slit` / `tall` ("ground floor" or "upstairs" limits it) |
| "three storeys" | `storeys.count=3` |
| "a flat roof with battlements" | `roof.style=flat, parapet=battlement` |
| "chimney on the left", "add a porch", "no door" | `details.chimney=left`, `details.porch=true`, `openings.door=none` |
| "half-timbered", "stone walls", "thicker walls" | `walls.style`, `walls.thickness=2` |
| "an L-shaped house 20 x 12" | `footprint.shape=L, width=20, depth=12` |
| "make the roof blue", "make the door green", "make the base green" | `roof.colour`, `openings.door_colour`, `base.colour` (role word → colour parameter) |

## Parts-aware mode

`grammar.generate(config, inventory=[{part, colour, qty}, ...], mode='prefer'|'only')`.

- `prefer`: every brick tiling is ordered by what is owned (an unowned length costs more), colours are kept, and the report lists `shortages` (what is missing, by part and colour).
- `only`: tilings are restricted to owned lengths for that colour; a colour with no owned part is replaced by the nearest owned colour by RGB distance (listed under `substitutions`); where a run cannot be tiled with owned lengths the engine falls back to unrestricted lengths and reports the shortage honestly.
- Plates, slopes, windows and doors are counted and reported but not yet substituted (see PROPOSAL.md).

## The house dialect

The building generator also accepts the old house generator's flat parameters (`width`, `depth`, `courses`, `door_side`, `door_style`, `windows`, `window_sides`, `roof_style`, `roof_tiers`, `porch`, `chimney`, `dormer`, `wall_colour`, `trim_colour`, `roof_colour`, `door_colour`, `glass_colour`, `chimney_colour`; code in `compat.py`), so the fast path, the settings form and saved builds that speak it keep working. `dormer` is reported as not built; `courses` of 12 or more become two storeys. The old generator stays registered as `house`; `building` is first in the registry and answers every house, cottage, shop and townhouse request.

## Use

```python
from ldraw_tools import grammar
result = grammar.generate(preset='castle-keep', config={'roof': {'colour': 'dark red'}}, validate=True)
result['plan']      # plan.schema.json document; every placement has a role
result['notes']     # what the validity rules changed
result['stats']     # parts, connected components (own check)
result['validation']  # toolkit build + geometry check, when validate=True
```

Through the registry: `generators.generate('building', config)`; presets are in `generators.get('building').entry()['presets']`.
