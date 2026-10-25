"""Make this a template: the quality gate, the role preview and corrections, the job (docs/plans/active/build-templates, section 6)."""

import copy
import json

import pytest

from ldraw_tools import templates
from ldraw_tools.templates import job, make


def parts_of(plan):
    return [p for s in plan['sections'] for step in s['steps'] for p in step]


@pytest.fixture(scope='module')
def truck():
    """The flatbed truck template's default plan with its roles removed, as a person's own build would arrive."""
    plan = copy.deepcopy(templates.get('flatbed-truck').generate()['plan'])
    for entry in parts_of(plan):
        entry.pop('role', None)
    return plan


class Pipeline:
    """The worker's pipeline, as far as the template job uses it."""
    def __init__(self, parts):
        self.parts = parts

    def load(self):
        return self.parts

    def run(self, outdir, **_):
        raise RuntimeError('no renderer in tests')


def test_gate_accepts_a_sound_build(official, truck):
    gate = make.gate(truck, official)
    assert gate['ok'] and gate['reasons'] == [] and gate['errors'] == 0 and gate['parts'] == 59


def test_gate_refuses_a_build_that_is_too_small_or_broken(official, truck):
    tiny = {'version': 1, 'sections': [{'name': 'a.ldr', 'description': 'a', 'steps': [parts_of(truck)[:3]]}]}
    gate = make.gate(tiny, official)
    assert not gate['ok'] and 'too little' in gate['reasons'][0]
    broken = copy.deepcopy(truck)
    parts_of(broken)[0]['ref'] = 'no-such-part.dat'
    gate = make.gate(broken, official)
    assert not gate['ok'] and gate['reasons']
    scattered = copy.deepcopy(truck)
    for i, entry in enumerate(parts_of(scattered)):
        entry['at'] = [i * 400, 0, 0]
    gate = make.gate(scattered, official)
    assert not gate['ok'] and any('loose groups' in r for r in gate['reasons'])


def test_preview_colours_every_part_by_role_and_lists_the_unsure(official, truck):
    shown = make.preview(truck, official, hints='flatbed truck')
    assert sum(r['count'] for r in shown['roles'].values()) == 59
    assert set(shown['palette']) == set(shown['roles']) and shown['domain'] == 'vehicle'
    assert shown['mpd'].startswith('0 FILE') or '1 ' in shown['mpd']
    assert 'untagged' not in shown['roles']


def test_a_correction_sticks_and_the_classifier_never_overwrites_it(official, truck):
    first = parts_of(truck)[0]
    key = (truck['sections'][0]['name'], first['id'])
    shown = make.preview(truck, official, hints='flatbed truck')
    before = shown['roles']
    fixed, applied = make.apply_corrections(truck, [{'section': key[0], 'id': key[1], 'role': 'wing'},
                                                    {'section': key[0], 'id': key[1], 'role': 'not-a-role'},
                                                    {'section': 'nowhere', 'id': 'x', 'role': 'roof'}])
    assert applied == 1 and parts_of(fixed)[0]['role'] == 'wing' and 'role' not in first
    after = make.preview(truck, official, hints='flatbed truck', corrections=[{'section': key[0], 'id': key[1], 'role': 'wing'}])
    assert after['corrected'] == 1 and after['roles']['wing']['count'] == before.get('wing', {'count': 0})['count'] + 1
    tagged, _ = make.prepare(fixed, official, hints='truck')
    assert parts_of(tagged)[0]['role'] == 'wing'


def test_make_describes_the_knobs_and_never_offers_what_it_rejected(official, truck):
    steps = []
    data, report, knobs, rejected = make.make(truck, official, id='my-truck', title='My truck', kinds=['truck'],
                                               families=['vehicle'], source={'name': 'My truck', 'author': 'Me'},
                                               hints='truck', progress=lambda *a: steps.append(a))
    assert [s[0] for s in steps][:2] == ['roles', 'baseline'] and any(s[0] == 'checked' for s in steps)
    assert data['id'] == 'my-truck' and data['source']['author'] == 'Me' and 'leaves' in data
    params = {k for k in data['params'] if not k.startswith('colour_')}
    assert {k['param'] for k in knobs} == params and all(k['extremes'] for k in knobs)
    assert not {r['param'] for r in rejected} & params
    template = templates.from_data(data)
    assert template.generate()['plan']['sections'][0]['name'] == 'my-truck.ldr'
    for knob in knobs:
        for extreme in knob['extremes']:
            plan = make.variant_plan(data, knob['param'], extreme['value'])
            assert plan['sections'][0]['steps']


def test_switching_a_knob_off_removes_it_and_nothing_can_be_added(official, truck):
    data, _, knobs, _ = make.make(truck, official, id='t', title='T', kinds=['truck'], families=['vehicle'], hints='truck')
    names = [k['param'] for k in knobs]
    fewer = make.without_knobs(data, [names[0], 'colour_body', 'include_nonsense', 'x_delta_not_there'])
    assert names[0] not in fewer['params'] and 'colour_body' in fewer['params']
    assert set(fewer['params']) == set(data['params']) - {names[0]}
    templates.from_data(fewer).generate()
    assert set(make.without_knobs(fewer, [])['params']) == set(fewer['params'])


def test_job_makes_and_finalizes(official, truck, tmp_path):
    jobdir = tmp_path / 'job'
    reply = job.run_request(dict(action='make', plan=truck, jobdir=str(jobdir), title='Red flatbed', kinds=['flatbed'],
                                 families=['vehicle'], hints='truck', source={'name': 'Red flatbed', 'build': 'red-flatbed', 'version': 2}),
                            Pipeline(official))
    assert reply['ok']
    assert json.loads((jobdir / 'progress.json').read_text())['step'] == 'done'
    result = json.loads((jobdir / 'result.json').read_text())
    assert result['template']['source']['version'] == 2 and result['knobs']
    off = result['knobs'][0]['param']
    done = job.run_request(dict(action='finalize', jobdir=str(jobdir), disabled=[off], title='Red flatbed truck',
                                kinds=['Flatbed', 'red truck'], taken=['red-flatbed-truck']), Pipeline(official))
    stored = done['template']
    assert stored['id'] == 'red-flatbed-truck-2' and stored['title'] == 'Red flatbed truck'
    assert stored['kinds'] == ['flatbed', 'red truck'] and off not in stored['params']
    assert templates.from_data(stored).generate()['plan']


def test_the_persons_templates_folder_is_loaded_and_refreshed(official, truck, tmp_path, monkeypatch):
    data, *_ = make.make(truck, official, id='folder-truck', title='Folder truck', kinds=['folder truck'], families=['vehicle'],
                         hints='truck')
    monkeypatch.setenv(templates.USER_DIR_ENV, str(tmp_path))
    try:
        assert templates.refresh() == []
        (tmp_path / 'folder-truck.json').write_text(json.dumps(data))
        (tmp_path / 'broken.json').write_text('{not json')
        assert templates.refresh() == ['folder-truck']
        assert templates.get('folder-truck').title == 'Folder truck'
        assert templates.find(None, 'folder truck')[0].id == 'folder-truck'
        (tmp_path / 'folder-truck.json').unlink()
        assert templates.refresh() == [] and 'folder-truck' not in templates.REGISTRY
    finally:
        monkeypatch.delenv(templates.USER_DIR_ENV)
        templates.refresh()
