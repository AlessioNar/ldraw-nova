"""Derive templates from models and write them as JSON.

    python derive_templates.py OUTDIR SPEC.json [--no-verify]

SPEC.json: a list of {"id", "src" (reference model name or path), "title", "family", "kind", "description"}.
"""
import argparse
import json
import sys
import time
from pathlib import Path

from ldraw_tools.common import get_parts, models_path
from ldraw_tools.templates import derive


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('outdir')
    ap.add_argument('spec')
    ap.add_argument('--no-verify', action='store_true')
    args = ap.parse_args(argv)
    parts = get_parts()
    out = Path(args.outdir)
    out.mkdir(parents=True, exist_ok=True)
    for spec in json.loads(Path(args.spec).read_text()):
        tid, src, title = spec['id'], spec['src'], spec['title']
        family, kind = spec['family'], spec['kind']
        path = Path(src) if Path(src).exists() else models_path() / src
        text = path.read_text(encoding='utf-8', errors='ignore')
        t0 = time.time()
        data, report = derive.derive_from_mpd(
            text, parts, name=path.name, id=tid, title=title, families=[family], kinds=[kind],
            description=spec.get('description', title), verify=not args.no_verify, log=lambda *a: print('  ', *a, flush=True))
        report['seconds'] = round(time.time() - t0, 1)
        (out / f'{tid}.json').write_text(json.dumps(derive.compact(data), separators=(',', ':')))
        (out / f'{tid}.report.json').write_text(json.dumps(report, indent=1, default=str))
        print(tid, report['leaves'], report['domain'], data['coverage'], report['seconds'], 's', flush=True)


if __name__ == '__main__':
    main()
