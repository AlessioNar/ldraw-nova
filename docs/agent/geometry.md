# Placement and physical review

## Inspect before placing

Run `./ldraw-agent part 3001 --limit 30`. It returns actual library metadata, origin-relative bounds, dimensions, stud positions, connectors, provenance, and completeness. Bounds include decorative geometry and stud height; **bounding-box height is not stacking height**. Do not infer axes from the part description alone.

Use `catalog parts QUERY --category NAME --measure` to discover shapes through the supplied categories and compare cached dimensions with the installed geometry. Category `ldu_y`, `plates_y` and rounded `studs_*` fields describe extents, not connection planes. The [visual-design guide](visual-design.md) explains selection boards, palettes and supported detail recipes. Reserve openings and overhangs before adding them; a new ornament must be checked in its containing module too.

The PDF's standard dimensions (p.63) are:

| Quantity | LDU |
|---|---:|
| Stud grid pitch / brick unit width | 20 |
| Brick body height | 24 |
| Plate body height | 8 |
| Stud diameter | 12 |
| Stud height | 4 |
| Approximate millimetres per LDU | 0.4 |

The typical studded-part origin is the centre of the top stud group at the **base** of the studs, Y=0; body extends downward toward positive Y (PDF p.143). Hinge origins often lie on the rotation axis. These are generic conventions: inspect the actual file, especially slopes, wheels, hinges, minifigures, brackets and asymmetric parts.

Verified against this library, `3001.dat` (2×4 brick) has bounds X=-40..40, Y=-4..24, Z=-20..20. Its body is 24 high, its full bounds 28 high, and its longest dimension is local X. Stud centres are X=-30,-10,10,30 and Z=-10,10, at Y=0.

## Stacking ordinary bricks and plates

For upright standard parts, the **upper part's underside** must meet the **lower part's top body plane**:

```text
y_upper = y_lower - height_of_upper_body
```

A plate placed on a brick at Y=0 goes at Y=-8. A brick placed on that plate goes at Y=-32. Do not subtract the lower part's height or add a 4-LDU gap for the stud. Studs enter the upper part's sockets, so their bounding boxes legitimately overlap.

`on` needs an earlier placement ID in the same section and at least one aligned stud grid point; it is unavailable for unknown part profiles, tiles, slopes, rotated sideways parts and submodel references (use inspection and explicit `at`). The field syntax is in the instructions and the tool reference.

Side-by-side bricks touching walls have no clutch connection; bridge them with a plate or a correctly mated connector. Place long plates across seams for connected assemblies.

## Reuse subassemblies and connection evidence

Search `./ldraw-agent search submodels 'steering' --limit 5`, then inspect the original section with `sections`. Learn its real relative transforms and mating components, but verify names, variants, completeness and annotations. OMR examples are evidence, not proof that every legacy transform is rigid or every annotation accurate. Preserve attribution if copying actual source.

For hinges, axles, wheels and clips:

1. Inspect both parts' connector frames, profile dimensions, kind, occupancy, and source/confidence. Matching visual bounds alone is insufficient. The supplied LDCad shadow metadata loads automatically; inspect its coverage and diagnostics.
2. Put them in a small test MPD. Use `inspect` to obtain stable occurrence indices and current contacts.
3. Run `connectors test.mpd --occurrence 1` to find feature IDs, then `snap test.mpd --moving 1 --fixed 0 --limit 5`. Candidates include world and parent-local poses with collision reports. `--moving-depth 0` moves the whole outer submodel. See the [snapping guide](snapping.md) for filters, application to an MPD copy and reproducible plan snaps.
4. Review candidate collisions, apply the selected pose and rerun assembly checks. Snapping checks moving descendants against stationary parts using body and oriented envelopes. General material intersections, stresses, permitted articulation ranges and strength still need review.

Use actual matching wheel/rim/tyre assemblies from inspected official shortcuts or verified references. Do not invent axle diameters, hinge pivots, or minifigure offsets.

## Interpret geometry reports honestly

`validate --geometry` and `inspect` recursively expand referenced library geometry through pyldraw3 and report world bounds for each leaf, parent source lines, connections, and overlaps. Conditional-line control points are excluded from physical extents by the geometry engine. Shortcuts remain library leaf assemblies in the BOM; they are not necessarily one manufactured piece.

`rectangular_body_overlap` is an error for curated ordinary bricks/plates in rigid orientations. Oriented bounds can eliminate AABB false positives; remaining `review_oriented_bounds` / `review_aabb_only` pairs are candidates: hollow parts can have overlapping boxes without material intersection, while an AABB cannot determine stud/socket legality. `stud_zone_overlap_review_connections` is expected when correctly stacked but still needs connector evidence.

`confirmed_components` and `optimistic_components` describe the connector algorithm's evidence. “Confirmed” is a library inference status, not a promise of real-world fit. Multiple groups can mean floating parts, missing connector metadata, or deliberately separate objects (e.g. a figure beside a vehicle). Document which explanation you verified. One connected group also does not prove that the model is strong or stable.

The report always says `physical_validity: not_proven`. Visually inspect home/top/front views and any obscured connection. Compare suspect regions with the actual part geometry and connector metadata. Record unresolved physical checks and simplify unverified construction where practical.
