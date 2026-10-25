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
| `sills` | yes / no | `false` | Projecting sill plate under every rect, small, tall and wide window that has a wall course below it |
| `sill_colour` | colour name or code | `white` | Window sills |
| `shutters` | yes / no | `false` | Coloured shutter bricks on both sides of every rect, small, tall and wide window |
| `shutter_colour` | colour name or code | `dark green` | Shutters |
| `keystones` | yes / no | `false` | Arch windows: a keystone brick in the course above the arch |
| `keystone_colour` | colour name or code | `light grey` | Keystone |

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
| `gable_windows` | yes / no | `false` | Gable roofs: a small window in each gable end |

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
| `flower_boxes` | yes / no | `false` | Flower boxes on the window sills (turns sills on) |
| `planter_colour` | colour name or code | `reddish brown` | Planter boxes |
| `flower_colour` | colour name or code | `red` | Flowers |
| `fence` | yes / no | `false` | Picket fence round the garden at the edge of the base plate, open where the path crosses it |
| `fence_colour` | colour name or code | `white` | Fence |
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
| `fence-base` | A fence needs a plain base plate with a margin of 2 studs or more |
| `flowers-sills` | Flower boxes stand on window sills, so they turn sills on |
| `gable-window-fits` | A gable-end window needs a gable roof at least 8 studs deep |
| `sign-shop` | A fascia sign needs a door and a ground storey of 7 or more courses |
