"""Writes ontology.schema.json (the machine-readable ontology) and params.md (parameter tables) from the code."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from ldraw_tools import grammar  # noqa: E402

HERE = Path(__file__).parent


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


def main():
    schema = grammar.SCHEMA
    (HERE / 'ontology.schema.json').write_text(json.dumps(schema, indent=1) + '\n', encoding='utf-8')
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
    (HERE / 'params.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
