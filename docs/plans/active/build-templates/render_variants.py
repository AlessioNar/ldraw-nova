"""Before/after renders of template variants and of role edits on imported models.

    python render_variants.py OUTDIR SPEC.json [--no-render]

SPEC.json is a list of
  {"name": "truck-long", "template": "flatbed-truck", "params": {"z_delta": 4}}
  {"name": "house-blue-roof", "src": "4956-1_House-with-Arch.mpd", "edits": [["recolour_role", "roof", "blue"]]}
Each item is built, checked (validation errors, and for templates the same geometry check the derivation used) and
rendered to OUTDIR/<name>.png (LeoCAD, home view, snap.py). OUTDIR/summary.json lists parts, errors, connected
components, the role edit's notes and timings.
"""
import argparse
import json
import time
from pathlib import Path

from snap import snap

from ldraw_tools import editor, role_edits, roles, templates
from ldraw_tools.builder import build_plan
from ldraw_tools.common import get_parts, library_path, models_path
from ldraw_tools.templates import derive


def plan_for(item, parts, facts):
    if 'template' in item:
        return templates.generate(item['template'], item.get('params'))['plan'], []
    path = Path(item['src'])
    path = path if path.exists() else models_path() / item['src']
    plan, _ = editor.import_mpd(path.read_text(encoding='utf-8', errors='ignore'), parts, name=path.name)
    plan, _ = roles.tag_plan(plan, parts, hints=plan['sections'][0].get('description', ''), facts=facts)
    notes = []
    for name, *args in item.get('edits', []):
        function = getattr(role_edits, name)
        ops = function(plan, *args, parts=parts) if name.startswith('recolour') else function(plan, *args)
        notes.append(f'{name} {args}: {len(ops)} ops, {len(ops.matched)} matched; {"; ".join(ops.notes)}')
        plan = editor.apply(plan, list(ops), parts)
    return plan, notes


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('outdir')
    ap.add_argument('spec')
    ap.add_argument('--no-render', action='store_true')
    args = ap.parse_args(argv)
    out = Path(args.outdir)
    out.mkdir(parents=True, exist_ok=True)
    parts = get_parts()
    facts = roles.PartFacts(parts)
    summary = {}
    for item in json.loads(Path(args.spec).read_text(encoding='utf-8')):
        t0 = time.time()
        try:
            plan, notes = plan_for(item, parts, facts)
            text, _, diagnostics = build_plan(plan, parts)
            errors = [d['message'] for d in diagnostics if d['severity'] == 'error']
            row = dict(parts=sum(len(s) for sec in plan['sections'] for s in sec['steps']), errors=errors[:3], notes=notes)
            if 'template' in item:
                geometry_errors, components = derive.check_plan(plan, parts)
                row.update(geometry_errors=len(geometry_errors), components=components)
            if not args.no_render:
                mpd = out / f"{item['name']}.mpd"
                mpd.write_text(text, encoding='utf-8')
                snap(mpd, out / f"{item['name']}.png", library_path())
                mpd.unlink()
            row['seconds'] = round(time.time() - t0, 1)
            summary[item['name']] = row
        except Exception as exc:  # noqa: BLE001
            summary[item['name']] = dict(error=str(exc)[:300])
        print(item['name'], summary[item['name']], flush=True)
    (out / 'summary.json').write_text(json.dumps(summary, indent=1), encoding='utf-8')


if __name__ == '__main__':
    main()
