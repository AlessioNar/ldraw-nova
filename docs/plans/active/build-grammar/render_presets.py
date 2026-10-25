"""Render every preset (front and 3/4) with the toolkit's LeoCAD path, and write course maps.

    LDRAW_DIR=... python docs/plans/active/build-grammar/render_presets.py [preset ...]

Needs leocad and xvfb-run on PATH. The LeoCAD BOM step may fail on old LeoCAD builds; the PNGs are still written.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from ldraw_tools import grammar  # noqa: E402
from ldraw_tools.grammar import engine, maps  # noqa: E402

HERE = Path(__file__).parent
EXTRA = {   # variants that show the grammar is one thing with knobs
    'variant-l-hall': {'name': 'l hall', 'footprint': {'shape': 'L', 'width': 18, 'depth': 12},
                       'roof': {'style': 'flat', 'parapet': 'battlement', 'crenel_colour': 'light grey'},
                       'walls': {'style': 'stone', 'colour': 'light grey', 'alt_colour': 'tan'}},
    'variant-round-towers': {'name': 'round towers', 'footprint': {'width': 16, 'depth': 12},
                             'corners': {'style': 'towers', 'tower_shape': 'round', 'tower_roof': 'cone',
                                         'tower_colour': 'light grey', 'tower_roof_colour': 'dark red', 'tower_extra_courses': 5},
                             'walls': {'style': 'stone', 'colour': 'tan', 'alt_colour': 'light grey'}},
    'variant-courtyard': {'name': 'courtyard', 'footprint': {'shape': 'courtyard', 'width': 18, 'depth': 18, 'court_width': 8, 'court_depth': 8},
                          'roof': {'style': 'flat', 'parapet': 'plain', 'crenel_colour': 'dark red'},
                          'walls': {'colour': 'dark red'}, 'storeys': {'count': 2}},
}


def render(name, config):
    result = engine.build(config)
    out = HERE / 'renders'
    out.mkdir(exist_ok=True)
    (HERE / 'maps').mkdir(exist_ok=True)
    canvas = result['canvas']
    storey_levels = sorted({it.y0 for it in canvas.items if it.label.startswith('wall') and it.label.endswith('c1')})
    (HERE / 'maps' / f'{name}.txt').write_text(maps.course_report(canvas, storey_levels), encoding='utf-8')
    (HERE / 'maps' / f'{name}.svg').write_text(maps.svg_sheet(canvas, name), encoding='utf-8')
    with tempfile.TemporaryDirectory() as tmp:
        plan = Path(tmp) / 'plan.json'
        plan.write_text(json.dumps(result['plan']))
        mpd = Path(tmp) / f'{name}.mpd'
        env = dict(os.environ)
        subprocess.run([str(ROOT / 'ldraw-agent'), 'build', str(plan), '--output', str(mpd), '--force'], check=True,
                       capture_output=True, env=env)
        shots = Path(tmp) / 'shots'
        subprocess.run(['xvfb-run', '-a', str(ROOT / 'ldraw-agent'), 'render', str(mpd), '--outdir', str(shots)],
                       capture_output=True, env=env)
        for view, suffix in (('front', 'front'), ('home', '34')):
            if (shots / f'{view}.png').exists():
                shutil.copy(shots / f'{view}.png', out / f'{name}-{suffix}.png')
    return result['stats']


if __name__ == '__main__':
    wanted = sys.argv[1:]
    todo = {k: grammar.preset_config(k) for k in grammar.PRESETS} | EXTRA
    for key, cfg in todo.items():
        if wanted and key not in wanted:
            continue
        print(key, render(key, cfg))
