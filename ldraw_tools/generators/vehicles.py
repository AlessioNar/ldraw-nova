"""Generator wrappers for the seven vehicle designs, with colour-by-name palettes and size parameters."""
from __future__ import annotations

from ..details import palettes
from ..vehicle_details import vehicle_colours
from ..vehicles import DESIGNS, vehicle_plan
from .base import Generator, colour_param, inherit_roles, tag_roles
from .colours import custom_palette

PALETTES = [k for k, v in palettes().items() if v.get('family') == 'vehicle']
# Placement colour -> role priority when two roles share a colour (the first one wins).
ROLE_ORDER = ['body', 'roof', 'glass', 'wood', 'metal', 'trim', 'chassis', 'headlamp', 'tail_lamp']
TYRES = {'6015.dat', '3641.dat', '50861.dat'}
RIMS = {'6014b.dat', '4624.dat', '50862.dat'}
HOLDERS = {'4600.dat', '6157.dat'}
# Parts whose job is unambiguous whatever palette colour they carry (a wing in the roof colour is still a wing).
WINGS = {'30355.dat', '30356.dat', '3936.dat', '3935.dat', '4867.dat'}
ENGINES = {'4868b.dat', '4869.dat'}
GLAZING = {'2377.dat', '87612.dat', '4176.dat', '2437.dat', '3823.dat'}

# name: (families, kinds, road range (length lo, hi, wheelbase lo, hi) or None)
INFO = {
    'grand-tourer': (('car',), ('car', 'coupe', 'sports car', 'sedan', 'racing car', 'roadster', 'supercar')),
    'delivery-van': (('van', 'truck'), ('van', 'delivery van', 'minibus', 'camper', 'bus')),
    'pickup': (('truck', 'car'), ('pickup', 'pickup truck', 'ute', 'truck')),
    'tipper-truck': (('truck',), ('tipper', 'dump truck', 'lorry', 'truck', 'construction truck')),
    'touring-motorcycle': (('motorcycle',), ('motorcycle', 'motorbike', 'bike', 'scooter')),
    'harbour-launch': (('boat',), ('boat', 'launch', 'motorboat', 'speedboat')),
    'courier-jet': (('aircraft',), ('plane', 'jet', 'aeroplane', 'airplane', 'aircraft', 'airliner')),
}
# Verified by tests/test_generators.py (corners of the box build with no validation errors).
RANGES = {
    # The body overlaps the wheels unless wheelbase <= length - 8 (clamped in build); length below the minimum
    # breaks the cab/bed decks (van, pickup) or the wheel arches (tipper).
    'grand-tourer': dict(length=(14, 32), wheelbase=(6, 24)),
    'delivery-van': dict(length=(18, 32), wheelbase=(6, 24)),
    'pickup': dict(length=(18, 32), wheelbase=(6, 24)),
    'tipper-truck': dict(length=(14, 32), wheelbase=(6, 24)),
}


def make(name):
    design = DESIGNS[name]
    road = design.get('profile', 'road') == 'road'

    def build(p):
        notes = []
        base = vehicle_colours(p['palette'])
        wheels = p.get('wheels')
        palette, roles = custom_palette(base, body=p.get('body'), roof=p.get('roof'), trim=p.get('trim'),
                                        glass=p.get('glass'), metal=p.get('accent'), wheel=wheels)
        kwargs = {}
        if road:
            length, wheelbase = p['length'] + p['length'] % 2, p['wheelbase'] + p['wheelbase'] % 2
            low_l, high_l = RANGES[name]['length']
            low_w, high_w = RANGES[name]['wheelbase']
            fixed_l = max(low_l, min(high_l, length))
            fixed_w = max(low_w, min(high_w, wheelbase, fixed_l - 8))
            fixed_w = fixed_w if fixed_w % 2 == 0 else fixed_w - 1
            if (fixed_l, fixed_w) != (p['length'], p['wheelbase']):
                notes.append(f'length/wheelbase adjusted to {fixed_l}/{fixed_w} (valid range for {name})')
            kwargs = dict(length=fixed_l, wheelbase=fixed_w)
        plan = vehicle_plan(name, palette, **kwargs)
        # The palette role of each placement was stamped when it was built (vehicle_details.RoleInt/RoleStr), so two
        # roles that share a colour stay apart. Wheel parts are told by their part, which no palette colour covers.
        def role_of(placement):
            ref = placement['ref']
            if ref in TYRES:
                return 'tyre'
            if ref in RIMS:
                return 'wheel'
            if ref in HOLDERS:
                return 'chassis'
            if ref in WINGS:
                return 'wing'
            if ref in ENGINES:
                return 'engine'
            if ref in GLAZING:
                return 'glass'
            return None
        tag_roles(plan, [('', role_of)])
        inherit_roles(plan)
        return plan, notes

    params = {
        'palette': {'enum': PALETTES, 'default': design['palette'], 'description': 'Preset colour scheme; the colour params override it'},
        'body': colour_param('Body panels'), 'roof': colour_param('Roof and cab top'),
        'trim': colour_param('Trim and bumpers'), 'wheels': colour_param('Wheel rims (tyres stay black)'),
        'glass': colour_param('Glazing (transparent colours)'), 'accent': colour_param('Metal fittings, bumpers, mirrors'),
    }
    if road:
        (ll, lh), (wl, wh) = RANGES[name]['length'], RANGES[name]['wheelbase']
        params = {
            'length': {'type': 'integer', 'minimum': ll, 'maximum': lh, 'x-even': True, 'default': design['length'],
                       'description': 'Body length in studs (even)'},
            'wheelbase': {'type': 'integer', 'minimum': wl, 'maximum': wh, 'x-even': True, 'default': design['wheelbase'],
                          'description': 'Axle distance in studs (even, at most length - 8)'},
            **params}
    if name in ('harbour-launch', 'courier-jet'):
        params.pop('wheels')
    families, kinds = INFO[name]
    return Generator(
        id=name, title=design['title'], families=families, kinds=kinds, description=design['lesson'],
        params=params, roles=(*ROLE_ORDER, 'wheel', 'tyre', *(('wing', 'engine') if name == 'courier-jet' else ())),
        build=build, coverage=('colours', 'length', 'wheelbase') if road else ('colours',),
        quality='Recognisable vehicle; fixed-size six-stud body' if road else 'Recognisable, fixed size')


GENERATORS = [make(name) for name in DESIGNS]
