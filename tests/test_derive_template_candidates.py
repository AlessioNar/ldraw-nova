"""tools/derive_template_candidates.py: the offline bulk gate-and-derive over the reference models (issue #56)."""

import importlib.util
import json
from pathlib import Path

import pytest

from ldraw_tools import templates
from ldraw_tools.builder import build_plan

TOOL = Path(__file__).resolve().parents[1] / "tools" / "derive_template_candidates.py"


@pytest.fixture(scope="module")
def bulk():
    spec = importlib.util.spec_from_file_location("derive_template_candidates", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def gate(**kw):
    return dict(ok=True, parts=100, components=5, **kw)


def test_soundness_is_zero_for_a_refused_build_and_falls_with_loose_groups(bulk):
    assert bulk.soundness(dict(ok=False, parts=100, components=1)) == 0.0
    tight, loose = bulk.soundness(gate()), bulk.soundness(dict(ok=True, parts=100, components=20))
    assert 1 > tight > loose > 0


def test_score_rewards_verified_knobs_and_zeroes_an_unclean_baseline(bulk):
    base = bulk.soundness(gate())
    assert bulk.score(gate()) == base
    assert bulk.score(gate(), dict(ok=False)) == base
    assert bulk.score(gate(), dict(ok=True, knobs=['x_delta'], baseline_errors=0)) > base
    many = bulk.score(gate(), dict(ok=True, knobs=list('abcdefgh'), baseline_errors=0))
    assert many == bulk.score(gate(), dict(ok=True, knobs=list('abcd'), baseline_errors=0))
    assert bulk.score(gate(), dict(ok=True, knobs=['x_delta'], baseline_errors=2)) == 0.0


def test_rank_orders_models_best_first_and_writes_csv_and_json(bulk, tmp_path):
    for name, g, d in (('a', gate(model='a.mpd', soundness=0.4), None),
                       ('b', gate(model='b.mpd', soundness=0.9), dict(ok=True, id='b', knobs=['x_delta'], baseline_errors=0)),
                       ('c', dict(ok=False, model='c.mpd', parts=3, components=0, reasons=['too little']), None)):
        (tmp_path / 'gate').mkdir(exist_ok=True)
        (tmp_path / 'gate' / f'{name}.json').write_text(json.dumps(g))
        if d:
            (tmp_path / 'derive').mkdir(exist_ok=True)
            (tmp_path / 'derive' / f'{name}.json').write_text(json.dumps(d))
    bulk.rank(type('A', (), {'out': str(tmp_path)}))
    table = json.loads((tmp_path / 'ranking.json').read_text())
    assert [r['model'] for r in table] == ['b.mpd', 'a.mpd', 'c.mpd']
    assert table[0]['template'] == 'b' and table[2]['note'] == 'too little'
    assert (tmp_path / 'ranking.csv').read_text().startswith('rank,score,model')


def test_gate_one_writes_a_verdict_for_a_sound_model(bulk, official, tmp_path):
    plan = templates.get('flatbed-truck').generate()['plan']
    model = tmp_path / '0001-1.mpd'
    model.write_text(build_plan(plan, official)[0], encoding='utf-8')
    bulk._parts = official
    result = bulk.gate_one((str(model), str(tmp_path / 'out')))
    assert result['ok'] and result['soundness'] > 0 and result['parts'] == 59
    assert json.loads((tmp_path / 'out' / 'gate' / '0001-1.json').read_text())['model'] == '0001-1.mpd'
    model.write_text('not a model', encoding='utf-8')
    assert not bulk.gate_one((str(model), str(tmp_path / 'out')))['ok']
