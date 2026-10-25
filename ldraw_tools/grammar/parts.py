"""Parts-aware choices: pick the length, window, door leaf, plate or tile the builder owns.

Every helper is a no-op without an inventory. With one, 'prefer' mode orders the choices by ownership and 'only'
mode restricts them to owned parts, falling back to the wanted part (and so to a reported shortage) when nothing
owned fits. Each swap in 'only' mode is recorded in the inventory report as a substitution.
"""
from __future__ import annotations

from . import catalog, tiling

SLOPE_COST = {4: 1.0, 3: 1.35, 2: 1.0, 1: 1.8}
NOT_OWNED = 1.8          # penalty per part length the builder lacks, in 'prefer' mode
DOOR_ORDER = ('plain', 'smooth', 'barred', 'glazed')


def only(ctx):
    return ctx.inv is not None and ctx.inv.mode == 'only'


def fit_run(ctx, n, start, avoid, table, colour, lengths=tiling.BRICK_LENGTHS, cost=None):
    """tiling.tile_run over `lengths` of the part family `table` ({length: code}), steered by what is owned in colour."""
    inv = ctx.inv
    owned = None
    if inv is not None:
        have = {k for k in lengths if inv.left(table[k], colour) > 0}
        if inv.mode == 'only' and have:
            try:
                return tiling.tile_run(n, start, avoid, tuple(k for k in lengths if k in have), cost)
            except ValueError:
                pass
            for k in lengths:
                if k not in have:
                    inv.substitute('length', table[k], None, 'length not owned in this colour')
        owned = (lambda length: 0.0 if length in have else NOT_OWNED)
    return tiling.tile_run(n, start, avoid, lengths, cost, owned=owned)


def cover(ctx, cells, kind, colour):
    """tiling.rect_cover restricted to owned plates or tiles in 'only' mode; tiles become plates when none are owned."""
    if not only(ctx):
        return tiling.rect_cover(cells, kind)
    inv = ctx.inv

    def owned(k):
        one, two = (catalog.PLATE, catalog.PLATE2) if k == 'plate' else (catalog.TILE, catalog.TILE2)
        return {c for c in (*one.values(), *two.values()) if inv.left(c, colour) > 0}, one

    have, one = owned(kind)
    if one[1] not in have:
        if kind == 'tile':
            plates, plate_one = owned('plate')
            if plate_one[1] in plates:
                inv.substitute('part', 'tile', 'plate', 'no tiles owned in this colour')
                return tiling.rect_cover(cells, 'plate', allowed=plates)
        return tiling.rect_cover(cells, kind)
    return tiling.rect_cover(cells, kind, allowed=have)


def window_needs(ctx, kind):
    """[(part, colour, count)] one window of this kind uses."""
    cfg = ctx.cfg['openings']
    frame = ctx.code(cfg['frame_colour'])
    glass = ctx.code(cfg['glass_colour'], glass=True)
    c = catalog
    return {
        'rect': [(c.WINDOW_4X3, frame, 1), (c.PANE_4X3, glass, 2)],
        'wide': [(c.WINDOW_4X3, frame, 2), (c.PANE_4X3, glass, 4)],
        'small': [(c.WINDOW_2X2, frame, 1)],
        'tall': [(c.WINDOW_2X3, frame, 1)],
        'arch': [(c.WINDOW_ARCH, frame, 1), (c.PANE_ARCH, glass, 1), ('3023b', frame, 1)],
        'slit': [],
    }[kind]


def window_owned(ctx, kind):
    return all(ctx.inv.left(code, colour) >= n for code, colour, n in window_needs(ctx, kind))


def window_kind(ctx, kind):
    """The wanted kind, or in 'only' mode the first kind down its fallback chain whose parts are owned (None: none)."""
    if not only(ctx) or kind is None:
        return kind
    wanted = kind
    while kind and not window_owned(ctx, kind):
        kind = catalog.FALLBACK.get(kind)
    if kind != wanted:
        ctx.inv.substitute('part', catalog.WINDOWS[wanted]['code'] or 'slit', catalog.WINDOWS[kind]['code'] if kind else None,
                           f"window '{wanted}' not owned")
    return kind


def door_style(ctx, style, colour):
    """The wanted door leaf style, or in 'only' mode the first style whose leaf is owned in this colour."""
    if not only(ctx) or ctx.inv.left(catalog.DOOR_LEAF[style], colour) > 0:
        return style
    for alt in DOOR_ORDER:
        if ctx.inv.left(catalog.DOOR_LEAF[alt], colour) > 0:
            ctx.inv.substitute('part', catalog.DOOR_LEAF[style], catalog.DOOR_LEAF[alt], 'door leaf not owned')
            return alt
    return style
