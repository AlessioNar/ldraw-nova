"""config -> plan. Deterministic: the same config (and inventory) always gives the same plan."""
from __future__ import annotations

from . import details, interior, openings, schema, structure, walls
from .context import Ctx


def build(config, inventory=None):
    """Assemble a building. Returns dict(config, notes, plan, canvas, stats)."""
    cfg, notes = schema.normalise(config)
    ctx = Ctx(cfg, inventory)
    ctx.notes.extend(notes)
    # Storey footprints are needed before the base plate (it surrounds them all).
    probe = Ctx(cfg)
    structure.make_layout(probe)
    ctx.storeys_preview = [st.cells for st in probe.storeys]
    details.base_plate(ctx)
    structure.make_layout(ctx)
    for st in ctx.storeys:
        override = None
        if st.index == 0 and cfg['details']['sign'] != 'none':
            front = {c for c in st.cells if (c[0], c[1] - 1) not in st.cells}
            sign = ctx.code(cfg['details']['sign_colour'])

            def override(c, course, colour, role, _front=front, _top=st.courses - 1, _sign=sign):
                return (_sign, 'detail') if course == _top and c in _front else (colour, role)
        openings.plan_storey(ctx, st)
        walls.build(ctx, st, override)
        openings.emit(ctx, st)
        interior.partition(ctx, st)
        structure.round_towers(ctx, st)
        structure.band(ctx, st)
        if cfg['storeys']['eaves'] and st.kind == 'main' and st.index < cfg['storeys']['count'] - 1:
            structure.eave_skirt(ctx, st)
        if st.index == 0:
            details.balcony(ctx)
    structure.main_top(ctx)
    structure.tower_tops(ctx)
    details.chimney(ctx)
    details.porch(ctx)
    details.path(ctx)
    details.fence(ctx)
    canvas = ctx.canvas
    name = (cfg['name'] or 'building').strip() or 'building'
    desc = f"{name}: {cfg['footprint']['shape']} {cfg['footprint']['width']}x{cfg['footprint']['depth']} studs, {cfg['storeys']['count']} storeys, {cfg['roof']['style']} roof"
    plan = canvas.plan(name.replace(' ', '-'), desc)
    comps = canvas.components()
    stats = dict(parts=len(canvas.items), components=len(comps), largest=len(comps[0]) if comps else 0)
    return dict(config=cfg, notes=ctx.notes, plan=plan, canvas=canvas, stats=stats,
                inventory=inventory.report() if inventory is not None else None)
