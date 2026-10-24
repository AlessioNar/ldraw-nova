"""Derive a template (colour roles, stretch cuts, wall slices, part swaps, optional sub-assemblies) from any model.

    from ldraw_tools.templates import derive
    data, report = derive.derive_from_mpd(text, parts, id='cottage', title='Cottage', families=['house'], kinds=['cottage'])

Nothing is hand-written per model. The model is flattened to its physical parts, roles come from the plan (or from
`roles.classify`), and every knob is offered only after the variant it produces was built and checked: no validation
error that the source did not already have, and no more disconnected components than the source.
"""
from __future__ import annotations

import collections
import copy
import re

import numpy as np

from .. import roles
from ..builder import build_plan
from ..editor import import_mpd
from ..geometry import analyze_geometry
from .core import COURSE, STUD, build

DELTA_RANGE = (-8, 12)
COURSE_RANGE = (-1, 6)
MAX_CANDIDATES = 5
OPTIONAL_ROLES = ('detail', 'engine', 'porch', 'chimney')
SLICE_ROLES = {'wall', 'body', 'structure'}
NO_SLICE_ROLES = {'roof', 'base', 'wheel', 'tyre', 'detail', 'engine', 'wing'}
KEEP_ROLES = {'roof', 'wall', 'base'}     # a sub-assembly that is mostly roof or wall is not offered as optional...
OPTIONAL_NAMES = re.compile(r'chimney|porch|dormer|garden|field|fence|tree|plant|bush|lamp|stair|step|trailer|swing|'
                            r'chair|bench|antenna|mirror|spoiler|ladder|crane|flag|sign')   # ...unless its name says it is
KEEP_NAMES = re.compile(r'roof|wall|shell|floor|base|foundation|storey|story|main|body|chassis|cab\b|hull|fuselage')
CLEAN_CODE = re.compile(r'^\d+[a-z]?$')

# Equivalent sets: same footprint and height, different surface. Each pair is verified against the library's boxes.
SWAP_CANDIDATES = {
    'brick_1x2': ['3004', '2877', '98283', '3065'],     # plain, grille, embossed bricks, clear
    'brick_1x4': ['3010', '30137'],                     # plain, log
    'tile_1x2': ['3069b', '2412b'],                     # groove, grille
}


# ------------------------------------------------------------------------------------------------ part families

_FAMILY = re.compile(r'(\d+) x (\d+)')


def family_key(description):
    """'Brick  2 x  4' -> 'Brick N x M' (None for 3-dimension parts and parts without a stud footprint)."""
    text = ' '.join(description.split()).removesuffix(' with Groove')    # every modern tile has a groove
    if re.search(r'\d+ x \d+ x \d+', text) or text[:1] in '~_=|':
        return None
    found = _FAMILY.search(text)
    return text[:found.start()] + 'N x M' + text[found.end():] if found else None


class Families:
    """Library parts grouped by description family, with their local boxes (built lazily, once per library)."""

    def __init__(self, parts, facts):
        self.parts, self.facts, self._by_key = parts, facts, None

    def members(self, description):
        if self._by_key is None:
            self._by_key = collections.defaultdict(list)
            for code, desc in self.parts.by_code.items():
                key = family_key(desc or '')
                if key:
                    self._by_key[key].append(code.casefold())
        return self._by_key.get(family_key(description) or '', [])


def _box(facts, code):
    return facts.get(code + '.dat')['bounds']


def _same(a, b, tol=0.6):
    return abs(a - b) <= tol


# A quarter turn about the vertical axis, as a local transform: a 2 x 3 brick turned this way fills the place of a 2 x 2
# brick lengthened along its local z. Boxes below are expressed in the original part's local frame.
QUARTER = np.array([[0.0, 0.0, 1.0], [0.0, 1.0, 0.0], [-1.0, 0.0, 0.0]])


def _turned(box):
    """Box of a part placed with QUARTER, in the frame it is placed in (x' = z, z' = -x)."""
    lo, hi = box
    return np.array([[lo[2], lo[1], -hi[0]], [hi[2], hi[1], -lo[0]]])


def candidate_for(fam, facts, leaf_code, axis_j, delta_studs):
    """Library part equal to leaf_code but `delta_studs` longer along local axis j (0 = x, 2 = z).

    Returns (code, box in the leaf's local frame, turned) or None; `turned` means the new part is placed with a
    quarter turn (QUARTER) because the library models it with its long side on the other axis.
    """
    desc = facts.get(leaf_code + '.dat')['desc']
    base = _box(facts, leaf_code)
    other = [i for i in range(3) if i != axis_j]
    options = []
    for code in fam.members(desc):
        if not CLEAN_CODE.match(code) and code != leaf_code:
            continue
        for turned in (False, True):
            box = _turned(_box(facts, code)) if turned else _box(facts, code)
            if not all(_same(box[0][i], base[0][i]) and _same(box[1][i], base[1][i]) for i in other):
                continue
            if _same((box[1][axis_j] - box[0][axis_j]) - (base[1][axis_j] - base[0][axis_j]), delta_studs * STUD):
                options.append((turned, code != leaf_code, len(code), code, box))
                break
    if not options:
        return None
    options.sort(key=lambda o: o[:4])
    return options[0][3], options[0][4], options[0][0]


# ------------------------------------------------------------------------------------------------ flattening

def _group_name(entry_id, section):
    if not re.fullmatch(r'p\d+', entry_id):
        return entry_id
    stem = re.sub(r'\.(ldr|mpd|dat)$', '', section, flags=re.IGNORECASE).split(' - ')[-1]
    slug = re.sub(r'[^a-z0-9]+', '_', stem.casefold()).strip('_')
    return f'{slug}_{entry_id}' if slug else entry_id


def leaves_of(plan, parts, facts):
    """Physical parts of a role-tagged plan as template leaves (stable ids l0001..)."""
    flat = roles.flatten(plan, parts, facts)
    stored = roles.role_of(plan)
    counter = collections.Counter()
    out = []
    for n, leaf in enumerate(flat, 1):
        key = leaf['key']
        base = (key[0], key[1].rsplit('-', 1)[0] if key not in stored and '-' in key[1] else key[1])
        role = stored.get(key) or stored.get(base) or 'structure'
        lo, hi = leaf['bounds']
        # every submodel instance above the part, outermost first: (path key, readable name)
        groups = [('/'.join(i for _, i in leaf['chain'][:k + 1]), _group_name(leaf['chain'][k][1], leaf['chain'][k + 1][0]))
                  for k in range(len(leaf['chain']) - 1)]
        counter[role] += 1
        out.append(dict(id=f'l{n:04d}', ref=leaf['ref'], colour=int(leaf['colour']), role=role, groups=groups,
                        at=[round(float(v), 6) for v in leaf['origin']], matrix=[[round(float(v), 6) for v in row] for row in leaf['mat']],
                        box=[[round(float(v), 3) for v in lo], [round(float(v), 3) for v in hi]],
                        ub=round(float(-hi[1]), 3), ut=round(float(-lo[1]), 3), kind=leaf['facts']['kind']))
    return out


# -------------------------------------------------------------------------------------------------- stretching

def _local_axis(leaf, world_axis):
    """Local axis (0 = x, 2 = z) that runs along the world axis, or None (tilted parts and lying parts)."""
    m = np.array(leaf['matrix'])
    j = int(np.argmax(np.abs(m[world_axis])))
    return j if abs(abs(m[world_axis, j]) - 1) < 1e-3 and j in (0, 2) else None


FLOAT_ROLES = {'detail', 'trim'}


def _floats(leaf, axis):
    """A small detail or trim part a cut crosses but cannot lengthen: it moves with the side its centre is on.

    Whether that keeps it attached is not assumed here; every offered stretch is built and checked afterwards.
    """
    lo, hi = leaf['box'][0][axis], leaf['box'][1][axis]
    return (leaf['role'] in FLOAT_ROLES or leaf['kind'] in ('detail', 'light')) and hi - lo <= 2 * STUD + 0.5


def _classify_cut(leaves, axis, cut, fam, facts, skip=()):
    """For a cut plane: (extend {id: {delta: (code, dpos)}}, shift ids, allowed deltas) or None when it cannot be cut."""
    extend, shift, allowed, left = {}, [], None, 0
    lo_d, hi_d = DELTA_RANGE
    for leaf in leaves:
        lo, hi = leaf['box'][0][axis], leaf['box'][1][axis]
        if leaf['kind'] == 'baseplate':
            continue
        if hi <= cut:
            left += 1
            continue
        if lo >= cut:
            shift.append(leaf['id'])
            continue
        if leaf['id'] in skip:
            return None
        j = _local_axis(leaf, axis)
        code = leaf['ref'].casefold().removesuffix('.dat')
        if j is None or not family_key(facts.get(leaf['ref'])['desc']):
            if not _floats(leaf, axis):
                return None
            if (lo + hi) / 2 >= cut:
                shift.append(leaf['id'])
            else:
                left += 1
            continue
        sign = 1 if leaf['matrix'][axis][j] > 0 else -1
        options = {}
        base = _box(facts, code)
        for d in range(lo_d, hi_d + 1):
            if d == 0:
                continue
            found = candidate_for(fam, facts, code, j, d)
            if found is None:
                continue
            new_code, box, turned = found
            # keep the min edge of the part fixed in world space, whatever the part's local origin is
            dpos = (base[0][j] - box[0][j]) if sign > 0 else (box[1][j] - base[1][j])
            options[d] = (new_code, round(float(dpos), 4), 1) if turned else (new_code, round(float(dpos), 4))
        if not set(options) & (allowed or set(options)) and _floats(leaf, axis):
            if (lo + hi) / 2 >= cut:
                shift.append(leaf['id'])
            else:
                left += 1
            continue
        allowed = set(options) if allowed is None else allowed & set(options)
        if not allowed:
            return None
        extend[leaf['id']] = options
    total = left + len(shift) + len(extend)
    if len(extend) < 2 or min(left, len(shift)) < max(2, 0.15 * total) or not allowed:
        return None          # a cut at the model's edge would only lengthen a few loose parts
    extend = {k: {str(d): list(v[d]) for d in sorted(allowed)} for k, v in extend.items()}
    return extend, shift, sorted(allowed)


def stretch_candidates(leaves, axis, fam, facts, skip=()):
    """Cut positions along a world axis ranked by centrality and by how many stretch options they offer."""
    bounds = sorted({round(l['box'][i][axis] * 2) / 2 for l in leaves if l['kind'] != 'baseplate' for i in (0, 1)})
    mid = (bounds[0] + bounds[-1]) / 2
    rows = []
    for a, b in zip(bounds, bounds[1:]):
        if b - a <= 2.5:
            continue
        cut = (a + b) / 2
        found = _classify_cut(leaves, axis, cut, fam, facts, skip)
        if found:
            rows.append((-(min(len(found[2]), 6)), abs(cut - mid), cut, found))
    rows.sort(key=lambda r: r[:3])
    return [(cut, *found) for _, _, cut, found in rows[:MAX_CANDIDATES]]


def _spec(cuts):
    """Stretch spec of one axis from accepted cuts: the cuts, and for every reachable total the delta at each cut."""
    best = {0: ()}
    for cut in cuts:
        nxt = {}
        for total, combo in best.items():
            for d in (0, *cut['delta']):
                t = total + d
                if -12 <= t <= 24 and (t not in nxt or sum(x * x for x in combo + (d,)) < sum(x * x for x in nxt[t])):
                    nxt[t] = combo + (d,)
        best = nxt
    return dict(cuts=[dict(cut=c['cut'], extend=c['extend'], shift=c['shift']) for c in cuts],
                plans={str(t): list(combo) for t, combo in sorted(best.items())})


FOOTPRINT_ROLES = {'wall', 'window', 'door', 'roof', 'body', 'glass', 'structure'}


def _footprint(leaves):
    """World x/z rectangle of the model's main mass (under the roof, else walls and body), so a garden or scenery
    around it is left alone."""
    core = ([l for l in leaves if l['role'] == 'roof'] or
            [l for l in leaves if l['role'] in FOOTPRINT_ROLES and l['kind'] != 'baseplate'] or leaves)
    return (min(l['box'][0][0] for l in core), max(l['box'][1][0] for l in core),
            min(l['box'][0][2] for l in core), max(l['box'][1][2] for l in core))


def _inside(leaf, rect, margin=1.0):
    x0, x1, z0, z1 = rect
    lo, hi = leaf['box']
    return min(hi[0], x1) - max(lo[0], x0) > margin and min(hi[2], z1) - max(lo[2], z0) > margin


def course_candidates(leaves):
    """Cut heights (up coordinates) where one 24-LDU slice of wall can be repeated: nothing crosses either plane.

    Only the model's main mass counts (its footprint rectangle): a lamp post or a tree in the garden that crosses
    the plane stays where it is, and only what stands inside the footprint moves up. Returns
    [(slice size, cut, slice ids, ids that move)].
    """
    tol = 6.0
    rect = _footprint(leaves)
    mass = [l for l in leaves if _inside(l, rect) and l['kind'] != 'baseplate']
    ups = sorted({round(l['ub']) for l in mass})
    out = []
    for cut in ups:
        if any(l['ub'] < cut - tol and l['ut'] > cut + tol for l in mass):
            continue
        if any(l['ub'] < cut - COURSE - tol and l['ut'] > cut - COURSE + tol for l in mass):
            continue
        slice_ = [l for l in mass if l['ub'] >= cut - COURSE - tol and l['ut'] <= cut + tol and l['ub'] <= cut - COURSE + tol]
        above = [l for l in mass if l['ub'] >= cut - tol and l not in slice_]
        main = sum(l['role'] in SLICE_ROLES for l in slice_)
        if len(slice_) < 4 or not above or main < 0.6 * len(slice_) or any(l['role'] in NO_SLICE_ROLES for l in slice_):
            continue
        out.append((len(slice_), cut, [l['id'] for l in slice_], [l['id'] for l in above]))
    if not out:
        return []
    mid = np.median([l['ub'] for l in mass])
    out.sort(key=lambda r: (abs(r[1] - mid), -r[0]))
    return out[:MAX_CANDIDATES]


# -------------------------------------------------------------------------------------- groups, swaps, colours

def optional_groups(leaves):
    """Sub-assemblies nothing rests on: submodel instances and optional roles, each with the leaf ids that go with it."""
    def rests_on(group_ids):
        ids = set(group_ids)
        tops = [l for l in leaves if l['id'] in ids]
        for other in leaves:
            if other['id'] in ids:
                continue
            for g in tops:
                overlap = (min(g['box'][1][0], other['box'][1][0]) - max(g['box'][0][0], other['box'][0][0]) > 1 and
                           min(g['box'][1][2], other['box'][1][2]) - max(g['box'][0][2], other['box'][0][2]) > 1)
                if overlap and abs(other['ub'] - g['ut']) < 6 and other['ub'] <= g['ut'] + 6 and other['ut'] > g['ut']:
                    return True
        return False
    total = len(leaves)
    found = {}
    by_group, names = collections.defaultdict(list), {}
    for l in leaves:
        for key, name in l.get('groups', ()):        # submodel instances at every depth: a garden lamp inside the house
            by_group[key].append(l['id'])
            names[key] = name
    role_of = {l['id']: l['role'] for l in leaves}
    seen = set()
    for key, ids in sorted(by_group.items(), key=lambda kv: (kv[0].count('/'), kv[0])):
        name = names[key]
        main = collections.Counter(role_of[i] for i in ids).most_common(1)[0][0]
        named = name.casefold()
        keep = bool(KEEP_NAMES.search(named)) or (main in KEEP_ROLES and not OPTIONAL_NAMES.search(named))
        if 3 <= len(ids) <= 0.4 * total and not keep and frozenset(ids) not in seen:
            seen.add(frozenset(ids))
            slug = re.sub(r'[^a-z0-9]+', '_', f'part_{name}'.casefold()).strip('_')
            while slug in found:
                slug += '_b'
            found[slug] = dict(label=f'sub-assembly {name}', leaves=ids)
    for role in OPTIONAL_ROLES:
        ids = [l['id'] for l in leaves if l['role'] == role]
        if 2 <= len(ids) <= 0.4 * total:
            found[role] = dict(label=f'all {role} parts', leaves=ids)
    return {k: v for k, v in found.items() if not rests_on(v['leaves'])}


def swap_sets(leaves, facts):
    """Equivalent part sets present in the model whose boxes agree in the library."""
    present = {l['ref'].casefold().removesuffix('.dat'): l for l in leaves}
    out = {}
    for name, codes in SWAP_CANDIDATES.items():
        used = [c for c in codes if c in present]
        if not used:
            continue
        base = _box(facts, used[0])
        same = [c for c in codes if all(_same(x, y) for x, y in zip(_box(facts, c).ravel(), base.ravel(), strict=True))]
        if len(same) > 1:
            out[name] = dict(codes=same, labels={c: facts.get(c + '.dat')['desc'] for c in same},
                             leaves=[l['id'] for l in leaves if l['ref'].casefold().removesuffix('.dat') in same])
    return out


# ------------------------------------------------------------------------------------------------- validation

def check_plan(plan, parts):
    """(error messages, optimistic component count) of a plan, built and measured the same way as the generators' tests."""
    _, model, diagnostics = build_plan(plan, parts)
    errors = [d['message'] for d in diagnostics if d['severity'] == 'error']
    geometry = analyze_geometry(model, parts, detail='summary', contacts='all')
    errors += [d['message'] for d in geometry['diagnostics'] if d['severity'] == 'error']
    return errors, geometry['optimistic_component_count']


def _progressive(check, deltas, verdicts):
    """The deltas that pass `check`, walking out from zero both ways and stopping at the first failure each way:
    the offered range is contiguous and every value in it was built and checked."""
    passed = []
    for side in (sorted(d for d in deltas if d > 0), sorted((d for d in deltas if d < 0), reverse=True)):
        for d in side:
            ok, info = check(d)
            verdicts[d] = info
            if not ok:
                break
            passed.append(d)
    return sorted(passed)


def _ok(data, params, parts, baseline):
    errors, components = check_plan(build(data, params)[0], parts)
    return len(errors) <= baseline[0] and components <= baseline[1], dict(errors=len(errors), components=components)


# ----------------------------------------------------------------------------------------------------- derive

def derive(plan, parts, *, id, title, families, kinds, description, source=None, hints='', verify=True, facts=None,
           log=None):
    """(template data, report) from a plan. `plan` may already carry roles; missing ones are classified."""
    log = log or (lambda *_: None)
    facts = facts or roles.PartFacts(parts)
    tagged, tag_report = roles.tag_plan(plan, parts, hints=hints or description, facts=facts)
    leaves = leaves_of(tagged, parts, facts)
    fam = Families(parts, facts)
    report = dict(leaves=len(leaves), domain=tag_report['domain'], verified={}, rejected={})
    data = dict(version=1, id=id, title=title, families=list(families), kinds=list(kinds), description=description,
                source=source or {}, leaves=leaves, stretch={}, courses=None, swaps={}, groups={})
    colour_counts = collections.defaultdict(collections.Counter)
    for l in leaves:
        colour_counts[l['role']][l['colour']] += 1
    data['colours'] = {r: c.most_common(1)[0][0] for r, c in sorted(colour_counts.items())}
    data['roles'] = sorted(colour_counts)
    baseline = check_plan(build(copy.deepcopy(data), {})[0], parts) if verify else (10 ** 6, 10 ** 6)
    baseline = (len(baseline[0]), baseline[1]) if verify else baseline
    report['baseline'] = dict(errors=baseline[0], components=baseline[1])
    log('baseline', report['baseline'])

    param_name = {'x': 'x_delta', 'z': 'z_delta'}
    axes = {'x': 0, 'z': 2}
    straddled = set()      # parts already extended along another axis: a second axis leaves them alone
    for axis, index in axes.items():
        accepted, tried = [], []
        for cut, extend, shift, allowed in stretch_candidates(leaves, index, fam, facts, straddled):
            if set(extend) & {i for c in accepted for i in c['extend']}:
                continue                                     # cuts must not share a part: each part changes at one cut only
            cand = dict(cut=cut, extend=extend, shift=shift, delta=allowed)
            verdicts, ok_all = {}, True
            if verify:
                passed = _progressive(lambda d, a=axis, c=cand: _ok(dict(data, stretch={a: _spec([c])}), {param_name[a]: d},
                                                                    parts, baseline), allowed, verdicts)
                ok_all = bool(passed)
                cand['delta'] = passed
                cand['extend'] = {k: {d: v for d, v in o.items() if int(d) in passed} for k, o in extend.items()}
            tried.append(dict(cut=cut, deltas=allowed, ok=ok_all, checks={str(k): v for k, v in verdicts.items()}))
            if ok_all:
                accepted.append(cand)
            if len(accepted) == 2:
                break
        while accepted:
            spec = _spec(accepted)
            totals = [int(t) for t in spec['plans'] if int(t)]
            ok_all, verdicts = True, {}
            if verify and len(accepted) > 1:
                for d in sorted(totals, key=abs):                          # every size the combination offers
                    ok, info = _ok(dict(data, stretch={axis: spec}), {param_name[axis]: d}, parts, baseline)
                    verdicts[d] = info
                    ok_all = ok_all and ok
                    if not ok:
                        break
            if ok_all:
                data['stretch'][axis] = spec
                straddled |= {i for c in accepted for i in c['extend']} if axis == 'x' else set()
                report['verified'][axis] = dict(cuts=[c['cut'] for c in accepted], totals=sorted(totals),
                                                combined_checks={str(k): v for k, v in verdicts.items()})
                break
            accepted.pop()                                   # the combination failed: fall back to fewer cuts
        report['rejected'][axis] = [t for t in tried if not t['ok']]
        log('stretch', axis, report['verified'].get(axis))
    for _count, cut, ids, moving in course_candidates(leaves):
        spec = dict(cut=cut, height=COURSE, slice=ids, above=moving, delta=list(range(COURSE_RANGE[0], COURSE_RANGE[1] + 1)))
        verdicts = {}
        if verify:
            passed = _progressive(lambda d, c=spec: _ok(dict(data, courses=c), {'courses_delta': d}, parts, baseline),
                                  spec['delta'], verdicts)
            spec['delta'] = passed
        ok_all = any(d > 0 for d in spec['delta'])
        if ok_all:
            data['courses'] = spec
            report['verified']['courses'] = dict(cut=cut, slice=len(ids), checks={str(k): v for k, v in verdicts.items()})
            break
        report['rejected'].setdefault('courses', []).append(dict(cut=cut, checks={str(k): v for k, v in verdicts.items()}))
    log('courses', report['verified'].get('courses'))
    for name, group in optional_groups(leaves).items():
        trial = dict(data, groups={name: group})
        ok, info = _ok(trial, {f'include_{name}': False}, parts, baseline) if verify else (True, {})
        if ok:
            data['groups'][name] = group
            report['verified'][f'include_{name}'] = info
        else:
            report['rejected'][f'include_{name}'] = info
        if len(data['groups']) >= 6:
            break
    for name, swap in swap_sets(leaves, facts).items():
        data['swaps'][name] = swap
        report['verified'][f'swap_{name}'] = dict(codes=swap['codes'])
    data['params'] = _params(data, leaves)
    report['size'] = {a: s['plans'] and max(int(t) for t in s['plans']) for a, s in data['stretch'].items()}
    data['coverage'] = _coverage(data)
    data['quality'] = (f"Derived from {source.get('name', 'a model') if source else 'a model'}: {len(leaves)} parts, roles by classifier "
                       f"({tag_report['domain']}); every offered size, swap and removal was built and checked.")
    return data, report


def compact(data):
    """The template JSON as stored: derivation-only fields (world boxes, kinds) dropped; `generate` never reads them."""
    out = dict(data)
    out['leaves'] = [{k: v for k, v in l.items() if k not in ('box', 'ut', 'kind', 'groups')} for l in data['leaves']]
    return out


def _params(data, leaves):
    params = {}
    for role in data['roles']:
        params[f'colour_{role}'] = {'type': ['string', 'integer'], 'x-colour': True, 'default': data['colours'][role],
                                    'description': f'Colour of the {role} parts (colour name or LDraw code)'}
    for axis, spec in data['stretch'].items():
        index = 0 if axis == 'x' else 2
        size = max(l['box'][1][index] for l in leaves if l['kind'] != 'baseplate') - min(l['box'][0][index] for l in leaves if l['kind'] != 'baseplate')
        params[f'{axis}_delta'] = {'enum': sorted(int(t) for t in spec['plans']), 'default': 0, 'x-axis': axis,
                                   'x-size-studs': round(size / STUD, 1),
                                   'description': f'Studs added (+) or removed (-) along {axis.upper()}; the model is {round(size / STUD)} studs long there'}
    if data['courses']:
        params['courses_delta'] = {'enum': sorted({0, *data['courses']['delta']}), 'default': 0,
                                   'description': 'Wall courses repeated (+) or dropped (-)'}
    for name, swap in data['swaps'].items():
        params[f'swap_{name}'] = {'enum': ['keep', *swap['codes']], 'default': 'keep', 'x-labels': swap['labels'],
                                  'description': f'Swap every {name.replace("_", " ")} for an equivalent part'}
    for name, group in data['groups'].items():
        params[f'include_{name}'] = {'type': 'boolean', 'default': True, 'description': f"Keep {group['label']}"}
    return params


def _coverage(data):
    out = ['colours by role']
    out += [f'length along {a.upper()}' for a in data['stretch']]
    if data['courses']:
        out.append('wall courses')
    out += [f'swap {n}' for n in data['swaps']]
    out += [f'optional {n}' for n in data['groups']]
    return out


def derive_from_mpd(text, parts, *, name='model.ldr', **kwargs):
    """Import MPD/LDR text as a plan, then derive. Author and licence come from the file header."""
    plan, warnings = import_mpd(text, parts, name=name)
    header = {}
    for line in text.splitlines():
        for key, label in (('Author:', 'author'), ('!LICENSE', 'license')):
            if line.startswith('0 ' + key) and label not in header:
                header[label] = line[2 + len(key):].strip()
    source = {**dict(name=name), **header, **(kwargs.pop('source', None) or {})}
    data, report = derive(plan, parts, source=source, **kwargs)
    report['import_warnings'] = len(warnings)
    return data, report
