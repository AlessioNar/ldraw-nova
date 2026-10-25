"""The worker's "template" op: what the workspace's "Make this a template" asks of the toolkit (see make.py).

    {"op": "template", "action": "check",  "plan": ..., "hints": "", "corrections": [...], "gate": true}
    {"op": "template", "action": "make",   "plan": ..., "jobdir": "...", "id": ..., "title": ..., "kinds": [...], ...}
    {"op": "template", "action": "finalize", "jobdir": "...", "taken": [ids], "disabled": [...], "title": ..., "kinds": [...]}

`make` runs for minutes: it writes jobdir/progress.json as it goes and jobdir/result.json (the template, its knobs, what
was rejected, a thumbnail per extreme value when a renderer exists) when it is done. `finalize` returns the template to
store, minus the knobs the person switched off; the backend writes it into their templates folder.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from . import BUNDLED, REGISTRY, make

MAX_THUMBS = 12


def _write(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, default=str), encoding='utf-8')
    temporary.replace(path)


def _progress(jobdir):
    started = time.time()

    def tell(step, detail=''):
        _write(Path(jobdir) / 'progress.json', dict(step=step, detail=detail, elapsed=round(time.time() - started, 1), at=time.time()))
    return tell


def check(request, parts):
    plan = request['plan']
    out = {}
    if request.get('gate', True):
        out['gate'] = make.gate(plan, parts)
    if request.get('preview', True) and (not request.get('gate', True) or out['gate']['ok'] or request.get('preview_anyway')):
        out['preview'] = make.preview(plan, parts, hints=request.get('hints', ''), corrections=request.get('corrections'))
    return dict(ok=True, **out)


def _thumbnails(pipeline, data, knobs, jobdir):
    """A picture of each knob's extreme values, built from the template itself. Best effort: a missing renderer
    leaves the knob without pictures, never fails the job."""
    count = 0
    for knob in knobs:
        for extreme in knob['extremes']:
            if count >= MAX_THUMBS:
                return
            count += 1
            folder = Path(jobdir) / 'thumbs' / str(count)
            try:
                reply = pipeline.run(folder, plan=make.variant_plan(data, knob['param'], extreme['value']), name='variant',
                                     geometry=False, glb=False, renders=['home'])
                image = (reply.get('paths') or {}).get('home.png')
                if image:
                    extreme['thumb'] = Path(image).relative_to(Path(jobdir).resolve()).as_posix()
            except Exception:       # noqa: BLE001 - pictures are a convenience
                return


def run_make(request, pipeline):
    jobdir = Path(request['jobdir'])
    jobdir.mkdir(parents=True, exist_ok=True)
    tell = _progress(jobdir)
    tell('start', 'Loading the part library')
    parts = pipeline.load()
    data, report, knobs, rejected = make.make(
        request['plan'], parts, id=make.slug(request.get('template_id') or request['title']), title=request['title'],
        kinds=request.get('kinds') or [request['title'].casefold()], families=request.get('families') or ['generic'],
        description=request.get('description') or request['title'], source=request.get('source'),
        hints=request.get('hints', ''), corrections=request.get('corrections'), progress=tell)
    tell('pictures', 'Rendering the extreme values')
    _thumbnails(pipeline, data, knobs, jobdir)
    result = dict(template=data, knobs=knobs, rejected=rejected, roles=data['roles'], stripes=data.get('subroles', {}), coverage=data['coverage'],
                  quality=data['quality'], report={k: v for k, v in report.items() if k in ('leaves', 'domain', 'baseline', 'size', 'corners', 'corrected')})
    _write(jobdir / 'result.json', result)
    tell('done', 'Finished')
    return dict(ok=True, knobs=len(knobs))


def _free_id(wanted, taken):
    taken = set(taken) | set(REGISTRY) | {p.stem for p in BUNDLED.glob('*.json')}
    if wanted not in taken:
        return wanted
    number = 2
    while f'{wanted}-{number}' in taken:
        number += 1
    return f'{wanted}-{number}'


def run_finalize(request):
    """The template to store: the job's result minus the knobs the person switched off, under a free id. The caller
    (the backend, which owns the templates folder) writes it; this process only ever reads that folder."""
    result = json.loads((Path(request['jobdir']) / 'result.json').read_text(encoding='utf-8'))
    data = make.without_knobs(result['template'], request.get('disabled') or [])
    if request.get('title'):
        data['title'] = str(request['title']).strip()[:120] or data['title']
    if request.get('kinds'):
        data['kinds'] = [str(k).strip().casefold() for k in request['kinds'] if str(k).strip()][:12] or data['kinds']
    if request.get('description'):
        data['description'] = str(request['description']).strip()[:500]
    data['id'] = _free_id(make.slug(request.get('template_id') or data['title']), request.get('taken') or ())
    return dict(ok=True, template=data)


def run_request(request, pipeline):
    action = request.get('action')
    if action == 'check':
        return check(request, pipeline.load())
    if action == 'make':
        return run_make(request, pipeline)
    if action == 'finalize':
        return run_finalize(request)
    raise ValueError(f'Unknown template action {action!r}')
