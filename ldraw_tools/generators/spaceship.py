"""Parametric starfighter: stepped delta/swept/straight wings, a brick spine, slope nose, canopy and jet pods.

Original System-part design (no reference model is adapted). Front is -Z, X is the wingspan, heights are plate
and brick courses above the wing underside; engine pods hang below the wings (negative heights).
"""
from __future__ import annotations

from ..architecture import Module, slab, WIDE_PLATES, lengths
from ..vehicle_details import detail_module
from ..details import palettes
from .base import Generator, colour_param, inherit_roles, tag_roles
from .colours import colour_code, custom_palette

BRICKS2 = {2: '3003', 3: '3002', 4: '3001', 6: '2456', 8: '3007'}
WING_STYLES = ['delta', 'swept', 'straight']


def spine(m, x, z0, n, h, colour, *, bricks=True):
    """2-wide strip of n studs centred on x, starting at z0 (plates or bricks, long side along Z)."""
    cursor = z0
    for k in lengths(n):
        if k == 1:
            m.add('3023b', colour, x, h, cursor + .5, yaw=90)
        else:
            m.add((BRICKS2 if bricks else WIDE_PLATES)[k], colour, x, h, cursor + k / 2, yaw=90)
        cursor += k


def row_widths(rows, span, style):
    """Even full widths of each 2-stud wing row, from the wing root forward edge to the tail."""
    out = []
    for r in range(rows):
        t = (r + 1) / rows
        if style == 'straight':
            w = span
        elif style == 'swept':
            w = 4 + (span - 4) * min(1, t * 1.4) if r < rows - 1 else span - 2
        else:
            w = 4 + (span - 4) * t
        out.append(max(6, 2 * round(w / 2)))
    return out


def build(p):
    notes = []
    length, span = p['length'] + p['length'] % 2, p['wingspan'] + p['wingspan'] % 2
    hull, wing, accent = colour_code(p['hull_colour']), colour_code(p['wing_colour']), colour_code(p['accent_colour'])
    canopy, engine = colour_code(p['canopy_colour'], glass=True), colour_code(p['engine_colour'])
    nose = min(max(4, 2 * round(length * p['nose'] / 4)), length - 8)   # studs ahead of the wing root
    zw = -length // 2 + nose
    rows = (length // 2 - zw) // 2
    widths = row_widths(rows, span, p['wing_style'])
    m = Module('starfighter', f'Starfighter {length} long, {span} wide; front -Z')
    base = Module('starfighter-hull', 'Wing plates, 4-wide fuselage, slope nose, canopy and tail fin; front -Z')

    # Layer 1: full-width plate rows under everything, plus the 2-wide nose plates.
    # base.role is stamped on each placement as it is added: per-placement roles, never recovered from colours.
    base.role = 'wing'
    for r in range(rows):
        slab(base, -widths[r] // 2, zw + 2 * r, widths[r], 2, 8, wing)
    base.role = 'hull'
    spine(base, 0, -length // 2, nose, 8, hull, bricks=False)
    base.role = 'accent'
    # Layers 2 and 3: stepped plates beside the fuselage, layer 2 offset one stud in Z so it bonds the rows.
    for r in range(rows - 1):
        w = min(widths[r], widths[r + 1]) - 2
        if w >= 8:
            for x0 in (-w // 2, 2):
                slab(base, x0, zw + 2 * r + 1, w // 2 - 2, 2, 16, accent)
    base.role = 'wing'
    for r in range(rows):
        w = widths[r] - 6
        if w >= 8:
            for x0 in (-w // 2, 2):
                slab(base, x0, zw + 2 * r, w // 2 - 2, 2, 24, wing)
    # Fuselage bricks over the plates: 4 wide behind the nose, 2 wide ahead of it, then slope, canopy, fin.
    base.role = 'hull'
    for x in (-1, 1):
        spine(base, x, zw, length // 2 - zw, 32, hull)
    spine(base, 0, -length // 2 + 2, nose - 2, 32, hull)
    base.add('3039', hull, 0, 32, -length // 2 + 1.5)
    base.add('3039', canopy, 0, 56, zw + 1.5, role='canopy')     # canopy: slope in front, glass brick behind
    base.add('3003', canopy, 0, 56, zw + 3, role='canopy')
    spine(base, 0, zw + 4, length // 2 - zw - 4, 56, hull)
    base.role = 'accent'
    spine(base, 0, length // 2 - 4, 4, 80, accent)
    spine(base, 0, length // 2 - 2, 2, 104, accent)
    base.role = None
    m.add(base, hull, id='hull')
    # Engines under the wing tails.
    pod_palette, _ = custom_palette(palettes()['heritage-racing']['roles'], roof=engine, metal=p['engine_core'])
    outer = min(widths[-3:]) // 2 - 1                            # pods are 2 wide and stay under the tail rows
    centres = {1: [0], 2: [-outer, outer], 4: [-outer, -outer + 2, outer - 2, outer]}[p['engines']]
    for i, x in enumerate(centres):
        # a pod spans z-3..z+2; keep it under the wing rows
        m.add(detail_module('jet-engine-pod', pod_palette), engine, x, 0, length // 2 - 3, id=f'engine-{i}')
    plan = m.plan()
    plan['author'] = 'ldraw-nova generators'
    tag_roles(plan, [('vehicle-jet-engine-pod', 'engine')])
    inherit_roles(plan)
    return plan, notes


GENERATOR = Generator(
    id='starfighter', title='Starfighter', families=('spaceship',),
    kinds=('starfighter', 'spaceship', 'starship', 'spacecraft', 'fighter', 'space fighter', 'rocket ship', 'x-wing'),
    description='Small original spacecraft: stepped wings, slope nose, canopy, 1/2/4 jet engines.',
    params={
        'length': {'type': 'integer', 'minimum': 12, 'maximum': 28, 'x-even': True, 'default': 16, 'description': 'Hull length in studs'},
        'wingspan': {'type': 'integer', 'minimum': 8, 'maximum': 28, 'x-even': True, 'default': 16, 'description': 'Wing tip to tip in studs'},
        'wing_style': {'enum': WING_STYLES, 'default': 'delta'},
        'nose': {'type': 'number', 'minimum': 0.25, 'maximum': 0.6, 'default': 0.4, 'description': 'Fraction of the length ahead of the wings'},
        'engines': {'enum': [1, 2, 4], 'default': 2},
        'hull_colour': colour_param('Fuselage') | {'default': 'white'},
        'wing_colour': colour_param('Wing underlayer') | {'default': 'light grey'},
        'accent_colour': colour_param('Wing top stripes, tail') | {'default': 'blue'},
        'canopy_colour': colour_param('Canopy (transparent)') | {'default': 'trans light blue'},
        'engine_colour': colour_param('Engine shells') | {'default': 'dark grey'},
        'engine_core': colour_param('Engine core') | {'default': 'orange'},
    },
    roles=('hull', 'wing', 'accent', 'canopy', 'engine'), build=build,
    coverage=('colours', 'size', 'wing shape', 'engine count'),
    quality='Simple blocky starfighter built from plates and bricks; not a detailed replica.')
