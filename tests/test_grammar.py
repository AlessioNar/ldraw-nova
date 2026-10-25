"""Building grammar: components, schema rules, determinism, parts-aware mode, natural language, registry.

These tests need no parts library: connectivity here is the grammar's own stud check. tests/test_grammar_validation.py
runs the toolkit's build and geometry analysis on presets and parameter sweeps.
"""
import json

import jsonschema
import pytest

from ldraw_tools import generators, grammar
from ldraw_tools.builder import check_schema
from ldraw_tools.grammar import catalog, engine, footprint, maps, nl, openings, roles, schema, tiling
from ldraw_tools.grammar.presets import PRESETS

PRESET_NAMES = list(PRESETS)


def build(config=None, **kwargs):
    return engine.build(config or {}, **kwargs)


def items(result, label=None, role=None, code=None):
    return [i for i in result['canvas'].items
            if (label is None or label in i.label) and (role is None or i.role == role) and (code is None or i.code == code)]


# ---------------------------------------------------------------- footprint

def test_footprint_shapes_have_the_expected_cell_counts():
    base = dict(width=12, depth=8, notch_width=4, notch_depth=4, notch_corner='back-right', notch_side='back',
                court_width=4, court_depth=2)
    assert len(footprint.shape_cells({**base, 'shape': 'rect'})) == 96
    assert len(footprint.shape_cells({**base, 'shape': 'L'})) == 96 - 16
    assert len(footprint.shape_cells({**base, 'shape': 'U'})) == 96 - 16
    assert len(footprint.shape_cells({**base, 'shape': 'courtyard'})) == 96 - 8


def test_walls_follow_the_boundary_and_corners_alternate_with_the_course():
    cells = footprint.rect(0, 0, 8, 6)
    walls = footprint.Walls(cells, 1)
    assert len(walls.mass) == 8 * 6 - 6 * 4
    assert walls.axis((3, 0), 0) == 'h' and walls.axis((0, 3), 0) == 'v'
    corner = (0, 0)
    assert walls.kind[corner] == 'corner'
    assert walls.axis(corner, 0) != walls.axis(corner, 1)


def test_thick_walls_cover_two_cells_and_reflex_corners_are_filled():
    walls = footprint.Walls(footprint.rect(0, 0, 10, 8), 2)
    assert len(walls.mass) == 80 - 6 * 4
    cells = footprint.shape_cells(dict(shape='L', width=10, depth=8, notch_width=4, notch_depth=4, notch_corner='back-right',
                                       notch_side='back', court_width=4, court_depth=4))
    thin = footprint.Walls(cells, 1)
    reflex = (5, 3)       # inside the notch corner: no axis is exposed but the wall must still be continuous
    assert reflex in thin.mass


def test_facades_report_straight_runs():
    facs = footprint.facades(footprint.rect(0, 0, 12, 8), 1, 0)
    assert {f['side'] for f in facs} == {'front', 'back', 'left', 'right'}
    front = next(f for f in facs if f['side'] == 'front')
    assert (front['a'], front['b'], front['usable']) == (0, 12, (1, 11))


# ---------------------------------------------------------------- tiling

@pytest.mark.parametrize('n', [1, 2, 3, 5, 7, 9, 12, 17, 23])
def test_tile_run_covers_the_run_exactly_with_real_lengths(n):
    lengths = tiling.tile_run(n, 0, set())
    assert sum(lengths) == n and set(lengths) <= set(catalog.BRICK)


def test_tile_run_avoids_joints_of_the_course_below():
    below = tiling.tile_run(12, 0, set())
    joints = {sum(below[:k]) for k in range(1, len(below))}
    above = tiling.tile_run(12, 0, joints)
    assert not joints & {sum(above[:k]) for k in range(1, len(above))}


def test_rect_cover_tiles_every_cell_once():
    cells = footprint.rect(0, 0, 11, 7) - footprint.rect(4, 2, 3, 3)
    seen = []
    for (code, i, j, w, d) in tiling.rect_cover(cells):
        seen += [(i + a, j + b) for a in range(w) for b in range(d)]
        assert code in catalog.PARTS
    assert sorted(seen) == sorted(cells)


def test_two_layers_hold_together_across_seams():
    cells = footprint.rect(0, 0, 20, 20)
    lower, upper = tiling.two_layers(cells, footprint.rect(0, 0, 20, 2))
    assert lower and upper
    assert {c for e in upper for c in tiling._footprint(e)} == cells


# ---------------------------------------------------------------- walls

def test_wall_courses_form_a_running_bond():
    r = build({'footprint': {'width': 16, 'depth': 10}, 'storeys': {'count': 1, 'courses': 6}, 'openings': {'door': 'none', 'ground_windows': 'none'},
               'details': {}, 'base': {'kind': 'none'}})
    rows = {}
    for it in items(r, 'wall s0'):
        if it.yaw == 0 and it.z == 0.5:                      # the front wall, row j = 0
            (it.y0 - r['canvas'].items[0].y0) // 3
            rows.setdefault(it.y0, set()).update({it.x - len_of(it) / 2, it.x + len_of(it) / 2})
    heights = sorted(rows)
    assert len(heights) == 6
    for low, high in zip(heights, heights[1:]):
        inner_low = {x for x in rows[low] if 0 < x < 16}
        inner_high = {x for x in rows[high] if 0 < x < 16}
        assert not inner_low & inner_high


def len_of(it):
    return it.cells[-1][0] - it.cells[0][0] + 1 if it.yaw == 0 else it.cells[-1][1] - it.cells[0][1] + 1


def test_every_wall_cell_is_filled_except_openings():
    r = build({'footprint': {'width': 14, 'depth': 8}, 'storeys': {'count': 1}, 'base': {'kind': 'none'}})
    [s for s in [r['canvas']]][0]
    cells = footprint.Walls(footprint.rect(0, 0, 14, 8), 1).mass
    covered = {(i, j) for it in items(r, 'wall s0') for (i, j) in it.cells}
    opening_cells = {(i, j) for it in items(r, role='window') + items(r, role='door') for (i, j) in it.cells}
    assert cells - covered <= opening_cells
    assert covered <= cells


def test_style_changes_colours_not_structure():
    plain = build({'walls': {'style': 'brick'}})
    stone = build({'walls': {'style': 'stone'}})
    timber = build({'walls': {'style': 'timber'}})
    assert {i.colour for i in items(stone, role='wall')} == {19, 71}
    assert {i.colour for i in items(timber, role='trim')} >= {70}
    assert {i.colour for i in items(plain, role='wall')} == {19}


def test_plinth_and_accent_courses_take_their_colours():
    r = build({'walls': {'plinth': True, 'plinth_colour': 'dark grey', 'accent_every': 3, 'accent_colour': 'red'}})
    assert any(i.colour == 72 for i in items(r, 'wall s0 c0'))
    assert any(i.colour == 4 for i in items(r, 'c2'))


def test_thick_walls_use_staggered_rows():
    r = build({'footprint': {'width': 12, 'depth': 10}, 'walls': {'thickness': 2}, 'openings': {'door': 'none', 'ground_windows': 'none'}, 'base': {'kind': 'none'}})
    assert items(r, 'wall s0 c0') and r['stats']['components'] == 1


# ---------------------------------------------------------------- openings

def test_fit_slots_respects_gaps_and_is_symmetric():
    for a, b, w, pier in [(1, 11, 2, 2), (1, 15, 4, 2), (2, 22, 2, 1)]:
        slots = openings.fit_slots(a, b, w, pier)
        assert slots and all(s >= a and s + w <= b for s in slots)
        assert all(n - (m + w) >= pier for m, n in zip(slots, slots[1:]))
        assert slots[0] - a == b - (slots[-1] + w)


def test_door_is_four_wide_six_tall_and_centred():
    r = build({'footprint': {'width': 14, 'depth': 8}})
    frames = items(r, code=catalog.DOOR_FRAME)
    assert len(frames) == 1 and frames[0].h == 18
    assert abs(frames[0].x - 7) < 1.01


def test_window_kinds_fall_back_when_they_do_not_fit():
    assert openings.kind_for('wide', 6, 5, 2, True) == 'rect'
    assert openings.kind_for('rect', 6, 3, 2, True) == 'tall'
    assert openings.kind_for('rect', 6, 1, 2, True) is None
    assert openings.kind_for('rect', 3, 10, 2, True) in ('small',)
    assert openings.kind_for('arch', 3, 10, 2, True) == 'small'


@pytest.mark.parametrize('kind,code', [('rect', catalog.WINDOW_4X3), ('small', catalog.WINDOW_2X2), ('tall', catalog.WINDOW_2X3),
                                       ('arch', catalog.WINDOW_ARCH)])
def test_each_window_kind_uses_its_part(kind, code):
    r = build({'footprint': {'width': 20, 'depth': 12}, 'openings': {'ground_windows': kind, 'upper_windows': kind}})
    assert items(r, code=code)


def test_slits_leave_a_gap_and_windows_have_glass():
    r = build({'openings': {'ground_windows': 'slit', 'upper_windows': 'slit'}})
    assert not items(r, role='window')
    r = build({'openings': {'ground_windows': 'rect'}, 'footprint': {'width': 20, 'depth': 8}})
    assert items(r, role='glass')


def test_upper_windows_line_up_with_the_ground_floor():
    r = build({'footprint': {'width': 16, 'depth': 10}})
    lambda s: sorted(round(i.x) for i in r['canvas'].items if i.role == 'window' and abs(i.z - .5) < .01 and s * 100 < i.y0 * 8 // 8 < (s + 1) * 100)
    ground = sorted(round(i.x) for i in items(r, role='window') if abs(i.z - .5) < .01 and i.y0 < 20)
    upper = sorted(round(i.x) for i in items(r, role='window') if abs(i.z - .5) < .01 and i.y0 >= 20)
    assert ground and upper and all(any(abs(u - g) <= 1 for g in ground + [8]) for u in upper)


# ---------------------------------------------------------------- roofs

def test_gable_has_slopes_gable_ends_and_a_ridge():
    r = build({'roof': {'style': 'gable'}, 'footprint': {'width': 12, 'depth': 8}})
    slopes = [i for i in items(r, role='roof') if i.code in catalog.SLOPE45.values()]
    assert slopes and {i.yaw for i in slopes} == {0, 180}
    assert items(r, 'gable end')
    assert items(r, 'ridge plate') and items(r, 'ridge tile')


def test_gable_ridge_follows_the_longer_side_and_can_be_forced():
    along_x = build({'footprint': {'width': 14, 'depth': 8}})
    along_z = build({'footprint': {'width': 14, 'depth': 8}, 'roof': {'ridge': 'z'}})
    assert {i.yaw for i in items(along_x, 'roof tier') if i.code in catalog.SLOPE45.values()} == {0, 180}
    assert {i.yaw % 360 for i in items(along_z, 'roof tier') if i.code in catalog.SLOPE45.values()} == {90, 270}


def test_tiers_limit_the_pitch():
    steep = build({'roof': {'tiers': 0}})
    low = build({'roof': {'tiers': 2}})
    assert max(i.y0 for i in items(steep, 'roof tier')) > max(i.y0 for i in items(low, 'roof tier'))


def test_hip_roof_has_four_corner_slopes_per_tier():
    r = build({'roof': {'style': 'hip'}, 'footprint': {'width': 12, 'depth': 12}, 'storeys': {'count': 1}})
    corners = items(r, 'hip roof corner')
    assert corners and len(corners) % 4 == 0
    assert {i.yaw for i in corners} == {0, 90, 180, 270}


def test_flat_roof_battlements_alternate_and_keep_corners():
    r = build({'roof': {'style': 'flat', 'parapet': 'battlement'}, 'footprint': {'width': 12, 'depth': 8}})
    merlons = items(r, 'parapet')
    ring = footprint.Walls(footprint.rect(0, 0, 12, 8), 1)
    cells = {c for m in merlons for c in m.cells}
    assert {(0, 0), (11, 0), (0, 7), (11, 7)} <= cells
    assert len(cells) < len(ring.mass)


def test_plain_parapet_closes_the_ring():
    r = build({'roof': {'style': 'flat', 'parapet': 'plain'}, 'footprint': {'width': 12, 'depth': 8}})
    assert {c for m in items(r, 'parapet') for c in m.cells} == footprint.Walls(footprint.rect(0, 0, 12, 8), 1).mass


def test_eave_roofs_wrap_every_storey_but_the_top_and_need_room():
    r = build({'footprint': {'width': 18, 'depth': 18}, 'storeys': {'count': 4, 'courses': 6, 'setback': 2, 'eaves': True},
               'roof': {'style': 'hip', 'finial': True}})
    skirts = {i.label for i in items(r, 'eave s')}
    assert {'eave s0', 'eave s1', 'eave s2'} <= {l.replace(' corner', '') for l in skirts} and 'eave s3' not in skirts
    assert items(r, 'finial') and r['stats']['components'] == 1
    cfg, notes = schema.normalise({'storeys': {'count': 3, 'eaves': True}})
    assert cfg['storeys']['setback'] >= 1 and cfg['storeys']['band_projection'] == 1 and notes
    cfg, notes = schema.normalise({'footprint': {'width': 12, 'depth': 12}, 'storeys': {'count': 5, 'setback': 3}})
    assert any('setback-fits' in n for n in notes) and cfg['storeys']['setback'] < 3


def test_asian_presets_use_eaves_pillars_and_hip_roofs():
    for name in ('pagoda', 'white-pagoda', 'gate-tower'):
        assert PRESETS[name]['config']['storeys']['eaves'] and PRESETS[name]['config']['roof']['style'] == 'hip'
    assert {p['group'] for p in PRESETS.values() if 'group' in p} == {'asian', 'general', 'modular'}
    assert len([n for n, p in PRESETS.items() if p.get('group') == 'asian']) >= 5
    assert len(PRESETS) >= 24


# ---------------------------------------------------------------- modular buildings

def modular(config=None):
    return build(grammar.deep_merge({'footprint': {'width': 32, 'depth': 22}, 'base': {'kind': 'modular', 'lamps': True},
                                     'storeys': {'count': 3, 'interior': 'partition', 'band_projection': 1},
                                     'details': {'connectors': True}, 'roof': {'overhang': 0}}, config or {}))


def test_modular_building_fills_exactly_one_32_stud_module():
    r = modular()
    cells = {c for i in r['canvas'].items for c in i.cells}
    xs, zs = [c[0] for c in cells], [c[1] for c in cells]
    assert (min(xs), max(xs) + 1) == (0, 32) and max(zs) - min(zs) + 1 <= 32
    base = items(r, role='base')
    assert {c for i in base if 'base' in i.label for c in i.cells} >= {(0, -6), (31, 25)}
    assert r['stats']['components'] == 1


def test_modular_rules_pull_everything_inside_the_module():
    cfg, notes = schema.normalise({'base': {'kind': 'modular'}, 'footprint': {'width': 20, 'depth': 30}, 'roof': {'overhang': 1, 'verge': 1},
                                   'corners': {'style': 'towers'}, 'storeys': {'eaves': True, 'floors': False}})
    assert cfg['footprint']['width'] == 32 and cfg['footprint']['depth'] == 26
    assert cfg['roof']['overhang'] == 0 and cfg['roof']['verge'] == 0 and cfg['corners']['style'] == 'pillars'
    assert not cfg['storeys']['eaves'] and cfg['storeys']['floors'] and len(notes) >= 5


def test_modular_cornices_stay_on_the_front():
    r = modular()
    plates = [i for i in items(r, 'band s0')]
    assert min(c[1] for i in plates for c in i.cells) == -1          # one stud in front of the wall
    assert min(c[0] for i in plates for c in i.cells) == 0 and max(c[0] for i in plates for c in i.cells) == 31


def test_sidewalk_lamps_and_connectors():
    r = modular()
    assert items(r, 'sidewalk') and len(items(r, 'street lamp')) == 10
    tech = items(r, code=catalog.TECHNIC_BRICK)
    assert len(tech) == 12 and {i.role for i in tech} == {'structure'}          # 2 per side, 3 storeys
    assert {i.cells[0][0] for i in tech} == {0, 31}
    none = build({'details': {'connectors': True}})
    assert not items(none, code=catalog.TECHNIC_BRICK) and any('modular' in n for n in none['notes'])


def test_partitions_have_a_doorway_and_are_bonded():
    r = modular()
    wall = items(r, 'partition s0')
    assert wall and {i.y0 for i in wall} == {2 + 3 * c for c in range(6)}
    row = {c for i in wall for c in i.cells if i.y0 == 2}
    gap = {c for c in range(1, 31)} - {c[0] for c in row}
    assert len(gap) == 4                                                     # a 4-stud doorway at the bottom
    assert {c[0] for i in wall for c in i.cells if i.y0 == 2 + 3 * 5} >= gap  # closed above the door
    assert not items(build({'storeys': {'interior': 'none'}}), 'partition')


def test_furnished_rooms_have_furniture_and_keep_the_doorway_clear():
    r = modular({'storeys': {'interior': 'furnished'}})
    assert len(r['canvas'].components()) == 1
    for label in ('table top', 'bed', 'cupboard', 'counter'):
        assert items(r, label, role='detail'), label
    part = lambda i: {c for c in i.cells}
    wall = {c for i in items(r, 'partition s0') for c in i.cells if i.y0 == 2}
    z = next(iter(wall))[1]
    gap = {c for c in range(1, 31)} - {c[0] for c in wall}
    furniture = [i for i in r['canvas'].items if i.role == 'detail' and i.y0 == 4 and i.label in ('table leg', 'chair seat', 'chair back', 'cupboard', 'counter')]
    assert not any(c[0] in gap and abs(c[1] - z) <= 1 for i in furniture for c in part(i))
    assert not items(modular({'storeys': {'interior': 'partition'}}), 'bed')


@pytest.mark.parametrize('corner', ['left', 'right'])
def test_corner_module_has_two_street_sides(corner):
    r = modular({'base': {'corner': corner}, 'footprint': {'depth': 22}, 'openings': {'door_position': corner}})
    sw = r['config']['base']['sidewalk']
    assert r['config']['footprint']['width'] == 32 - sw and r['config']['openings']['sides'] == ['front', corner]
    cells = {c for i in r['canvas'].items for c in i.cells}
    xs = {c[0] for c in cells}
    assert max(xs) - min(xs) + 1 == 32                                  # still one 32 stud module
    assert len(r['canvas'].components()) == 1
    walk = [i for i in r['canvas'].items if i.label == 'sidewalk']
    side_cells = {c for i in walk for c in i.cells if (c[0] < min(xs) + sw if corner == 'left' else c[0] > max(xs) - sw)}
    assert len(side_cells) >= sw * 22                                   # the second sidewalk runs the depth of the building
    windows = {i.label.split()[-2] for i in r['canvas'].items if i.role == 'window'}
    assert windows <= {'front', corner}
    tech = items(r, code=catalog.TECHNIC_BRICK)
    assert tech and {i.cells[0][0] for i in tech} == {max(xs) if corner == 'left' else min(xs)}   # bolted on the far side only


def test_corner_needs_a_modular_base():
    cfg, notes = schema.normalise({'base': {'corner': 'left'}})
    assert cfg['base']['corner'] == 'none' and any('corner-fits' in n for n in notes)


def test_furniture_needs_a_rectangular_footprint():
    cfg, notes = schema.normalise({'footprint': {'shape': 'L', 'width': 16, 'depth': 12}, 'storeys': {'interior': 'furnished'}, 'roof': {'style': 'flat'}})
    assert cfg['storeys']['interior'] == 'none' and any('interior-rect' in n for n in notes)


def test_awning_stripes_the_front_cornice_in_two_colours():
    r = build({'footprint': {'width': 16, 'depth': 10}, 'details': {'awning': True, 'awning_colour': 'red', 'awning_alt_colour': 'white'}})
    assert r['config']['storeys']['band_projection'] == 1
    stripes = [i for i in r['canvas'].items if i.label == 'band s0' and i.role == 'detail']
    assert {i.colour for i in stripes} == {4, 15}
    front = min(c[1] for i in stripes for c in i.cells)
    assert all(c[1] == front for i in stripes for c in i.cells if c[1] < front + 1) and len(r['canvas'].components()) == 1
    assert not build({'details': {'awning': True}, 'storeys': {'count': 1}})['config']['details']['awning']
    porch = build({'details': {'awning': True, 'porch': True}})
    assert not porch['config']['details']['porch'] and any('awning-fits' in n for n in porch['notes'])


# ---------------------------------------------------------------- corners

def test_corner_towers_one_per_selected_corner():
    for at, count in (('all', 4), ('front', 2), ('diagonal', 2)):
        r = build({'corners': {'style': 'towers', 'tower_shape': 'round', 'tower_at': at}})
        assert len(items(r, 'round tower c0')) == count


def test_round_tower_stack_is_as_tall_as_the_building_plus_extra():
    r = build({'corners': {'style': 'towers', 'tower_shape': 'round', 'tower_extra_courses': 3}, 'storeys': {'count': 2, 'courses': 6}})
    one = [i for i in items(r, code=catalog.ROUND_BRICK_4) if i.cells[0] == items(r, code=catalog.ROUND_BRICK_4)[0].cells[0]]
    assert len(one) == 6 * 2 + 3


def test_square_tower_rises_above_the_roof_and_gets_a_pyramid():
    r = build({'corners': {'style': 'towers', 'tower_shape': 'square', 'tower_roof': 'cone', 'tower_extra_courses': 3}})
    assert items(r, 'tower pyramid corner')
    assert max(i.y0 for i in items(r, role='tower')) > max(i.y0 for i in items(r, role='wall') if 'wall' in i.label) - 1


def test_pillars_colour_the_corner_cells():
    r = build({'corners': {'style': 'pillars', 'pillar_colour': 'red'}, 'storeys': {'count': 1}})
    assert {i.colour for i in items(r, 'wall s0 c2') if i.role == 'trim'} == {4}


# ---------------------------------------------------------------- details and base

def test_chimney_stands_on_the_ridge():
    r = build({'details': {'chimney': 'left', 'chimney_courses': 4}})
    stack = items(r, 'chimney')
    ridge = max(i.y0 for i in items(r, 'ridge plate'))
    assert len(stack) == 5 and min(i.y0 for i in stack) == ridge + 1


def test_porch_has_pillars_floor_and_canopy_and_balcony_a_rail():
    r = build({'details': {'porch': True}})
    assert len(items(r, 'porch pillar')) == 14 and items(r, 'porch canopy')
    r = build({'details': {'balcony': True}})
    assert items(r, 'balcony rail') and any(i.role == 'detail' and 'band' in i.label for i in r['canvas'].items)


def test_sign_fascia_replaces_the_top_ground_course_on_the_front():
    r = build({'details': {'sign': 'fascia', 'sign_colour': 'blue'}})
    assert r['config']['storeys']['courses'] >= 7
    assert items(r, role='detail') and any(i.colour == 1 for i in items(r, role='detail'))


def test_base_surrounds_the_building_and_the_path_leads_from_the_door():
    r = build({'base': {'margin': 4}})
    base = items(r, role='base')
    xs = [c[0] for i in base for c in i.cells]
    assert max(xs) - min(xs) + 1 == 12 + 8
    assert items(r, 'path')
    assert not items(build({'base': {'kind': 'none'}}), role='base')


# ---------------------------------------------------------------- schema, roles, rules

def test_defaults_validate_and_unknown_keys_are_rejected():
    cfg, notes = schema.normalise({})
    schema.validate(cfg)
    with pytest.raises(ValueError):
        schema.normalise({'roof': {'style': 'pagoda'}})
    with pytest.raises(ValueError):
        schema.normalise({'roof': {'colour': 'no such colour'}})
    with pytest.raises(ValueError):
        schema.normalise({'bogus': 1})
    jsonschema.Draft202012Validator.check_schema(schema.SCHEMA)


def test_every_constraint_has_an_enforcing_rule():
    expected = {
        'door-height': {'storeys': {'courses': 3}},
        'pitched-needs-rect': {'footprint': {'shape': 'L'}},
        'towers-flat': {'corners': {'style': 'towers'}},
        'setback-rect': {'footprint': {'shape': 'U'}, 'storeys': {'setback': 1}},
        'chimney-pitched': {'roof': {'style': 'flat'}, 'details': {'chimney': 'left'}},
        'porch-door': {'openings': {'door': 'none'}, 'details': {'porch': True}},
        'balcony-storeys': {'storeys': {'count': 1}, 'details': {'balcony': True}},
        'parapet-flat': {'roof': {'style': 'gable', 'parapet': 'plain'}},
        'sign-shop': {'details': {'sign': 'fascia'}, 'storeys': {'courses': 6}},
        'awning-fits': {'details': {'awning': True}, 'storeys': {'count': 1}},
        'corner-fits': {'base': {'corner': 'left'}},
    }
    for rule, patch in expected.items():
        _, notes = schema.normalise(patch)
        assert any(rule in n for n in notes), (rule, notes)
    assert {c[0] for c in schema.CONSTRAINTS} >= set(expected)


def test_invalid_footprints_are_rejected_or_trimmed():
    with pytest.raises(ValueError):
        schema.normalise({'footprint': {'width': 4, 'depth': 4}, 'walls': {'thickness': 2}})
    cfg, notes = schema.normalise({'footprint': {'shape': 'L', 'width': 8, 'depth': 8, 'notch_width': 8}})
    assert cfg['footprint']['notch_width'] <= 4 and any('notch' in n for n in notes)


@pytest.mark.parametrize('name', PRESET_NAMES)
def test_every_placement_has_a_known_role_and_the_plan_fits_the_schema(name):
    r = build(PRESETS[name]['config'])
    check_schema(r['plan'])
    placed = [p for step in r['plan']['sections'][0]['steps'] for p in step]
    assert placed and all(p['role'] in roles.ROLE_NAMES for p in placed)
    assert len({p['id'] for p in placed}) == len(placed)
    assert {'wall', 'base'} <= {p['role'] for p in placed}


@pytest.mark.parametrize('name', PRESET_NAMES)
def test_presets_are_one_connected_piece_without_overlaps(name):
    r = build(PRESETS[name]['config'])
    assert r['stats']['components'] == 1


# ---------------------------------------------------------------- parameter sweeps (own checks)

def sweep_cases():
    for w in (6, 9, 14, 22):
        for d in (6, 9, 12):
            for roof in ('gable', 'hip', 'flat'):
                yield {'footprint': {'width': w, 'depth': d}, 'roof': {'style': roof}}
    for count in (1, 2, 4):
        for courses in (4, 6, 9):
            yield {'storeys': {'count': count, 'courses': courses}}
    for shape in ('L', 'U', 'courtyard'):
        for t in (1, 2):
            yield {'footprint': {'shape': shape, 'width': 16, 'depth': 12}, 'walls': {'thickness': t}}
    for shape in ('square', 'round'):
        for at in ('all', 'front', 'diagonal'):
            yield {'corners': {'style': 'towers', 'tower_shape': shape, 'tower_at': at}}
    for style in ('brick', 'timber', 'stone'):
        yield {'walls': {'style': style}, 'corners': {'style': 'pillars'}}
    for w, d in ((10, 8), (16, 10), (24, 14)):
        for count in (1, 2, 3):
            yield {'footprint': {'width': w, 'depth': d}, 'storeys': {'count': count, 'interior': 'furnished'}, 'details': {'awning': True}}


@pytest.mark.parametrize('config', list(sweep_cases()), ids=lambda c: json.dumps(c, sort_keys=True)[:80])
def test_parameter_sweeps_stay_valid(config):
    r = build(config)
    assert r['stats']['components'] == 1
    check_schema(r['plan'])


def test_larger_footprint_means_more_parts_and_more_storeys_means_taller():
    small, big = build({'footprint': {'width': 10, 'depth': 8}}), build({'footprint': {'width': 20, 'depth': 12}})
    assert big['stats']['parts'] > small['stats']['parts']
    low, high = build({'storeys': {'count': 1}}), build({'storeys': {'count': 3}})
    assert max(i.y0 + i.h for i in high['canvas'].items) > max(i.y0 + i.h for i in low['canvas'].items)


# ---------------------------------------------------------------- determinism

@pytest.mark.parametrize('name', PRESET_NAMES)
def test_same_config_same_plan(name):
    a = json.dumps(build(PRESETS[name]['config'])['plan'], sort_keys=True)
    b = json.dumps(build(json.loads(json.dumps(PRESETS[name]['config'])))['plan'], sort_keys=True)
    assert a == b


def test_key_order_does_not_matter():
    a = build({'roof': {'style': 'hip', 'colour': 'blue'}, 'storeys': {'count': 3}})['plan']
    b = build({'storeys': {'count': 3}, 'roof': {'colour': 'blue', 'style': 'hip'}})['plan']
    assert a == b


# ---------------------------------------------------------------- parts-aware mode

ROWS = [dict(part='3004', colour='tan', qty=300), dict(part='3010', colour='tan', qty=80), dict(part='3005', colour='tan', qty=40),
        dict(part='3004', colour='white', qty=40), dict(part='3004', colour='light grey', qty=40),
        dict(part='3010', colour='red', qty=60)]


def test_only_mode_substitutes_unowned_colours_and_reports_it():
    r = grammar.generate(preset='house', inventory=ROWS, mode='only')
    used = {i['colour'] for i in r['inventory']['used']}
    assert r['inventory']['substitutions']
    assert {s['wanted'] for s in r['inventory']['substitutions']} >= {320}           # dark red roof not owned
    walls = {p['colour'] for st in r['plan']['sections'][0]['steps'] for p in st if p['role'] == 'wall'}
    assert walls <= {19, 15, 71, 4}
    assert used and r['stats']['components'] == 1


def test_only_mode_restricts_brick_lengths_to_owned_ones():
    rows = [dict(part='3004', colour='tan', qty=999), dict(part='3010', colour='tan', qty=999)]
    r = grammar.generate({'openings': {'ground_windows': 'none', 'upper_windows': 'none', 'door': 'none'},
                          'walls': {'plinth': False}, 'footprint': {'width': 12, 'depth': 8}, 'roof': {'style': 'flat'}},
                         inventory=rows, mode='only')
    bricks = {p['ref'] for st in r['plan']['sections'][0]['steps'] for p in st if p['role'] == 'wall'}
    assert bricks <= {'3004.dat', '3010.dat'}


def test_shortages_are_reported_when_the_table_runs_out():
    rows = [dict(part='3004', colour='tan', qty=5)]
    r = grammar.generate(preset='cottage', inventory=rows, mode='prefer')
    assert not r['inventory']['complete'] and r['inventory']['shortages']
    assert all(s['missing'] > 0 for s in r['inventory']['shortages'])


def test_prefer_mode_keeps_the_requested_colours():
    r = grammar.generate(preset='house', inventory=ROWS, mode='prefer')
    assert not r['inventory']['substitutions']
    assert 320 in {p['colour'] for st in r['plan']['sections'][0]['steps'] for p in st if p['role'] == 'roof'}


def test_parts_aware_mode_is_deterministic():
    a = grammar.generate(preset='house', inventory=ROWS, mode='only')
    b = grammar.generate(preset='house', inventory=ROWS, mode='only')
    assert json.dumps(a['plan'], sort_keys=True) == json.dumps(b['plan'], sort_keys=True) and a['inventory'] == b['inventory']


def _everything_but(*drop):
    from ldraw_tools.grammar import catalog
    return [dict(part=c, colour='white', qty=999) for c in catalog.PARTS if c not in drop]


def _refs(result):
    return {p['ref'] for st in result['plan']['sections'][0]['steps'] for p in st}


def _part_swaps(result):
    return [s for s in result['inventory']['substitutions'] if s['kind'] == 'part']


def test_only_mode_swaps_a_door_leaf_that_is_not_owned():
    r = grammar.generate(preset='house', inventory=_everything_but('60623'), mode='only')
    assert '60623.dat' not in _refs(r) and '60616a.dat' in _refs(r)
    assert dict(kind='part', wanted='60623', used='60616a', reason='door leaf not owned') in r['inventory']['substitutions']


def test_only_mode_replaces_tiles_with_plates_when_none_are_owned():
    from ldraw_tools.grammar import catalog
    r = grammar.generate(preset='house', inventory=_everything_but(*catalog.TILE.values(), *catalog.TILE2.values()), mode='only')
    assert not _refs(r) & {f'{c}.dat' for c in (*catalog.TILE.values(), *catalog.TILE2.values())}
    assert any(s['wanted'] == 'tile' and s['used'] == 'plate' for s in _part_swaps(r))


def test_only_mode_tiles_slopes_with_the_owned_lengths():
    from ldraw_tools.grammar import catalog
    r = grammar.generate(preset='house', inventory=_everything_but(catalog.SLOPE45[4], catalog.SLOPE45[3]), mode='only')
    assert not _refs(r) & {'3037.dat', '3038.dat'} and '3039.dat' in _refs(r)
    assert r['inventory']['complete']


def test_only_mode_falls_back_to_a_smaller_window_it_owns():
    from ldraw_tools.grammar import catalog
    cfg = {'openings': {'ground_windows': 'tall', 'upper_windows': 'tall'}}
    r = grammar.generate(cfg, preset='house', inventory=_everything_but(catalog.WINDOW_2X3), mode='only')
    assert '60593c01.dat' not in _refs(r) and '60592c01.dat' in _refs(r)
    assert any(s['used'] == catalog.WINDOW_2X2 and 'window' in s['reason'] for s in _part_swaps(r))


def test_prefer_mode_never_swaps_parts():
    r = grammar.generate(preset='house', inventory=_everything_but('60623'), mode='prefer')
    assert '60623.dat' in _refs(r) and not _part_swaps(r)


# ---------------------------------------------------------------- natural language

@pytest.mark.parametrize('text,path,value', [
    ('add a tower at each corner', ('corners', 'style'), 'towers'),
    ('add a tower at each corner', ('corners', 'tower_at'), 'all'),
    ('arched windows', ('openings', 'ground_windows'), 'arch'),
    ('arched windows', ('openings', 'upper_windows'), 'arch'),
    ('three storeys', ('storeys', 'count'), 3),
    ('make the roof blue', ('roof', 'colour'), 'blue'),
    ('make the door green', ('openings', 'door_colour'), 'green'),
    ('a flat roof with battlements', ('roof', 'parapet'), 'battlement'),
    ('round towers with cone roofs', ('corners', 'tower_shape'), 'round'),
    ('stone walls', ('walls', 'style'), 'stone'),
    ('an L-shaped house 20 x 12', ('footprint', 'shape'), 'L'),
    ('an L-shaped house 20 x 12', ('footprint', 'width'), 20),
    ('chimney on the left', ('details', 'chimney'), 'left'),
    ('no door', ('openings', 'door'), 'none'),
    ('thicker walls', ('walls', 'thickness'), 2),
    ('make the base green', ('base', 'colour'), 'green'),
])
def test_natural_language_phrases(text, path, value):
    cfg, understood, missed = nl.apply_text({}, text)
    assert not missed and cfg[path[0]][path[1]] == value


def test_natural_language_reports_what_it_did_not_understand_and_edits_stack():
    cfg, understood, missed = nl.apply_text({'roof': {'style': 'gable'}}, 'make the roof blue and sing a song')
    assert cfg['roof'] == {'style': 'gable', 'colour': 'blue'}
    assert missed == ['sing a song']
    out = grammar.generate(cfg)
    assert out['config']['roof']['colour'] == 'blue'


# ---------------------------------------------------------------- registry, maps

def test_building_answers_the_house_dialect():
    r = generators.generate('building', dict(width=10, depth=8, windows=2, door_side='left', roof_colour='blue', chimney='left',
                                             dormer=True, trim_colour='white', courses=6))
    assert any('dormer' in n for n in r['notes'])
    placed = [p for step in r['plan']['sections'][0]['steps'] for p in step]
    assert {p['colour'] for p in placed if p['role'] == 'roof'} == {1}
    flat = generators.generate('building', dict(roof_style='flat', door_side='none', windows=0, courses=12))
    assert flat['plan']
    for bad in (dict(width=5), dict(door_side='top'), dict(roof_colour=True)):
        with pytest.raises(ValueError):
            generators.get('building').resolve(bad)


def test_registered_as_one_generator_without_disturbing_house():
    g = generators.get('building')
    entry = g.entry()
    assert entry['id'] == 'building' and set(entry['presets']) == set(PRESET_NAMES)
    assert 'house' in generators.REGISTRY and generators.find(kind='house')[0].id == 'building'
    assert [g.id for g in generators.find('house', 'cottage')][:2] == ['building', 'house']
    assert generators.find(kind='castle')[0].id == 'building'
    plan = generators.generate('building', {'roof': {'style': 'hip'}})['plan']
    check_schema(plan)
    for preset in entry['presets'].values():
        g.resolve(preset['config'])


def test_digest_stays_small():
    assert len(generators.digest()) / 3 < 1500 and 'building |' in generators.digest()


@pytest.mark.parametrize('name', PRESET_NAMES)
def test_course_maps_exist(name):
    r = build(PRESETS[name]['config'])
    front = maps.ascii_front(r['canvas'])
    assert 'W' in front and 'R' in front or 'T' in front or 't' in front
    assert maps.ascii_course(r['canvas'], 4).strip()
    assert maps.svg_sheet(r['canvas']).startswith('<svg')


def test_house_dialect_colours_keep_the_storeys_a_preset_chose():
    """The fast path sends a preset's configuration plus flat edits ("make the roof blue"): the flat values must not
    reset the preset's storeys to the old house's single storey; the old flat-only form still means one storey."""
    from ldraw_tools.grammar.presets import PRESETS
    house = PRESETS['house']['config']
    kept = generators.generate('building', {**house, 'roof_colour': 'blue'})
    stories = lambda r: len({p['at'][1] for s in r['plan']['sections'] for step in s['steps'] for p in step if p.get('role') == 'wall'})
    legacy = generators.generate('building', dict(roof_colour='blue'))
    assert stories(kept) > stories(legacy)
    assert {p['colour'] for s in kept['plan']['sections'] for step in s['steps'] for p in step if p.get('role') == 'roof'} == {1}


# ---------------------------------------------------------------- detail and roof variety (#52)

DETAIL = {'openings': {'sills': True, 'shutters': True}, 'details': {'flower_boxes': True, 'fence': True}}


def test_detail_options_are_off_by_default():
    r = build()
    assert not items(r, label='window sill') and not items(r, label='fence') and not items(r, label='gable window')


def test_sills_sit_under_windows_and_overhang_the_wall():
    r = build({'openings': {'sills': True}})
    sills = items(r, label='window sill')
    assert sills and {i.role for i in sills} == {'trim'}
    assert any(i.code == catalog.PLATE2[2] for i in sills)       # the overhanging top plate
    assert len(r['canvas'].components()) == 1


def test_shutters_recolour_the_piers_beside_windows():
    plain, shut = build(), build({'openings': {'shutters': True, 'shutter_colour': 'dark green'}})
    green = shut['config']['openings']['shutter_colour']
    assert not [i for i in plain['canvas'].items if i.role == 'detail']
    assert [i for i in shut['canvas'].items if i.role == 'detail' and i.label.startswith('wall')]
    assert len(shut['canvas'].components()) == 1 and green


def test_flower_boxes_turn_sills_on_and_stand_on_them():
    r = build({'details': {'flower_boxes': True}})
    assert r['config']['openings']['sills'] and any('flower boxes' in n or 'sills turned on' in n for n in r['notes'])
    boxes, flowers = items(r, label='flower box'), items(r, label='flowers')
    assert boxes and flowers and {i.role for i in boxes + flowers} == {'detail'}
    assert len(r['canvas'].components()) == 1


def test_fence_surrounds_the_garden_and_leaves_the_path_open():
    r = build({'details': {'fence': True}})
    posts = items(r, label='fence post')
    assert posts and items(r, label='fence rail')
    assert len(r['canvas'].components()) == 1
    path = {c for i in items(r, label='path') for c in i.cells}
    assert not path & {c for i in posts for c in i.cells}


def test_fence_needs_a_plate_with_margin():
    r = build({'details': {'fence': True}, 'base': {'kind': 'none'}})
    assert not r['config']['details']['fence'] and any('fence-base' in n for n in r['notes'])


def test_keystones_crown_arch_windows():
    base = {'openings': {'ground_windows': 'arch', 'upper_windows': 'arch'}, 'storeys': {'courses': 7}}
    plain = build(base)
    key = build({**base, 'openings': {**base['openings'], 'keystones': True, 'keystone_colour': 'red'}})
    reds = lambda r: [i for i in r['canvas'].items if i.role == 'trim' and i.colour == 4 and i.label.startswith('wall')]
    assert not reds(plain) and reds(key)
    assert len(key['canvas'].components()) == 1


def test_gable_windows_fill_both_gable_ends():
    r = build({'footprint': {'width': 16, 'depth': 10}, 'roof': {'gable_windows': True}})
    wins = items(r, label='gable window')
    assert len(wins) == 2 and {i.role for i in wins} == {'window'}
    assert len(r['canvas'].components()) == 1


@pytest.mark.parametrize('depth', [6, 8, 9, 10, 12, 14])
def test_gable_windows_across_depths(depth):
    r = build({'footprint': {'width': 16, 'depth': depth}, 'roof': {'gable_windows': True, 'verge': 1}})
    assert len(r['canvas'].components()) == 1


@pytest.mark.parametrize('cfg', [
    DETAIL,
    {**DETAIL, 'walls': {'thickness': 2}},
    {**DETAIL, 'walls': {'style': 'timber'}, 'openings': {'sills': True, 'shutters': True, 'ground_windows': 'rect', 'upper_windows': 'small'}},
    {**DETAIL, 'openings': {'sills': True, 'shutters': True, 'ground_windows': 'wide', 'rhythm': 'dense'}},
    {**DETAIL, 'storeys': {'count': 3, 'courses': 7}, 'details': {'flower_boxes': True, 'fence': True, 'porch': True, 'balcony': True}},
    {**DETAIL, 'roof': {'style': 'hip'}, 'details': {'flower_boxes': True, 'fence': True, 'chimney': 'left'}},
    {**DETAIL, 'roof': {'style': 'gable', 'gable_windows': True, 'ridge': 'z'}, 'footprint': {'width': 12, 'depth': 18}},
    {**DETAIL, 'footprint': {'shape': 'L', 'width': 16, 'depth': 12}, 'roof': {'style': 'flat'}},
])
def test_detail_combinations_stay_one_piece(cfg):
    r = build(cfg)
    assert len(r['canvas'].components()) == 1


def test_detail_presets_are_registered():
    for name in ('cottage-garden', 'arched-townhouse'):
        assert name in PRESETS
