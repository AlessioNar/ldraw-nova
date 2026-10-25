"""Doors and windows: where they go on each facade (rhythm), and the real parts that fill them."""
from __future__ import annotations

import math

from . import catalog, footprint
from .canvas import rotate

YAW = {'front': 0, 'back': 180, 'left': 90, 'right': -90}
PIER = {'dense': 1, 'regular': 2, 'sparse': 3}


def half_up(x):
    return math.floor(x + 0.5)


def fit_slots(a, b, width, pier, count=None):
    """Left edges of up to `count` openings of `width` evenly spaced in [a, b); gaps between openings >= pier.

    Spare room is shared equally between all n+1 gaps (the two ends included), so a row is symmetric.
    """
    length = b - a
    n = max(0, (length + pier) // (width + pier))
    if count is not None:
        n = min(n, count)
    if n <= 0:
        return []
    extra = (length - n * width - (n - 1) * pier) / (n + 1)
    return [a + half_up(extra * (k + 1)) + k * (width + pier) for k in range(n)]


def kind_for(want, storey_courses, span_len, pier, ground):
    """Largest window kind <= want that fits the storey height and the span; None when nothing does."""
    kind = want
    while kind and kind != 'none':
        spec = catalog.WINDOWS[kind]
        height_ok = spec['courses'] + 1 <= storey_courses
        if height_ok and spec['width'] <= span_len:
            return kind
        kind = catalog.FALLBACK.get(kind)
    return None


def base_course(kind, n, ground):
    spec = catalog.WINDOWS[kind]
    return max(1 if n - spec['courses'] >= 1 else 0, (n - spec['courses']) // 2)


def plan_storey(ctx, st):
    """Fill st.openings: dicts with kind, side, line, u0, width, c0, courses."""
    cfg = ctx.cfg['openings']
    t = ctx.t
    n = st.courses
    ground = st.index == 0
    pier = PIER[cfg['rhythm']]
    want = cfg['ground_windows'] if ground else cfg['upper_windows']
    facs = footprint.facades(st.cells - st.blocked, t, margin=0)
    door_fac = None
    if ground and cfg['door'] != 'none':
        cands = [f for f in facs if f['side'] == cfg['door_facade'] and f['usable'][1] - f['usable'][0] >= 4]
        if cands:
            door_fac = max(cands, key=lambda f: (f['b'] - f['a'], -f['line'] if f['side'] in ('front', 'left') else f['line']))
            ctx.door = door_fac
    for fac in facs:
        side = fac['side']
        ua, ub = fac['usable']
        if ground and fac is door_fac:
            L = fac['b'] - fac['a']
            pos = cfg['door_position']
            if pos == 'centre':
                u0 = fac['a'] + (L - 4) // 2
            elif pos == 'left':
                u0 = ua
            else:
                u0 = ub - 4
            u0 = min(max(u0, ua), ub - 4)
            st.openings.append(dict(kind='door', side=side, line=fac['line'], u0=u0, width=4, c0=0, courses=6))
            ctx.door = dict(fac, u0=u0)
            spans = [(ua, u0 - 1), (u0 + 5, ub)]
            door_centre = u0 + 2
        else:
            spans = [(ua, ub)]
            door_centre = None
        if side not in cfg['sides'] or want == 'none':
            continue
        want_here = want
        key = (side, fac['line'], fac['a'])
        count = cfg['count'] or None
        if ground:
            centres = []
            per = None if count is None else max(1, math.ceil(count / len(spans)))
            for (a, b) in spans:
                kind = kind_for(want_here, n, b - a, pier, ground)
                if kind is None:
                    continue
                w = catalog.WINDOWS[kind]['width']
                for u0 in fit_slots(a, b, w, pier, per):
                    st.openings.append(dict(kind=kind, side=side, line=fac['line'], u0=u0, width=w,
                                            c0=base_course(kind, n, ground), courses=catalog.WINDOWS[kind]['courses']))
                    centres.append(u0 + w / 2)
            ctx.facade_slots[key] = centres + ([door_centre] if door_centre is not None and cfg['above_door'] else [])
            if kind_for(want, n, 99, pier, ground) is not None and not centres and door_fac is None:
                pass
        else:
            known = ctx.facade_slots.get(key)
            if known:
                for centre in sorted(known):
                    kind = kind_for(want, n, ub - ua, pier, ground)
                    if kind is None:
                        break
                    w = catalog.WINDOWS[kind]['width']
                    u0 = half_up(centre - w / 2)
                    if u0 < ua or u0 + w > ub:
                        continue
                    st.openings.append(dict(kind=kind, side=side, line=fac['line'], u0=u0, width=w,
                                            c0=base_course(kind, n, ground), courses=catalog.WINDOWS[kind]['courses']))
            else:
                kind = kind_for(want, n, ub - ua, pier, ground)
                if kind is None:
                    continue
                w = catalog.WINDOWS[kind]['width']
                for u0 in fit_slots(ua, ub, w, pier, count):
                    st.openings.append(dict(kind=kind, side=side, line=fac['line'], u0=u0, width=w,
                                            c0=base_course(kind, n, ground), courses=catalog.WINDOWS[kind]['courses']))
    carve(ctx, st)


def cell_of(o, u, k):
    side, line = o['side'], o['line']
    return {'front': (u, line + k), 'back': (u, line - k), 'left': (line + k, u), 'right': (line - k, u)}[side]


def carve(ctx, st):
    for o in st.openings:
        for c in range(o['c0'], o['c0'] + o['courses']):
            cells = st.carve.setdefault(c, set())
            for u in range(o['u0'], o['u0'] + o['width']):
                for k in range(ctx.t):
                    cells.add(cell_of(o, u, k))


def origin(o):
    side, line = o['side'], o['line']
    mid = o['u0'] + o['width'] / 2
    return (mid, line + .5) if side in ('front', 'back') else (line + .5, mid)


def emit(ctx, st):
    cfg = ctx.cfg['openings']
    canvas = ctx.canvas
    frame = ctx.code(cfg['frame_colour'])
    glass = ctx.code(cfg['glass_colour'], glass=True)
    leaf = ctx.code(cfg['door_colour'])
    for o in st.openings:
        kind, side = o['kind'], o['side']
        yaw = YAW[side]
        x, z = origin(o)
        y0 = st.y_floor + 3 * o['c0']
        label = f"{kind} {side} s{st.index}"
        if kind == 'door':
            door = canvas.put(catalog.DOOR_FRAME, frame, 'door', x, z, y0, yaw, label='door frame ' + side)
            style = cfg['door']
            dx, dz = rotate(-1.6, .25, yaw)
            leaf_colour = frame if style in ('smooth', 'glazed') and False else leaf
            canvas.put(catalog.DOOR_LEAF[style], leaf_colour, 'door', x + dx, z + dz, y0, yaw, host=door.index,
                       label=f'door leaf {style}', top_ldu=8 * y0 + 144)
        elif kind == 'rect' or kind == 'wide':
            centres = [0] if kind == 'rect' else [-2, 2]
            for cx in centres:
                ox, oz = rotate(cx, 0, yaw)
                win = canvas.put(catalog.WINDOW_4X3, frame, 'window', x + ox, z + oz, y0, yaw, label=label)
                for sgn in (-1, 1):
                    px, pz = rotate(cx + 1.6 * sgn, .25, yaw)
                    canvas.put(catalog.PANE_4X3, glass, 'glass', x + px, z + pz, y0, yaw + sgn * 90, host=win.index,
                               label='pane', top_ldu=8 * y0 + 68)
        elif kind in ('small', 'tall'):
            code = catalog.WINDOW_2X2 if kind == 'small' else catalog.WINDOW_2X3
            canvas.put(code, frame, 'window', x, z, y0, yaw, label=label)
        elif kind == 'arch':
            canvas.put('3023b', frame, 'trim', x, z, y0, yaw, label='arch sill')
            win = canvas.put(catalog.WINDOW_ARCH, frame, 'window', x, z, y0 + 1, yaw, label=label)
            dx, dz = rotate(0, .2, yaw)
            canvas.put(catalog.PANE_ARCH, glass, 'glass', x + dx, z + dz, y0 + 1, yaw, host=win.index, label='pane',
                       top_ldu=8 * (y0 + 1 + 8) - 24)
        # slit: an open gap, nothing to place
