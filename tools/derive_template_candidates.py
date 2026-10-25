#!/usr/bin/env python3
"""Bulk-derive template candidates from the reference models, ranked by soundness (issue #56, offline half).

Nothing here ships: results go to a local folder that git ignores (data/local/template-candidates by default).
A person picks the 50-100 worth publishing from `ranking.csv`, then copies those templates into the bundled set.

    tools/derive_template_candidates.py gate                  quality-gate every model, write the ranking
    tools/derive_template_candidates.py derive --top 40       derive the best gated models into templates/
    tools/derive_template_candidates.py rank                  rebuild ranking.csv from what is on disk

Options: --library DIR (else LDRAW_DIR), --models DIR, --out DIR, --jobs N, --pattern GLOB, --limit N, --force.
The gate takes a few seconds a model and the derivation 0.5-40 minutes, so both run in parallel processes and
resume: a model with a result on disk is skipped unless --force is given.

Layout of --out:
    gate/<model>.json        the gate verdict of one model (parts, errors, components, soundness, reasons)
    templates/<id>.json      a derived template; <id>.report.json beside it holds the derivation report
    derive/<model>.json      the derivation summary (knobs, rejected, corners) or why it failed
    ranking.csv, ranking.json
"""
from __future__ import annotations

import argparse
import concurrent.futures
import csv
import json
import os
import signal
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DEFAULT_OUT = ROOT / 'data' / 'local' / 'template-candidates'
DEFAULT_MODELS = ROOT / 'data' / 'models-annotated'
KNOB_BONUS = 0.25                   # per verified knob family, up to MAX_KNOBS
MAX_KNOBS = 4
TITLE_CHARS = 80                    # model descriptions run long; a template title should not
_parts = None


def soundness(gate):
    """0..1: how safely the build can be varied. 0 when the gate refuses it; otherwise higher for fewer loose groups."""
    from ldraw_tools.templates import make
    if not gate['ok']:
        return 0.0
    ratio = gate['components'] / max(1, gate['parts'])
    return round(max(0.0, 1.0 - ratio / make.MAX_COMPONENT_RATIO), 3)


def score(gate, derived=None):
    """Ranking score: soundness, lifted by the knobs a derivation verified (a template with nothing to vary is worth little)."""
    base = soundness(gate)
    if derived is None or not derived.get('ok'):
        return base
    knobs = min(MAX_KNOBS, len(derived.get('knobs', [])))
    return round(base * (1 + KNOB_BONUS * knobs) if derived.get('baseline_errors', 0) == 0 else 0.0, 3)


def _init(library):
    global _parts
    from ldraw_tools.common import get_parts, library_path
    _parts = get_parts(library_path(library))


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, indent=1, default=str), encoding='utf-8')
    temporary.replace(path)


def _title(text, fallback):
    for line in text.splitlines()[:6]:
        words = line.strip().split(None, 2)
        if len(words) == 3 and words[0] == '0' and words[1] not in ('FILE', 'Name:', 'Author:'):
            return words[2].strip()
    return fallback


def gate_one(task):
    model, out = task
    from ldraw_tools.editor import import_mpd
    from ldraw_tools.templates import make
    path = Path(model)
    result = dict(model=path.name, ok=False)
    started = time.time()
    try:
        text = path.read_text(encoding='utf-8', errors='replace')
        plan, warnings = import_mpd(text, _parts, name=path.name)
        gate = make.gate(plan, _parts)
        result.update(gate, title=_title(text, path.stem), import_warnings=len(warnings))
    except Exception as exc:                    # noqa: BLE001 - one bad model must not stop the bulk run
        result.update(reasons=[f'{type(exc).__name__}: {exc}'], parts=0, errors=0, components=0, title=path.stem, import_warnings=0)
    result['soundness'] = soundness(result) if 'components' in result else 0.0
    result['seconds'] = round(time.time() - started, 1)
    _write(Path(out) / 'gate' / f'{path.stem}.json', result)
    return result


class Timeout(Exception):
    pass


def _alarm(*_):
    raise Timeout()


def derive_one(task):
    from ldraw_tools import templates
    from ldraw_tools.templates import derive, make
    model, out, timeout = task
    path = Path(model)
    gate = json.loads((Path(out) / 'gate' / f'{path.stem}.json').read_text(encoding='utf-8'))
    summary = dict(model=path.name, ok=False)
    started = time.time()
    signal.signal(signal.SIGALRM, _alarm)
    signal.alarm(timeout)
    try:
        text = path.read_text(encoding='utf-8', errors='replace')
        title = (gate.get('title') or path.stem)[:TITLE_CHARS]
        template_id = make.slug(path.stem)
        data, report = derive.derive_from_mpd(
            text, _parts, name=path.name, id=template_id, title=title, families=['generic'], kinds=[title.casefold()],
            description=title)
        knobs, rejected = make.knobs_of(data, report)
        compact = derive.compact(data)
        templates.from_data(compact)            # the stored form must load
        _write(Path(out) / 'templates' / f'{template_id}.json', compact)
        _write(Path(out) / 'templates' / f'{template_id}.report.json', report)
        summary.update(ok=True, id=template_id, knobs=[k['param'] for k in knobs], rejected=[r['param'] for r in rejected],
                       baseline_errors=report['baseline']['errors'], baseline_components=report['baseline']['components'],
                       corners={k: v.get('ok') for k, v in report.get('corners', {}).items()}, domain=report['domain'])
    except Timeout:
        summary['error'] = f'timed out after {timeout}s'
    except Exception as exc:                    # noqa: BLE001
        summary['error'] = f'{type(exc).__name__}: {exc}'
        summary['trace'] = traceback.format_exc()[-800:]
    finally:
        signal.alarm(0)
    summary['seconds'] = round(time.time() - started, 1)
    _write(Path(out) / 'derive' / f'{path.stem}.json', summary)
    return summary


def _models(args):
    paths = sorted(Path(args.models).glob(args.pattern))
    return paths[:args.limit] if args.limit else paths


def _pool(args):
    return concurrent.futures.ProcessPoolExecutor(max_workers=args.jobs, initializer=_init, initargs=(args.library,))


def _run(args, fn, tasks, label):
    done = 0
    with _pool(args) as pool:
        for result in pool.map(fn, tasks):
            done += 1
            if done % 25 == 0 or done == len(tasks):
                print(f'{label}: {done}/{len(tasks)}', flush=True)
            yield result


def cmd_gate(args):
    out = Path(args.out)
    tasks = [(str(m), str(out)) for m in _models(args) if args.force or not (out / 'gate' / f'{m.stem}.json').exists()]
    print(f'gating {len(tasks)} models with {args.jobs} workers', flush=True)
    for _ in _run(args, gate_one, tasks, 'gate'):
        pass
    rank(args)


def cmd_derive(args):
    out = Path(args.out)
    rows = _rows(out)
    candidates = [r for r in rows if r['gate'].get('ok')]
    candidates.sort(key=lambda r: -r['gate']['soundness'])
    tasks = [(str(Path(args.models) / r['gate']['model']), str(out), args.timeout) for r in candidates[:args.top]
             if args.force or not (out / 'derive' / f"{Path(r['gate']['model']).stem}.json").exists()]
    print(f'deriving {len(tasks)} of the top {args.top} gated models ({args.timeout}s each)', flush=True)
    for _ in _run(args, derive_one, tasks, 'derive'):
        pass
    rank(args)


def _rows(out):
    rows = []
    for gate_file in sorted((out / 'gate').glob('*.json')):
        gate = json.loads(gate_file.read_text(encoding='utf-8'))
        derive_file = out / 'derive' / gate_file.name
        derived = json.loads(derive_file.read_text(encoding='utf-8')) if derive_file.exists() else None
        rows.append(dict(gate=gate, derived=derived, score=score(gate, derived)))
    rows.sort(key=lambda r: (-r['score'], -(r['derived'] or {}).get('ok', 0), r['gate']['model']))
    return rows


def rank(args):
    out = Path(args.out)
    rows = _rows(out)
    fields = ['rank', 'score', 'model', 'title', 'parts', 'gate_ok', 'soundness', 'errors', 'components', 'derived', 'knobs',
              'template', 'note']
    table = []
    for number, row in enumerate(rows, 1):
        gate, derived = row['gate'], row['derived']
        note = (derived or {}).get('error') or '; '.join(gate.get('reasons') or [])[:160]
        table.append(dict(rank=number, score=row['score'], model=gate['model'], title=gate.get('title'), parts=gate.get('parts'),
                          gate_ok=gate.get('ok'), soundness=gate.get('soundness'), errors=gate.get('errors'),
                          components=gate.get('components'), derived='' if derived is None else derived['ok'],
                          knobs=' '.join((derived or {}).get('knobs', [])), template=(derived or {}).get('id', ''), note=note))
    out.mkdir(parents=True, exist_ok=True)
    with (out / 'ranking.csv').open('w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fields)
        writer.writeheader()
        writer.writerows(table)
    _write(out / 'ranking.json', table)
    passed = sum(1 for r in rows if r['gate'].get('ok'))
    derived = sum(1 for r in rows if (r['derived'] or {}).get('ok'))
    print(f'{len(rows)} models gated, {passed} passed the gate, {derived} derived; ranking in {out / "ranking.csv"}')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('command', choices=['gate', 'derive', 'rank'])
    parser.add_argument('--library', help='LDraw library (default: LDRAW_DIR)')
    parser.add_argument('--models', default=str(DEFAULT_MODELS))
    parser.add_argument('--out', default=str(DEFAULT_OUT))
    parser.add_argument('--jobs', type=int, default=max(1, (os.cpu_count() or 2) - 1))
    parser.add_argument('--pattern', default='*.mpd')
    parser.add_argument('--limit', type=int, default=0, help='only the first N models')
    parser.add_argument('--top', type=int, default=20, help='derive: how many of the best gated models')
    parser.add_argument('--timeout', type=int, default=2400, help='derive: seconds allowed per model')
    parser.add_argument('--force', action='store_true', help='redo models that already have a result')
    args = parser.parse_args(argv)
    {'gate': cmd_gate, 'derive': cmd_derive, 'rank': rank}[args.command](args)


if __name__ == '__main__':
    main()
