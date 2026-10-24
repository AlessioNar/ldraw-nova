"""Before/after renders of template variants and of role edits on imported models.

    python render_variants.py OUTDIR SPEC.json

SPEC.json is a list of
  {"name": "truck-long", "template": "flatbed-truck", "params": {"z_delta": 8}}
  {"name": "house-blue-roof", "src": "544-1_House.mpd", "edits": [["recolour_role", "roof", "blue"], ["remove_role", "chimney"]]}
Each item is built, checked for validation errors and rendered to OUTDIR/<name>.png (LeoCAD, home view).
"""
import json
import sys
import time
from pathlib import Path

from ldraw_tools import editor, role_edits, roles, templates
from ldraw_tools.builder import build_plan
from ldraw_tools.common import get_parts, library_path, models_path
from ldraw_tools.external import render


def plan_for(item, parts, facts):
    if 'template' in item:
        return templates.generate(item['template'], item.get('params')).get('plan'), []
    path = Path(item['src'])
    path = path if path.exists() else models_path() / item['src']
    plan, _ = editor.import_mpd(path.read_text(encoding='utf-8', errors='ignore'), parts, name=path.name)
    plan, _ = roles.tag_plan(plan, parts, hints=plan['sections'][0]['description'], facts=facts)
    notes = []
    for edit in item.get('edits', []):
        ops = getattr(role_edits, edit[0])(plan, *edit[1:], parts=parts) if edit[0].startswith('recolour') else getattr(role_edits, edit[0])(plan, *edit[1:])
        notes.append(f'{edit[0]} {edit[1:]}: {len(ops)} ops {ops.notes}')
        plan = editor.apply(plan, list(ops), parts)
    return plan, notes


def main(outdir, spec):
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    parts = get_parts()
    facts = roles.PartFacts(parts)
    summary = {}
    for item in json.loads(Path(spec).read_text()):
        t0 = time.time()
        try:
            plan, notes = plan_for(item, parts, facts)
            text, _, diagnostics = build_plan(plan, parts)
            errors = [d['message'] for d in diagnostics if d['severity'] == 'error']
            mpd = out / f"{item['name']}.mpd"
            mpd.write_text(text)
            render(mpd, library_path(), out / item['name'], views=['home'], timeout=300)
            (out / item['name'] / 'home.png').replace(out / f"{item['name']}.png")
            mpd.unlink()
            summary[item['name']] = dict(parts=sum(len(s) for sec in plan['sections'] for s in sec['steps']), errors=errors[:3],
                                         notes=notes, seconds=round(time.time() - t0, 1))
        except Exception as exc:  # noqa: BLE001
            summary[item['name']] = dict(error=str(exc)[:300])
        print(item['name'], summary[item['name']], flush=True)
    (out / 'summary.json').write_text(json.dumps(summary, indent=1))


if __name__ == '__main__':
    main(*sys.argv[1:3])
