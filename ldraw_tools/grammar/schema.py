"""The machine-readable ontology: components, parameters, defaults, ranges, and the rules that keep combinations valid.

SCHEMA is plain JSON Schema (draft 2020-12) with two extra keywords the forms and the engine use:
  x-component   which ontology component a group belongs to (Footprint, Storeys, Walls, Openings, Corners, Roof, Details, Base)
  x-colour      the value is an LDraw colour name or code
CONSTRAINTS lists the validity rules; `normalise` applies them and says what it changed.
"""
from __future__ import annotations

import copy

import jsonschema

from .colours import colour_code

SIDES = ['front', 'back', 'left', 'right']
WINDOW_KINDS = ['none', 'rect', 'small', 'tall', 'arch', 'slit', 'wide']


def colour(default, description):
    return {'type': ['string', 'integer'], 'x-colour': True, 'default': default, 'description': description}


def integer(default, lo, hi, description):
    return {'type': 'integer', 'minimum': lo, 'maximum': hi, 'default': default, 'description': description}


def choice(default, values, description):
    return {'enum': values, 'default': default, 'description': description}


def flag(default, description):
    return {'type': 'boolean', 'default': default, 'description': description}


def group(component, description, **props):
    return {'type': 'object', 'additionalProperties': False, 'x-component': component, 'description': description,
            'properties': props, 'default': {k: v['default'] for k, v in props.items()}}


SCHEMA = {
    '$schema': 'https://json-schema.org/draft/2020-12/schema',
    'title': 'Building grammar configuration',
    'type': 'object',
    'additionalProperties': False,
    'properties': {
        'name': {'type': 'string', 'default': 'building', 'maxLength': 60, 'pattern': '^[A-Za-z0-9 _.-]*$',
                 'description': 'Model name'},
        'footprint': group('Footprint', 'Plan shape of the ground floor',
            shape=choice('rect', ['rect', 'L', 'U', 'courtyard'], 'rect, L, U (two wings round a notch) or courtyard (ring round an open yard)'),
            width=integer(12, 4, 40, 'Studs along x (the front width)'),
            depth=integer(8, 4, 32, 'Studs along z'),
            notch_width=integer(4, 2, 20, 'L/U: width of the cut-out'),
            notch_depth=integer(4, 2, 16, 'L/U: depth of the cut-out'),
            notch_corner=choice('back-right', ['back-right', 'back-left', 'front-right', 'front-left'], 'L: which corner is cut away'),
            notch_side=choice('back', ['back', 'front'], 'U: which side the notch opens to'),
            court_width=integer(4, 2, 24, 'Courtyard: width of the open yard'),
            court_depth=integer(4, 2, 20, 'Courtyard: depth of the open yard')),
        'storeys': group('Storeys', 'Storeys stacked on the footprint; each is wall courses plus a floor band',
            count=integer(2, 1, 6, 'Number of storeys'),
            courses=integer(6, 3, 14, 'Wall height of each storey in brick courses (a door needs 6)'),
            top_courses=integer(0, 0, 14, 'Courses of the top storey; 0 = same as the others'),
            setback=integer(0, 0, 3, 'Studs each upper storey steps in on every side (rect footprints)'),
            floors=flag(True, 'Fill the interior with floor plates (off = ring bands only)'),
            band_projection=integer(0, 0, 1, 'Studs the floor bands project as a cornice'),
            eaves=flag(False, 'A small hip roof round every storey but the top (pagoda, temple); needs setback 1 or more'),
            band_colour=colour('white', 'Floor bands, floors and cornice')),
        'walls': group('Walls', 'Wall construction',
            style=choice('brick', ['brick', 'timber', 'stone'], 'brick; half-timber (posts and infill); stone (two-tone random bricks)'),
            thickness=choice(1, [1, 2], 'Wall thickness in studs; 2 uses staggered rows'),
            colour=colour('tan', 'Main wall colour'),
            alt_colour=colour('light grey', 'Second colour (stone mix, timber infill accent)'),
            timber_colour=colour('reddish brown', 'Timber posts and corner studs'),
            plinth=flag(False, 'First course in a plinth colour'),
            plinth_colour=colour('dark grey', 'Plinth colour'),
            accent_every=integer(0, 0, 6, 'Every n-th course in the accent colour (0 = none)'),
            accent_colour=colour('red', 'Accent course colour')),
        'openings': group('Openings', 'Doors and windows, sized from real parts',
            door=choice('plain', ['none', 'plain', 'smooth', 'glazed', 'barred'], 'Door leaf style (4 studs wide, 6 courses tall)'),
            door_facade=choice('front', SIDES, 'Facade the door is on'),
            door_position=choice('centre', ['centre', 'left', 'right'], 'Where the door sits along the facade'),
            door_colour=colour('reddish brown', 'Door leaf'),
            frame_colour=colour('white', 'Window and door frames'),
            glass_colour=colour('clear', 'Glass'),
            ground_windows=choice('tall', WINDOW_KINDS, 'Window kind on the ground floor (wide = two 4-stud windows side by side)'),
            upper_windows=choice('tall', WINDOW_KINDS, 'Window kind on upper floors'),
            rhythm=choice('regular', ['dense', 'regular', 'sparse'], 'Spacing: minimum pier 1, 2 or 3 studs between openings'),
            count=integer(0, 0, 12, 'Windows per facade (0 = as many as fit)'),
            sides={'type': 'array', 'uniqueItems': True, 'items': {'enum': SIDES}, 'default': list(SIDES),
                   'description': 'Facades that get windows'},
            above_door=flag(True, 'A window above the door on upper floors')),
        'corners': group('Corners', 'Building corners',
            style=choice('plain', ['plain', 'pillars', 'towers'], 'plain interlocking corners, coloured pillars, or corner towers'),
            pillar_colour=colour('white', 'Pillar/quoin colour'),
            tower_shape=choice('square', ['square', 'round'], 'Square tower (any size) or round tower (4x4 round bricks)'),
            tower_size=integer(4, 4, 6, 'Square tower size in studs (round towers are 4)'),
            tower_proud=integer(1, 0, 2, 'Studs the tower sticks out of the corner'),
            tower_extra_courses=integer(4, 0, 12, 'Courses the tower rises above the top storey band'),
            tower_at=choice('all', ['all', 'front', 'back', 'left', 'right', 'diagonal'], 'Which corners get a tower'),
            tower_roof=choice('flat', ['flat', 'cone'], 'Flat with battlements, or cone/pyramid'),
            tower_colour=colour('light grey', 'Tower walls'),
            tower_roof_colour=colour('dark red', 'Tower cone/pyramid'),
            tower_slits=flag(True, 'Arrow slits in the tower')),
        'roof': group('Roof', 'Roof or top finish',
            style=choice('gable', ['gable', 'hip', 'flat', 'none'], 'gable, hip, flat deck (with optional parapet), or none'),
            colour=colour('dark red', 'Roof slopes / deck'),
            ridge=choice('auto', ['auto', 'x', 'z'], 'Ridge direction (auto = along the longer side)'),
            overhang=integer(1, 0, 1, 'Eave overhang in studs'),
            verge=integer(0, 0, 1, 'Gable-end overhang in studs'),
            tiers=integer(0, 0, 9, 'Slope courses; 0 = as steep as the depth allows'),
            finial=flag(False, 'Hip roofs: a cone finial on the peak'),
            parapet=choice('none', ['none', 'plain', 'battlement'], 'Flat roofs: parapet or battlements'),
            merlon=integer(1, 1, 2, 'Battlement merlon width'),
            gap=integer(1, 1, 2, 'Battlement gap width'),
            parapet_courses=integer(1, 1, 2, 'Parapet height in courses'),
            crenel_colour=colour('tan', 'Parapet and merlon colour')),
        'details': group('Details', 'Add-ons',
            chimney=choice('none', ['none', 'left', 'centre', 'right'], 'Chimney on the ridge (gable/hip)'),
            chimney_colour=colour('dark grey', 'Chimney bricks'),
            chimney_courses=integer(3, 2, 6, 'Chimney height in courses'),
            porch=flag(False, 'Pillared porch over the door'),
            porch_colour=colour('white', 'Porch pillars and floor'),
            balcony=flag(False, 'Balcony over the door (needs 2+ storeys)'),
            balcony_colour=colour('white', 'Balcony floor and railing'),
            sign=choice('none', ['none', 'fascia'], 'Shop sign band above the ground floor'),
            sign_colour=colour('red', 'Sign band colour')),
        'base': group('Base', 'Ground',
            kind=choice('plate', ['none', 'plate'], 'Base plate under the building'),
            margin=integer(3, 0, 8, 'Studs of ground around the building'),
            colour=colour('green', 'Ground'),
            path=flag(True, 'Path from the door to the edge'),
            path_colour=colour('light grey', 'Path'),
            path_width=choice(2, [2, 4], 'Path width')),
    },
}

CONSTRAINTS = [
    ('door-height', 'A door is 6 courses tall, so the ground storey needs 6 or more courses'),
    ('pitched-needs-rect', 'Gable and hip roofs need a rectangular footprint; other shapes get a flat roof'),
    ('towers-flat', 'Corner towers need a flat main roof (a pitched roof would collide with them)'),
    ('setback-rect', 'Setbacks only apply to rectangular footprints'),
    ('thick-walls', 'Wall thickness t needs a footprint at least 2t+2 studs wide and deep'),
    ('notch-fits', 'L and U notches and courtyards must leave at least 4 studs of wall'),
    ('door-fits', 'A door needs a facade at least 8 studs long (door plus corner piers)'),
    ('window-height', 'A window needs its height plus one course of wall in the storey; otherwise a smaller kind is used'),
    ('chimney-pitched', 'A chimney needs a gable or hip roof'),
    ('porch-door', 'A porch needs a front door'),
    ('balcony-storeys', 'A balcony needs a door and at least 2 storeys'),
    ('parapet-flat', 'Parapets and battlements only exist on flat roofs and tower tops'),
    ('eaves-setback', 'Eave roofs round storeys need each upper storey to step in by 1 or more'),
    ('setback-fits', 'The top storey must stay at least 4 studs wide after all setbacks'),
    ('eaves-rect', 'Eave roofs only go round rectangular storeys'),
    ('sign-shop', 'A fascia sign needs a door and a ground storey of 7 or more courses'),
]


def defaults():
    out = {}
    for key, spec in SCHEMA['properties'].items():
        out[key] = copy.deepcopy(spec['default'])
    return out


def deep_merge(base, patch):
    out = copy.deepcopy(base)
    for k, v in (patch or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def validate(config):
    """Raise ValueError naming the first schema violation."""
    error = jsonschema.exceptions.best_match(jsonschema.Draft202012Validator(SCHEMA).iter_errors(config))
    if error is not None:
        raise ValueError(f"{'.'.join(map(str, error.path)) or 'config'}: {error.message}")


def colours_of(config):
    """[(path, colour spec)] of every colour parameter in a config."""
    out = []
    for gname, gspec in SCHEMA['properties'].items():
        if gspec.get('type') != 'object' or 'properties' not in gspec:
            continue
        for k, spec in gspec['properties'].items():
            if spec.get('x-colour'):
                out.append(((gname, k), config[gname][k]))
    return out


def normalise(config):
    """(complete config, notes): defaults filled, schema checked, validity rules applied (each change is a note)."""
    notes = []
    cfg = deep_merge(defaults(), config or {})
    validate(cfg)
    for path, value in colours_of(cfg):
        try:
            colour_code(value, glass=path[1] == 'glass_colour')
        except ValueError as exc:
            raise ValueError(f"{'.'.join(path)}: {exc}") from None
    fp, st, wl, op, co, rf, dt, bs = (cfg[k] for k in ('footprint', 'storeys', 'walls', 'openings', 'corners', 'roof', 'details', 'base'))
    t = wl['thickness']
    w, d = fp['width'], fp['depth']
    if min(w, d) < 2 * t + 2:
        raise ValueError(f'footprint {w}x{d} is too small for walls {t} studs thick (thick-walls)')
    if fp['shape'] == 'L':
        if fp['notch_width'] > w - 4 or fp['notch_depth'] > d - 4:
            fp['notch_width'], fp['notch_depth'] = min(fp['notch_width'], w - 4), min(fp['notch_depth'], d - 4)
            notes.append(f"notch limited to {fp['notch_width']}x{fp['notch_depth']} (notch-fits)")
    if fp['shape'] == 'U':
        if fp['notch_width'] > w - 8:
            fp['notch_width'] = max(2, w - 8)
            notes.append(f"notch width limited to {fp['notch_width']} (notch-fits)")
        fp['notch_depth'] = min(fp['notch_depth'], d - 4)
    if fp['shape'] == 'courtyard':
        fp['court_width'] = min(fp['court_width'], w - 2 * (t + 1))
        fp['court_depth'] = min(fp['court_depth'], d - 2 * (t + 1))
        if fp['court_width'] < 2 or fp['court_depth'] < 2:
            raise ValueError('courtyard footprint needs width and depth of at least 2t+4 (notch-fits)')
    if fp['shape'] != 'rect' and st['setback']:
        st['setback'] = 0
        notes.append('setback ignored: only rectangular footprints step in (setback-rect)')
    if st['eaves'] and fp['shape'] != 'rect':
        st['eaves'] = False
        notes.append('eaves removed: they only go round rectangular storeys (eaves-rect)')
    if st['eaves'] and not st['band_projection']:
        st['band_projection'] = 1
        notes.append('cornice added under the eave roofs: the corner slopes clip onto the projecting band (eaves-setback)')
    while st['setback'] and min(w, d) - 2 * st['setback'] * (st['count'] - 1) < 4 + 2 * t - 2:
        st['setback'] -= 1
        notes.append(f"setback reduced to {st['setback']}: the top storey would be narrower than 4 studs (setback-fits)")
    if st['eaves'] and st['count'] > 1 and st['setback'] < 1:
        st['setback'] = 1
        notes.append('setback raised to 1: the eave roof of a storey needs room beside the storey above (eaves-setback)')
    if op['door'] != 'none' and st['courses'] < 6:
        st['courses'] = 6
        notes.append('courses raised to 6: the door is six courses tall (door-height)')
    towers = co['style'] == 'towers'
    if rf['style'] in ('gable', 'hip'):
        if fp['shape'] != 'rect':
            rf['style'] = 'flat'
            notes.append(f"{fp['shape']} footprint gets a flat roof (pitched-needs-rect)")
        elif towers:
            rf['style'] = 'flat'
            notes.append('flat roof: corner towers and a pitched roof would collide (towers-flat)')
    if rf['style'] not in ('gable', 'hip') and dt['chimney'] != 'none':
        dt['chimney'] = 'none'
        notes.append('chimney removed: it needs a gable or hip roof (chimney-pitched)')
    if rf['style'] != 'flat' and rf['parapet'] != 'none':
        rf['parapet'] = 'none'
        notes.append('parapet removed: only flat roofs have one (parapet-flat)')
    if rf['style'] == 'flat' and rf['parapet'] == 'none' and towers:
        rf['parapet'] = 'battlement'
        notes.append('battlements added to the flat roof of a towered building (parapet-flat)')
    if op['door'] == 'none':
        for key, rule in (('porch', 'porch-door'), ('balcony', 'balcony-storeys')):
            if dt[key]:
                dt[key] = False
                notes.append(f'{key} removed: it needs a door ({rule})')
        if dt['sign'] != 'none':
            dt['sign'] = 'none'
            notes.append('sign removed: it needs a door (sign-shop)')
    if dt['balcony'] and st['count'] < 2:
        dt['balcony'] = False
        notes.append('balcony removed: it needs 2 or more storeys (balcony-storeys)')
    if dt['porch'] and op['door_facade'] != 'front':
        dt['porch'] = False
        notes.append('porch removed: it only goes on a front door (porch-door)')
    if dt['sign'] != 'none' and st['courses'] < 7:
        st['courses'] = 7
        notes.append('courses raised to 7: the sign band needs a course above the door (sign-shop)')
    return cfg, notes
