"""A template: a model (with roles) plus the knobs its own geometry offers, and `generate(params)` -> plan.

Everything `generate` needs is stored in the template JSON (poses, roles, which parts can be swapped for which
codes at each stretch), so it is pure, fast and needs no part library. The knobs are derived, never hand-written
(see derive.py):

  colour_<role>   recolour every part of a role (transparent colours stay transparent for windows and glass)
  x_delta/z_delta studs added (+) or removed (-) along an axis: parts crossing a cut plane become the next
                  longer/shorter equivalent part, parts beyond it move; only steps proven valid are offered
  courses_delta   repeat or drop one wall slice (a course of bricks) in the model
  swap_<set>      swap every part of an equivalent set (same footprint and height) for another member
  include_<name>  keep or drop an optional sub-assembly (nothing rests on it)
"""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field

import jsonschema

from ..generators.colours import colour_code
from ..role_edits import GLASSY_ROLES, TRANSPARENT

STUD = 20.0
COURSE = 24.0


@dataclass(frozen=True)
class Template:
    """Same surface as generators.Generator (id, families, kinds, params, roles, generate...), plus `kind`."""
    id: str
    title: str
    families: tuple
    kinds: tuple
    description: str
    params: dict
    roles: tuple
    data: dict = field(repr=False, compare=False)
    quality: str = ''
    coverage: tuple = ()
    kind: str = 'template'

    def schema(self):
        return {'type': 'object', 'additionalProperties': False, 'properties': copy.deepcopy(self.params)}

    def defaults(self):
        return {k: v['default'] for k, v in self.params.items() if 'default' in v}

    def resolve(self, params=None):
        merged = {**self.defaults(), **{k: v for k, v in (params or {}).items() if v is not None}}
        error = jsonschema.exceptions.best_match(jsonschema.Draft202012Validator(self.schema()).iter_errors(merged))
        if error is not None:
            where = '.'.join(map(str, error.path)) or 'params'
            raise ValueError(f'{self.id}: {where}: {error.message}')
        for name, spec in self.params.items():
            if spec.get('x-colour') and isinstance(merged[name], str):
                colour_code(merged[name], glass=name.removeprefix('colour_') in GLASSY_ROLES)   # raises on an unknown name
        return merged

    def generate(self, params=None):
        resolved = self.resolve(params)
        plan, notes = build(self.data, resolved)
        return dict(generator=self.id, kind='template', params=resolved, notes=notes, plan=plan)

    def plan(self, params=None):
        return self.generate(params)['plan']

    def entry(self):
        return dict(id=self.id, title=self.title, kind='template', families=list(self.families), kinds=list(self.kinds),
                    description=self.description, params=self.schema(), roles=list(self.roles),
                    coverage=list(self.coverage), quality=self.quality, defaults=self.defaults(),
                    source=self.data.get('source', {}))


def from_data(data):
    """Template from its JSON (as written by derive.derive and stored under data/templates/)."""
    return Template(id=data['id'], title=data['title'], families=tuple(data['families']), kinds=tuple(data['kinds']),
                    description=data['description'], params=data['params'], roles=tuple(data['roles']), data=data,
                    quality=data.get('quality', ''), coverage=tuple(data.get('coverage', ())))


def load(path):
    with open(path, encoding='utf-8') as handle:
        return from_data(json.load(handle))


def _rows(matrix):
    return [[round(float(v), 6) + 0.0 for v in row] for row in matrix]


def _identity(matrix):
    return all(abs(matrix[i][j] - (i == j)) < 1e-9 for i in range(3) for j in range(3))


def _turned(matrix, turn):
    """Matrix of a part turned a quarter about its own up axis (the library stores a 1x2 brick along x, the plan needs z)."""
    t = 1 if turn > 0 else -1
    local = [[0, 0, t], [0, 1, 0], [-t, 0, 0]]
    return [[sum(matrix[i][k] * local[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def build(data, params):
    """(plan, notes) of the template at these parameters; pure function of (data, params)."""
    leaves = {l['id']: copy.deepcopy(l) for l in data['leaves']}
    notes = []
    # 1. optional sub-assemblies
    for name, group in data.get('groups', {}).items():
        if not params.get(f'include_{name}', True):
            for lid in group['leaves']:
                leaves.pop(lid, None)
    # 2. part swaps
    for name, swap in data.get('swaps', {}).items():
        chosen = params.get(f'swap_{name}', 'keep')
        if chosen != 'keep':
            for lid in swap['leaves']:
                if lid in leaves:
                    leaves[lid]['ref'] = chosen + '.dat'
    # 3. stretch along x and z: at each cut, straddling parts become longer/shorter equivalents and the far side moves
    for axis, index in (('x', 0), ('z', 2)):
        delta = int(params.get(f'{axis}_delta', 0))
        spec = data.get('stretch', {}).get(axis)
        if not delta or not spec:
            continue
        for cut, step in zip(spec['cuts'], spec['plans'][str(delta)]):
            if not step:
                continue
            shift, extend = set(cut['shift']), cut['extend']
            for lid, leaf in leaves.items():
                move = step * STUD if lid in shift else 0.0
                if lid in extend:
                    code, move, *turn = extend[lid][str(step)]  # the longer/shorter part, where its origin goes, quarter turn
                    leaf['ref'] = code + '.dat'
                    if turn and turn[0]:
                        leaf['matrix'] = _turned(leaf['matrix'], turn[0])
                leaf['at'][index] += move - step * STUD / 2      # re-centre: the model grows evenly both ways
    # 4. courses: repeat (or drop) the slice between two bricks courses
    delta = int(params.get('courses_delta', 0))
    spec = data.get('courses')
    if delta and spec:
        cut, height, slice_ids = spec['cut'], spec['height'], [i for i in spec['slice'] if i in leaves]
        above = [i for i, l in leaves.items() if i not in set(slice_ids) and l['ub'] >= cut - 0.5]
        for lid in above:
            leaves[lid]['at'][1] -= height * delta
        if delta < 0:
            for lid in slice_ids:
                del leaves[lid]
        for k in range(1, max(delta, 0) + 1):
            for lid in slice_ids:
                copy_ = copy.deepcopy(leaves[lid])
                copy_['id'] = f'{lid}-c{k}'
                copy_['at'][1] -= height * k
                leaves[copy_['id']] = copy_
    # 5. colours by role
    for role, default in data.get('colours', {}).items():
        value = params.get(f'colour_{role}', default)
        if value == default:
            continue
        for leaf in leaves.values():
            if leaf.get('role') == role:
                glass = role in GLASSY_ROLES or leaf['colour'] in TRANSPARENT
                leaf['colour'] = colour_code(value, glass=glass)
    return _plan(data, leaves), notes


def _plan(data, leaves):
    """One root section, steps = layers from the bottom up, every part with its pose, colour and role."""
    ordered = sorted(leaves.values(), key=lambda l: (-(l['at'][1]), l['id']))
    steps, last, current = [], None, []
    for leaf in ordered:
        layer = round(-leaf['at'][1] / COURSE)
        if last is not None and layer != last:
            steps.append(current)
            current = []
        last = layer
        entry = dict(id=leaf['id'], ref=leaf['ref'], colour=leaf['colour'], at=[round(v, 6) + 0.0 for v in leaf['at']])
        if not _identity(leaf['matrix']):
            entry['matrix'] = _rows(leaf['matrix'])
        if leaf.get('role'):
            entry['role'] = leaf['role']
        current.append(entry)
    steps.append(current)
    name = f"{data['id']}.ldr"
    source = data.get('source', {})
    plan = dict(version=1, author=(source.get('author') or 'template')[:500],
                sections=[dict(name=name, description=data['description'][:500], steps=steps)])
    if source.get('license'):
        plan['license'] = source['license'][:500]
    return plan
