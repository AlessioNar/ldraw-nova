"""Writes ontology.schema.json (the machine-readable ontology) and params.md (parameter tables) from ontology/obd.ttl (#94).

    python docs/plans/active/build-grammar/export_schema.py            rewrite both files
    python docs/plans/active/build-grammar/export_schema.py --check    exit 1 if either file differs from what obd.ttl generates (CI)

Needs rdflib (toolkit-only). The schema keeps the shape of ldraw_tools/grammar/schema.py, including x-component and x-colour,
so the component-tree editor (#50) is unaffected. The validity-rule table still comes from grammar.CONSTRAINTS until SHACL (#93) replaces it.
"""
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
from ldraw_tools import grammar  # noqa: E402

HERE = Path(__file__).parent
_spec = importlib.util.spec_from_file_location('build_obd', ROOT / 'tools' / 'build_obd.py')
build_obd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build_obd)


def build_schema():
    """The JSON Schema as a dict, equal to grammar.SCHEMA when obd.ttl agrees with it."""
    groups, building = build_obd.load()
    props = dict(building)
    for key, comp in groups.items():
        specs = comp['properties']
        props[key] = {'type': 'object', 'additionalProperties': False, 'x-component': comp['component'],
                      'description': comp['description'], 'properties': specs,
                      'default': {k: v['default'] for k, v in specs.items()}}
    return {'$schema': 'https://json-schema.org/draft/2020-12/schema', 'title': 'Building grammar configuration',
            'type': 'object', 'additionalProperties': False, 'properties': props}


def cell(spec):
    if 'enum' in spec:
        return ' / '.join(f'`{e}`' for e in spec['enum'])
    if spec.get('type') == 'boolean':
        return 'yes / no'
    if spec.get('type') == 'integer':
        return f"{spec['minimum']}..{spec['maximum']}"
    if spec.get('type') == 'array':
        return 'any of ' + ', '.join(f'`{e}`' for e in spec['items']['enum'])
    if spec.get('x-colour'):
        return 'colour name or code'
    return spec.get('type', '')


def render(schema):
    """-> {file name: text}."""
    lines = []
    for group, spec in schema['properties'].items():
        if spec.get('type') != 'object':
            continue
        lines += [f"#### {spec['x-component']}  (`{group}`)", '', spec['description'] + '.', '',
                  '| parameter | values | default | meaning |', '|---|---|---|---|']
        for key, p in spec['properties'].items():
            default = json.dumps(p['default']).strip('"')
            lines.append(f"| `{key}` | {cell(p)} | `{default}` | {p.get('description', '')} |")
        lines.append('')
    lines += ['#### Validity rules', '', '| rule | what it says |', '|---|---|']
    lines += [f'| `{rule}` | {text} |' for rule, text in grammar.CONSTRAINTS]
    return {'ontology.schema.json': json.dumps(schema, indent=1) + '\n', 'params.md': '\n'.join(lines) + '\n'}


def main(argv):
    files = render(build_schema())
    if '--check' in argv:
        stale = [n for n, text in files.items() if (HERE / n).read_text(encoding='utf-8') != text]
        if stale:
            print(f'{", ".join(stale)} differ from ontology/obd.ttl; run: python docs/plans/active/build-grammar/export_schema.py')
        return 1 if stale else 0
    for name, text in files.items():
        (HERE / name).write_text(text, encoding='utf-8')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
