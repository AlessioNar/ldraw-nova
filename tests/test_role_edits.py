"""Role- and selection-based edit primitives: ops the existing editor applies."""
import json

import pytest

from ldraw_tools import editor, generators, role_edits


@pytest.fixture(scope='module')
def house():
    return generators.generate('house', dict(roof_colour='dark red', chimney='left', porch=True))['plan']


def leaves(plan, role=None):
    sections = {s['name'].casefold() for s in plan['sections']}
    return [(s['name'], p) for s in plan['sections'] for step in s['steps'] for p in step
            if p['ref'].casefold() not in sections and (role is None or p.get('role') == role)]


def test_recolour_role_changes_only_that_role(house):
    ops = role_edits.recolour_role(house, 'roof', 'blue')
    assert ops and all(o['op'] == 'recolour' and o['colour'] == 1 for o in ops)
    edited = editor.apply(house, ops)
    for _, p in leaves(edited, 'roof'):
        assert p['colour'] == 1
    before = {(n, p['id']): p['colour'] for n, p in leaves(house) if p.get('role') != 'roof'}
    after = {(n, p['id']): p['colour'] for n, p in leaves(edited) if p.get('role') != 'roof'}
    assert before == after
    assert role_edits.role_word('roofs') == 'roof' and role_edits.role_word('windows') == 'window'


def test_recolouring_windows_keeps_glass_transparent(house):
    edited = editor.apply(house, role_edits.recolour_role(house, 'window', 'blue'))
    assert 33 in {p['colour'] for _, p in leaves(edited, 'window')}      # the panes became trans blue, not opaque blue


def test_remove_role_deletes_the_parts_and_nothing_else(house):
    ops = role_edits.remove_role(house, 'chimney')
    assert ops and all(o['op'] == 'delete' for o in ops)
    edited = editor.apply(house, ops)
    assert not leaves(edited, 'chimney')
    assert len(leaves(edited)) == len(leaves(house)) - len(ops)


def test_remove_role_refuses_what_other_placements_depend_on(house):
    ops = role_edits.remove_role(house, 'wall')       # the shell carries the roof's attach point
    assert editor.apply(house, ops)                    # whatever is left is still a valid plan


def test_selection_scoped_edits(house):
    roofs = [dict(section=n, id=p['id']) for n, p in leaves(house, 'roof')][:3]
    ids = {r['id'] for r in roofs}
    ops = role_edits.recolour_selection(house, roofs, 'green')
    assert {o['id'] for o in ops} == ids
    scoped = role_edits.recolour_role(house, 'roof', 'green', selection=roofs)
    assert {o['id'] for o in scoped} == ids
    other = [dict(section=n, id=p['id']) for n, p in leaves(house, 'wall')][:2]
    assert not role_edits.recolour_role(house, 'roof', 'green', selection=other)
    assert role_edits.roles_of_selection(house, roofs) == ['roof']
    like = role_edits.recolour_like_selection(house, roofs[:1], 'green')
    assert len(like) >= len(ops) and like.notes[0] == 'role of the selection: roof'
    assert editor.apply(house, role_edits.remove_selection(house, roofs))


def test_missing_role_explains_itself(house):
    ops = role_edits.recolour_role(house, 'wing', 'red')
    assert not ops and 'roles present' in ops.notes[0]


def test_works_on_a_plan_without_generators(house):
    """A plan with only the optional role field (as classifier output) is enough; no generator source needed."""
    plan = json.loads(json.dumps(house))
    plan['author'] = 'someone else'
    assert role_edits.recolour_role(plan, 'door', 'dark green')
    summary = role_edits.role_summary(plan)
    assert summary['roof']['count'] > 0 and 'colours' in summary['roof']


def test_removal_of_a_whole_submodel_removes_its_instances():
    plan = generators.generate('house', dict(window_sides=['front'], windows=2, door_side='none'))['plan']
    ops = role_edits.remove_role(plan, 'window')
    assert ops
    edited = editor.apply(plan, ops)
    names = {s['name'] for s in edited['sections']}
    assert not [p for s in edited['sections'] for step in s['steps'] for p in step if p['ref'].startswith('window4')]
    assert 'house-shell.ldr' in names
