"""Throwaway: in-process timings (what a resident worker would pay) + retrieval timings + a rule-parsed edit.
   cd /opt/ldraw-nova && .venv/bin/python /tmp/measure2.py
"""
import json, subprocess, time, copy, sys
T = {}
t0 = time.time()
from ldraw_tools.common import get_parts
from ldraw_tools.builder import build_plan
from ldraw_tools.geometry import analyze_geometry
from ldraw_tools.validation import validate_text
from ldraw_tools import editor
from ldraw_tools.vehicles import vehicle_plan
T['import_s'] = round(time.time() - t0, 2)

t = time.time(); parts = get_parts(); T['load_parts_s'] = round(time.time() - t, 2)

def timed(label, fn):
    t = time.time(); r = fn(); T[label] = round(time.time() - t, 2); return r

plan = timed('plan_s', lambda: vehicle_plan('tipper-truck'))
text, model, diags = timed('build_inproc_s', lambda: build_plan(plan, parts))
geo = timed('geometry_inproc_s', lambda: analyze_geometry(model, parts, detail='summary', contacts='all'))
T['parts'] = geo['occurrence_count']; T['components'] = geo.get('optimistic_component_count')

# deterministic follow-up: "make the cabin roof blue" -> recolour entries with the roof colour in section cabin
roof_ids = []
for s in plan['sections']:
    if s['name'].endswith('cabin.ldr'):
        for st in s['steps']:
            for e in st:
                if e['colour'] == '@colours.Tan':
                    roof_ids.append((s['name'], e['id']))
T['edit_entries'] = len(roof_ids)
edits = [dict(op='recolour', section=s, id=i, colour='@colours.Blue') for s, i in roof_ids]
new = timed('edit_apply_s', lambda: editor.apply(plan, edits, parts))
if isinstance(new, tuple): new = new[0]
r = timed('edit_rebuild_s', lambda: editor.rebuild(new, parts))
# validate (in-process) after edit
text2, model2, d2 = build_plan(new, parts)
g2 = timed('edit_geometry_s', lambda: analyze_geometry(model2, parts, detail='summary', contacts='all'))

# retrieval timings through the CLI
A = '/opt/ldraw-nova/ldraw-agent'
def cli(label, args):
    t = time.time(); p = subprocess.run([A, *args], capture_output=True, text=True); T[label] = round(time.time() - t, 2)
    return p.stdout
out = cli('search_models_cli_s', ['search', 'models', 'car', '--limit', '5'])
T['search_models_head'] = out[:600]
cli('discover_models_fts_s', ['discover', 'search', 'models', 'small red sports car', '--engine', 'fts', '--limit', '8'])
out = cli('sets_search_s', ['sets', 'search', 'truck', '--limit', '5'])
T['sets_head'] = out[:400]
cli('part_lookup_s', ['part', '3001', '3020', '3022', '3795'])
cli('examples_vehicle_s', ['examples', '--family', 'vehicle', 'truck'])
print(json.dumps(T, indent=1))
