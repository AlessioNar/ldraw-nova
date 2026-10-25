"""Validation with the toolkit's own build and geometry analysis."""
from __future__ import annotations


def check(plan, parts=None):
    """Build the plan and analyse its geometry.

    Returns dict(ok, errors, components, confirmed_components, parts). ok means: the plan builds, no error
    diagnostics (rectangular body overlaps are errors), and the optimistic connectivity graph is one component.
    """
    from ..builder import build_plan
    from ..common import get_parts
    from ..geometry import analyze_geometry
    parts = parts or get_parts()
    _, model, diagnostics = build_plan(plan, parts)
    errors = [d for d in diagnostics if d['severity'] == 'error']
    geometry = analyze_geometry(model, parts, detail='summary', contacts='all')
    errors += [d for d in geometry['diagnostics'] if d['severity'] == 'error']
    comps = geometry['optimistic_component_count']
    return dict(ok=not errors and comps == 1, errors=errors, components=comps,
                confirmed_components=geometry['confirmed_component_count'], parts=geometry['occurrence_count'])
