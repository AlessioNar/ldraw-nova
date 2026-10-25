"""A street: modular buildings side by side along x, module fronts in line, with a check that their Technic holes meet.

    street = compose_street(['modular-corner-cafe', 'modular-bakery', 'modular-town-hall'])
    street['plan']        # one plan, modules 32 studs apart
    street['joints']      # per neighbouring pair: pins that line up, pins that do not, and whether the joint holds

A street is a list of modules or the name of a composition in STREETS. A module is a preset name or a (partial) config; it must have a modular base. A corner module ends the street on its
street side: `corner: left` goes first, `corner: right` last. Modules are not studded to each other (real modulars are
pinned), so the street has one connected component per module.
"""
from __future__ import annotations

from . import catalog, engine, walls
from .canvas import Canvas
from .presets import PRESETS
from .schema import deep_merge

MODULE = 32
STREETS = {
    'modular-street': ['modular-corner-cafe', 'modular-bakery', 'modular-town-hall', 'modular-flats', 'modular-tea-house'],
}
MIN_PINS = 2        # a joint needs this many aligned ground-floor pins to count as bolted


def _config(module):
    if isinstance(module, str):
        if module not in PRESETS:
            raise ValueError(f'unknown preset {module!r}')
        return PRESETS[module]['config']
    return module


def _pins(build, side):
    """{(z, y)} of the Technic bricks on this module's left or right wall: studs behind the module front, plates above the ground."""
    bx0, bz0, _, _ = build['base_rect']
    return {(min(c[1] for c in item.cells) - bz0, item.y0 - build['y_ground'])
            for item in build['canvas'].items
            if item.code == catalog.TECHNIC_BRICK and (item.cells[0][0] < bx0 + MODULE // 2) == (side == 'left')}


def compose(modules, inventory=None):
    if isinstance(modules, str):
        modules = STREETS[modules]
    """Build the modules and lay them along x. Returns dict(plan, notes, builds, joints, ok, stats)."""
    if not modules:
        raise ValueError('a street needs at least one module')
    builds, notes = [], []
    for k, module in enumerate(modules):
        cfg = deep_merge(_config(module), {})
        if cfg.get('base', {}).get('kind', 'plate') != 'modular':
            raise ValueError(f'module {k + 1} is not modular: set base.kind to modular')
        build = engine.build(cfg, inventory)
        corner = build['config']['base']['corner']
        if corner == 'left' and k != 0:
            raise ValueError(f"module {k + 1} has its street on the left, so it must come first")
        if corner == 'right' and k != len(modules) - 1:
            raise ValueError(f"module {k + 1} has its street on the right, so it must come last")
        notes += [f'module {k + 1}: {n}' for n in build['notes']]
        builds.append(build)
    street = Canvas(inventory=inventory)
    for k, build in enumerate(builds):
        bx0, bz0, bw, bd = build['base_rect']
        dx, dz = MODULE * k - bx0, -bz0
        remap = {}
        for item in build['canvas'].items:
            host = remap.get(item.host) if item.host is not None else None
            put = street.put(item.code, item.colour, item.role, item.x + dx, item.z + dz, item.y0, item.yaw,
                             label=f'm{k + 1} {item.label}', host=host, top_ldu=item.top_ldu)
            remap[item.index] = put.index
    joints = []
    for k in range(len(builds) - 1):
        left, right = _pins(builds[k], 'right'), _pins(builds[k + 1], 'left')
        aligned = left & right
        ground = [p for p in aligned if p[1] == 3 * walls.PIN_COURSE]
        joints.append(dict(between=(k + 1, k + 2), aligned=len(aligned), ground_aligned=len(ground),
                           unmatched=len(left ^ right), ok=len(ground) >= MIN_PINS))
    for j in joints:
        if not j['ok']:
            notes.append(f"joint {j['between'][0]}-{j['between'][1]}: only {j['ground_aligned']} ground-floor pins line up "
                         f"(needs {MIN_PINS}); turn details.connectors on for both modules")
        elif j['unmatched']:
            notes.append(f"joint {j['between'][0]}-{j['between'][1]}: {j['unmatched']} upper-floor pins do not meet "
                         f"because the storey heights differ")
    plan = street.plan('street', f'Modular street of {len(builds)} modules')
    comps = street.components()
    return dict(plan=plan, notes=notes, builds=builds, joints=joints, ok=all(j['ok'] for j in joints), canvas=street,
                stats=dict(parts=len(street.items), modules=len(builds), components=len(comps)))
