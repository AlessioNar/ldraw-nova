"""Make a template from a build: the quality gate, the role preview and the derivation job (docs/plans/active/build-templates, section 6).

    gate(plan, parts)                                   # is this build sound enough to vary?
    preview(plan, parts, hints=..., corrections=...)    # roles of every part, a role-coloured model, low-confidence parts
    make(plan, parts, id=..., title=..., ...)           # derive, then describe the knobs found (and their extreme values)

The workspace calls these through the build worker (`pipeline.py`, op "template"); nothing here talks to a model.
"""
from __future__ import annotations

import copy
import re

from .. import role_edits, role_vocab, roles, subroles
from ..builder import build_plan
from . import derive
from .core import from_data

# A build is offered as a source only when it is sound: no validation errors, few loose pieces, a size the derivation
# can verify in reasonable time (every offered value is built and checked; about 0.5-40 minutes).
MIN_PARTS = 8
MAX_PARTS = 1500
MAX_COMPONENT_RATIO = 0.25          # disconnected components per part; the shipped templates sit between 0.01 and 0.18
MAX_REASONS = 5

# One colour per role for the preview (LDraw codes). Unknown roles fall back to FALLBACK_COLOUR.
ROLE_PALETTE = {'roof': 4, 'wall': 19, 'window': 1, 'door': 70, 'base': 2, 'wheel': 14, 'tyre': 0, 'chassis': 72, 'body': 25,
                'glass': 11, 'wing': 22, 'engine': 26, 'trim': 15, 'detail': 13, 'structure': 71}
FALLBACK_COLOUR = 71
# Stripes of one role are shown in different colours so the person can tell the colour groups apart (a is the role's own).
STRIPE_PALETTE = {'a': None, 'b': 14, 'c': 3}
LOW_CONFIDENCE = 0.5
MAX_LOW = 60


def _placements(plan):
    return [(s, e) for s in plan['sections'] for step in s['steps'] for e in step]


def _leaf_count(plan):
    sections = {s['name'].casefold() for s in plan['sections']}
    return sum(1 for _, e in _placements(plan) if e['ref'].casefold() not in sections)


def gate(plan, parts):
    """{ok, reasons, parts, errors, components}: the quality gate that decides whether "Make this a template" is offered."""
    count = _leaf_count(plan)
    out = dict(ok=False, reasons=[], parts=count, errors=0, components=0)
    if count < MIN_PARTS:
        out['reasons'].append(f'Only {count} parts: there is too little to vary (at least {MIN_PARTS}).')
        return out
    if count > MAX_PARTS:
        out['reasons'].append(f'{count} parts is more than can be verified in reasonable time (at most {MAX_PARTS}).')
        return out
    try:
        errors, components = derive.check_plan(plan, parts)
    except ValueError as exc:               # the plan does not even build (an unknown part, a loop of submodels)
        out['reasons'].append(f'The build does not build: {exc}')
        return out
    out.update(errors=len(errors), components=components)
    if errors:
        out['reasons'] += [f'Validation error: {message}' for message in errors[:MAX_REASONS]]
        if len(errors) > MAX_REASONS:
            out['reasons'].append(f'... and {len(errors) - MAX_REASONS} more validation errors.')
    if components > max(1, MAX_COMPONENT_RATIO * count):
        out['reasons'].append(f'{components} loose groups of parts for {count} parts: the build is not connected enough '
                              f'to stretch or shrink soundly (at most {MAX_COMPONENT_RATIO:.0%} of its part count).')
    out['ok'] = not out['reasons']
    return out


def apply_corrections(plan, corrections):
    """Copy of the plan with the person's role corrections stamped on the placements ([{section, id, role}]).

    A stamped role is kept by every later classification (`tag_plan` only fills placements without one).
    An empty section means the root one (what the canvas selects). Unknown sections, ids and roles are ignored; the
    return value is (plan, number applied).
    """
    out = copy.deepcopy(plan)
    root = out['sections'][0]['name']
    index = {(s['name'], e['id']): e for s, e in _placements(out)}
    applied = 0
    for item in corrections or ():
        role = str(item.get('role', '')).strip().lower() if item.get('role') else None
        if role not in role_vocab.SUBROLES:                 # a stripe is a role of its own; other names fold into their role
            role = role_vocab.canonical(role) if role else None
        entry = index.get((item.get('section') or root, item.get('id')))
        if entry is not None and role_vocab.is_role(role):
            entry['role'] = role
            applied += 1
    return out, applied


def prepare(plan, parts, *, hints='', corrections=None, facts=None):
    """(plan with a role on every part, report): the person's corrections first, the classifier for the rest."""
    corrected, applied = apply_corrections(plan, corrections)
    tagged, report = roles.tag_plan(corrected, parts, hints=hints, facts=facts)
    root = tagged['sections'][0]['name']
    fixed = {(c.get('section') or root, c.get('id')) for c in corrections or ()}     # the person's click is never moved
    tagged = subroles.split_stripes(tagged, keep=fixed)
    report['corrected'] = applied
    report['stripes'] = subroles.stripes_of(tagged)
    return tagged, report


def role_colour(role):
    """Preview colour of a role or sub-role (LDraw code)."""
    parent = role_vocab.SUBROLES.get(role)
    base = ROLE_PALETTE.get(roles.canonical(parent or role or 'structure'), FALLBACK_COLOUR)
    return (STRIPE_PALETTE.get(role.rsplit('-', 1)[-1]) or base) if parent else base


def recoloured(plan):
    """Copy of a tagged plan with every part in its role's colour (the preview and its legend)."""
    out = copy.deepcopy(plan)
    sections = {s['name'].casefold() for s in out['sections']}
    for _, entry in _placements(out):
        if entry['ref'].casefold() not in sections:
            entry['colour'] = role_colour(entry.get('role') or 'structure')
    return out


def preview(plan, parts, *, hints='', corrections=None):
    """What the role step shows: the role summary, the parts the classifier is unsure about, the role-coloured model (MPD)."""
    facts = roles.PartFacts(parts)
    tagged, report = prepare(plan, parts, hints=hints, corrections=corrections, facts=facts)
    summary = role_edits.role_summary(tagged)
    low = []
    stamped = {(s['name'], e['id']): e for s, e in _placements(tagged)}
    for row in sorted(report['low_confidence'], key=lambda r: r['confidence'])[:MAX_LOW]:
        entry = stamped.get((row['section'], row['id']))
        if entry is None:
            continue
        info = facts.get(entry['ref'])
        low.append(dict(section=row['section'], id=row['id'], key=f"{row['section']}:{row['id']}", role=row['role'],
                        confidence=round(row['confidence'], 2), part=entry['ref'], name=info['desc'] if info else entry['ref'],
                        colour=entry['colour']))
    text = build_plan(recoloured(tagged), parts, validate=False)[0]
    return dict(domain=report['domain'], roles=summary, low_confidence=low, low_total=len(report['low_confidence']),
                palette={r: role_colour(r) for r in summary}, vocabulary=[*roles.ROLES, *role_vocab.SUBROLES],
                stripes=report['stripes'],
                corrected=report['corrected'], mpd=text)


# --------------------------------------------------------------------------------------------------------- knobs

SIZE_PARAMS = ('x_delta', 'z_delta', 'x_repeat', 'z_repeat', 'courses_delta')


def _label(name, spec, data):
    """The knob's name for the person, in the template's own terms."""
    if name in ('x_delta', 'z_delta'):
        axis = name[0]
        return f'Length along {axis.upper()}', 'studs'
    if name in ('x_repeat', 'z_repeat'):
        return f'Repeated section along {name[0].upper()}', f"copies of a {spec.get('x-slice-studs', '?')}-stud section"
    if name == 'courses_delta':
        return 'Wall courses', 'courses'
    if name.startswith('swap_'):
        return f"Part swap: {name[5:].replace('_', ' ')}", None
    if name.startswith('include_'):
        return data['groups'][name[8:]]['label'].capitalize(), None
    return name, None


def _extremes(name, spec):
    """[(value, phrase)] of the values worth a thumbnail: both ends of a size range, the alternative of a swap or group."""
    if spec.get('type') == 'boolean':
        return [(False, 'removed')]
    options = spec.get('enum') or []
    if name.startswith('swap_'):
        labels = spec.get('x-labels') or {}
        return [(o, f'as {labels.get(o, o)}') for o in options if o != spec['default']]
    numeric = sorted(o for o in options if isinstance(o, (int, float)))
    if not numeric:
        return []
    counted = name.endswith('repeat') or name == 'courses_delta'
    unit = {'courses_delta': 'courses', 'x_repeat': 'sections', 'z_repeat': 'sections'}.get(name, 'studs')
    out = []
    for value, word in ((numeric[0], 'fewer' if counted else 'shorter'), (numeric[-1], 'more' if counted else 'longer')):
        if value != spec['default']:
            out.append((value, f'{abs(value)} {word} {unit}' if counted else f'{abs(value)} {unit} {word}'))
    return out


def knobs_of(data, report):
    """The knobs the derivation verified: [{param, label, default, options, extremes: [{value, text}]}], and what it rejected."""
    out = []
    for name, spec in data['params'].items():
        if name.startswith('colour_'):
            continue
        label, unit = _label(name, spec, data)
        out.append(dict(param=name, label=label, unit=unit, default=spec.get('default'), options=spec.get('enum'),
                        boolean=spec.get('type') == 'boolean', description=spec.get('description', ''),
                        extremes=[dict(value=v, text=t) for v, t in _extremes(name, spec)]))
    rejected = []
    for key, tried in (report.get('rejected') or {}).items():
        if not tried:
            continue
        if key.startswith('include_'):
            rejected.append(dict(param=key, reason=f"cannot remove {key[8:].replace('_', ' ')}: something rests on it or the build breaks"))
        elif key == 'courses' and 'courses_delta' not in data['params']:
            rejected.append(dict(param=key, reason='cannot repeat or drop a wall course without breaking the build'))
        elif key in ('x', 'z') and f'{key}_delta' not in data['params']:
            rejected.append(dict(param=f'{key}_delta', reason=f'cannot lengthen along {key.upper()}: every cut crosses windows, '
                                                          f'doors or curved parts with no longer equivalent part'))
        elif key.endswith('_repeat') and key not in data['params']:
            rejected.append(dict(param=key, reason=f'cannot repeat a section along {key[0].upper()}: no section repeats cleanly'))
    return out, rejected


def without_knobs(data, names):
    """Copy of a template with the named knobs switched off (the baseline value is kept). Only switches off: a knob that
    was not verified is not in `data` and cannot be added."""
    out = copy.deepcopy(data)
    for name in names or ():
        if name not in out['params'] or name.startswith('colour_'):
            continue
        del out['params'][name]
        if name in ('x_delta', 'z_delta'):
            out['stretch'].pop(name[0], None)
        elif name in ('x_repeat', 'z_repeat'):
            out.get('repeat', {}).pop(name[0], None)
        elif name == 'courses_delta':
            out['courses'] = None
        elif name.startswith('swap_'):
            out['swaps'].pop(name[5:], None)
        elif name.startswith('include_'):
            out['groups'].pop(name[8:], None)
    out['coverage'] = derive._coverage(out)
    return out


# ---------------------------------------------------------------------------------------------------------- make

def slug(text):
    return re.sub(r'[^a-z0-9]+', '-', str(text).casefold()).strip('-')[:40] or 'template'


def make(plan, parts, *, id, title, kinds, families=('generic',), description='', source=None, hints='', corrections=None,
         progress=None):
    """(template data, report, knobs, rejected): the derivation of a build. `progress(step, detail)` is told where it is."""
    progress = progress or (lambda *_: None)
    facts = roles.PartFacts(parts)
    progress('roles', 'Classifying the parts')
    tagged, tag_report = prepare(plan, parts, hints=hints or description or title, corrections=corrections, facts=facts)

    def log(step, *rest):
        progress(step, ' '.join(str(r) for r in rest if r is not None)[:200])

    data, report = derive.derive(tagged, parts, id=id, title=title, families=list(families), kinds=list(kinds),
                                 description=description or title, source=source or {}, hints=hints or description or title,
                                 facts=facts, log=log, stripes=False)      # prepare() already split the stripes
    report['corrected'] = tag_report['corrected']
    knobs, rejected = knobs_of(data, report)
    return derive.compact(data), report, knobs, rejected


def variant_plan(data, param, value):
    """The plan of the template with one knob at `value` and the rest at their defaults."""
    return from_data(data).generate({param: value})['plan']
