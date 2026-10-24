"""The uniform generator interface and the role tagger shared by all generators."""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Callable

import jsonschema

from .colours import colour_names

COLOUR = {'type': ['string', 'integer'], 'x-colour': True,
          'description': 'LDraw colour name (red, dark grey, tan...) or code'}


def colour_param(description):
    return {**COLOUR, 'description': description + ' (colour name or code)'}


@dataclass(frozen=True)
class Generator:
    """One deterministic model builder.

    params is a JSON-schema `properties` object. `build(params)` receives the validated parameters with
    defaults filled and returns (plan, notes); notes list every value the generator had to adjust.
    """
    id: str
    title: str
    families: tuple
    kinds: tuple
    description: str
    params: dict
    roles: tuple
    build: Callable = field(repr=False, compare=False)
    quality: str = ''
    coverage: tuple = ()

    def schema(self):
        return {'type': 'object', 'additionalProperties': False, 'properties': copy.deepcopy(self.params)}

    def defaults(self):
        return {k: v['default'] for k, v in self.params.items() if 'default' in v}

    def resolve(self, params=None):
        """Validated copy of params with defaults filled; raises ValueError on a bad value."""
        merged = {**self.defaults(), **{k: v for k, v in (params or {}).items() if v is not None}}
        error = jsonschema.exceptions.best_match(jsonschema.Draft202012Validator(self.schema()).iter_errors(merged))
        if error is not None:
            where = '.'.join(map(str, error.path)) or 'params'
            raise ValueError(f'{self.id}: {where}: {error.message}')
        return merged

    def generate(self, params=None):
        resolved = self.resolve(params)
        plan, notes = self.build(resolved)
        return dict(generator=self.id, params=resolved, notes=notes, plan=plan)

    def plan(self, params=None):
        return self.generate(params)['plan']

    def entry(self):
        return dict(id=self.id, title=self.title, families=list(self.families), kinds=list(self.kinds),
                    description=self.description, params=self.schema(), roles=list(self.roles),
                    coverage=list(self.coverage), quality=self.quality, defaults=self.defaults())


def tag_roles(plan, rules):
    """Add the optional `role` field to placements.

    rules: list of (section-name prefix, role | {colour: role} | callable(placement) -> role | None). The
    first matching rule of a section decides; unmatched placements stay untagged. Returns the plan.
    """
    for section in plan['sections']:
        rule = next((r for prefix, r in rules if section['name'].startswith(prefix)), None)
        if rule is None:
            continue
        for step in section['steps']:
            for p in step:
                role = rule(p) if callable(rule) else rule.get(p['colour']) if isinstance(rule, dict) else rule
                if role:
                    p['role'] = role
    return plan


def digest_line(g):
    """One compact line: id | families | kinds | parameters."""
    colours, parts = [], []
    for name, spec in g.params.items():
        if spec.get('x-colour'):
            colours.append(name)
        elif 'enum' in spec:
            parts.append(f"{name}={'|'.join(map(str, spec['enum']))}")
        elif spec.get('type') == 'integer':
            parts.append(f"{name}:{spec.get('minimum', '')}-{spec.get('maximum', '')}{'e' if spec.get('x-even') else ''}")
        elif spec.get('type') == 'boolean':
            parts.append(name + '?')
        elif spec.get('type') == 'array':
            parts.append(f"{name}=[{'|'.join(spec['items']['enum'])}]")
        else:
            parts.append(name)
    if colours:
        parts.append('colours:' + ','.join(colours))
    return f"{g.id} | {','.join(g.families)} | {','.join(g.kinds)} | {' '.join(parts)}"


def digest(generators):
    head = ('GENERATORS (id | families | kinds | params). Integers are studs unless named courses/tiers; '
            'colour params take a colour name or LDraw code; name? is a yes/no flag; ranges are name:lo-hi (suffix e = even only); omit a param for its default. '
            f'Colour names: {", ".join(colour_names()[:24])}, ...')
    return '\n'.join([head, *(digest_line(g) for g in generators)])


