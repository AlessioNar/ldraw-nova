"""Hash every generator plan (roles stripped) for a few parameter sets; used to prove role changes leave plans identical.

    python tools/plan_hashes.py > tests/fixtures/generator_plan_hashes.json
"""
import hashlib
import json
import sys

from ldraw_tools import generators

CASES = {
    'house': [{}, dict(width=12, depth=8, courses=8, windows=4, porch=True, chimney='left', dormer=True),
              dict(roof_style='flat', door_side='centre', wall_colour='red', roof_colour='blue')],
    'starfighter': [{}, dict(length=24, wingspan=24, engines=4, wing_style='swept')],
    'grand-tourer': [{}, dict(body='red', roof='white', trim='blue', wheels='dark grey', glass='trans black')],
    'delivery-van': [{}, dict(length=24, wheelbase=12)],
    'pickup': [{}], 'tipper-truck': [{}], 'touring-motorcycle': [{}], 'harbour-launch': [{}], 'courier-jet': [{}],
    'arcade-bay': [{}, dict(height=6, colour='red')], 'street-lantern': [{}],
    'toy-train': [{}, dict(wagons=0), dict(wagons=5, loco_colour='green', wagon_colour='yellow')],
    'cargo-ship': [{}, dict(length=16, bays=7), dict(length=36, bays=6, hull_colour='black', cargo_colour='red')],
    'stool': [{}, dict(courses=4, seat_colour='yellow')], 'chair': [{}, dict(back_courses=4, frame_colour='black')],
    'dining-table': [{}, dict(width=8, depth=6, leg_courses=3, top_colour='white'), dict(width=2, depth=2, leg_courses=1)],
    'bed': [{}, dict(width=6, length=10, mattress_colour='light blue')], 'sofa': [{}, dict(width=10, depth=5, cushion_colour='red')],
    'bookshelf': [{}, dict(width=7, shelves=4, books=False), dict(width=3, shelves=1, book_colour='blue')],
}


def strip(value):
    if isinstance(value, dict):
        return {k: strip(v) for k, v in value.items() if k != 'role'}
    if isinstance(value, list):
        return [strip(v) for v in value]
    return value


def digest(plan):
    return hashlib.sha1(json.dumps(strip(plan), sort_keys=True, default=str).encode()).hexdigest()


def main():
    out = {}
    for gid, cases in CASES.items():
        for i, params in enumerate(cases):
            out[f'{gid}#{i}'] = dict(params=params, sha1=digest(generators.generate(gid, params)['plan']))
    json.dump(out, sys.stdout, indent=1, sort_keys=True)


if __name__ == '__main__':
    main()
