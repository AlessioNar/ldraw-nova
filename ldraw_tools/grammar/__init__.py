"""The building grammar: one ontology of LEGO buildings (footprint, storeys, walls, openings, corners, roof,
details, base) that turns a JSON configuration into a validated, role-tagged plan.

    from ldraw_tools import grammar
    result = grammar.generate({'roof': {'style': 'hip'}})            # or preset='castle-keep'
    result['plan']          # plan.schema.json document, every placement has a `role`
    grammar.generate(cfg, inventory=rows, mode='only')              # owned-parts mode
"""
from __future__ import annotations

from .. import generators as _generators  # noqa: F401  (loads the registry first; avoids a circular import)
from .catalog import Inventory
from .engine import build
from .nl import apply_text
from .presets import PRESETS
from .roles import ROLE_NAMES, ROLES
from .schema import CONSTRAINTS, SCHEMA, defaults, deep_merge, normalise

__all__ = ['generate', 'build', 'normalise', 'apply_text', 'PRESETS', 'SCHEMA', 'CONSTRAINTS', 'ROLES', 'ROLE_NAMES',
           'Inventory', 'defaults', 'preset_config']


def preset_config(name):
    if name not in PRESETS:
        raise ValueError(f"unknown preset {name!r}; presets: {', '.join(PRESETS)}")
    return PRESETS[name]['config']


def generate(config=None, *, preset=None, inventory=None, mode='prefer', validate=False):
    """config (partial is fine) -> dict(config, notes, plan, stats, inventory, validation).

    preset names a starting configuration that `config` then overrides. inventory is a list of
    {part, colour, qty} rows; mode 'prefer' orders choices by what is owned and reports shortages, 'only'
    restricts tilings (bricks, slopes, plates) to owned lengths and substitutes colours, window kinds, door leaves
    and tiles (for plates) that are not owned; every swap is listed in the report's `substitutions`.
    """
    cfg = deep_merge(preset_config(preset), config) if preset else config
    inv = None
    if inventory is not None:
        inv = inventory if isinstance(inventory, Inventory) else Inventory.from_rows(inventory, mode)
    result = build(cfg, inv)
    result.pop('canvas')
    if validate:
        from .validate import check
        result['validation'] = check(result['plan'])
    return result
