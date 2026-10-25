"""Per-colour sub-roles (issue #220): a role striped in several colours keeps one colour knob per stripe."""
import copy


from ldraw_tools import editor, generators, role_edits, role_vocab, subroles
from ldraw_tools.templates import core
from ldraw_tools.templates import derive, make


def placements(plan):
    """Leaf parts only: a submodel instance carries a role too, but its parts are what gets a colour."""
    return [(s['name'], e) for s in plan['sections'] for step in s['steps'] for e in step if not e['ref'].endswith('.ldr')]


def striped_house(a=4, b=14):
    """The house generator's plan with its walls banded course by course in two colours (red / yellow)."""
    plan = copy.deepcopy(generators.generate('house', dict(roof_colour='dark red'))['plan'])
    course = {}
    for section in plan['sections']:
        for n, step in enumerate(section['steps']):
            for entry in step:
                if entry.get('role') == 'wall' and not entry['ref'].endswith('.ldr'):
                    entry['colour'] = a if n % 2 else b
                    course[entry['id']] = entry['colour']
    return plan


def walls(plan, role=None):
    return [e for _, e in placements(plan) if e.get('role') == role or (role is None and role_vocab.canonical(e.get('role', '')) == 'wall')]


def test_vocabulary_folds_stripes_into_their_role():
    assert role_vocab.canonical('wall:stripe-a') == 'wall' and role_vocab.canonical('tower') == 'wall'
    assert role_vocab.subroles_of('wall') == ('wall:stripe-a', 'wall:stripe-b', 'wall:stripe-c')
    assert role_vocab.is_role('wall:stripe-b') and not role_vocab.is_role('wall:stripe-z')
    assert 'wall:stripe-a' not in role_vocab.ROLES        # stripes never enter the classifier's candidates


def test_split_names_stripes_in_the_order_the_colours_appear():
    split = subroles.split_stripes(striped_house())
    a, b = walls(split, 'wall:stripe-a'), walls(split, 'wall:stripe-b')
    assert a and b and len({e['colour'] for e in a}) == 1 and len({e['colour'] for e in b}) == 1
    assert a[0]['colour'] != b[0]['colour'] and not walls(split, 'wall')
    assert subroles.stripes_of(split) == {'wall': ['wall:stripe-a', 'wall:stripe-b']}


def test_a_single_colour_role_and_accents_are_not_striped():
    plain = copy.deepcopy(generators.generate('house', dict())['plan'])
    assert subroles.split_stripes(plain) == plain
    one = striped_house()
    flagged = [e for e in walls(one, 'wall')]
    for e in flagged[1:]:
        e['colour'] = flagged[0]['colour']
    flagged[0]['colour'] = 99           # a lone accent part is below MIN_PARTS
    assert subroles.stripes_of(subroles.split_stripes(one)) == {}


def test_corrected_parts_and_existing_sub_roles_are_left_alone():
    plan = striped_house()
    name, first = next((n, e) for n, e in placements(plan) if e.get('role') == 'wall')
    split = subroles.split_stripes(plan, keep={(name, first['id'])})
    assert next(e for _, e in placements(split) if e['id'] == first['id'])['role'] == 'wall'
    assert subroles.split_stripes(split) == split           # already split: idempotent


def test_recolour_stripes_in_order_and_the_role_still_means_all_of_it(official):
    plan = striped_house()
    done = role_edits.resolve(plan, official, [dict(op='recolour', role='wall stripes', colour='red and white')])
    assert done['clarify'] is None and done['ops']
    edited = editor.apply(plan, done['ops'])
    split = subroles.split_stripes(plan)
    colour = {(n, e['id']): e['colour'] for n, e in placements(edited)}
    first = {(n, e['id']) for n, e in placements(split) if e.get('role') == 'wall:stripe-a'}
    second = {(n, e['id']) for n, e in placements(split) if e.get('role') == 'wall:stripe-b'}
    assert first and second and {colour[k] for k in first} == {4} and {colour[k] for k in second} == {15}
    one = role_edits.resolve(plan, official, [dict(op='recolour', role='wall stripe b', colour='blue')])
    changed = {(n, e['id']) for n, e in placements(editor.apply(plan, one['ops'])) if e['colour'] == 1}
    assert changed == second                                   # only the second stripe moved
    everything = role_edits.resolve(plan, official, [dict(op='recolour', role='wall', colour='blue')])
    assert {(o['section'], o['id']) for o in everything['ops']} >= first | second


def test_stripes_on_a_single_colour_role_ask_instead_of_guessing(official):
    plain = generators.generate('house', dict())['plan']
    said = role_edits.resolve(plain, official, [dict(op='recolour', role='wall stripes', colour='red and white')])
    assert said['ops'] == [] and 'one colour' in said['clarify']['question']


def test_corrections_may_name_a_stripe():
    plan = striped_house()
    section, target = next((n, e['id']) for n, e in placements(plan) if e.get('role') == 'wall')
    fixed, applied = make.apply_corrections(plan, [dict(section=section, id=target, role='wall:stripe-c'),
                                                   dict(section=section, id=target, role='nonsense:x')])
    assert applied == 1 and next(e for _, e in placements(fixed) if e['id'] == target)['role'] == 'wall:stripe-c'


def test_template_has_one_colour_knob_per_stripe(official):
    plan = striped_house()
    shown = make.preview(plan, official)
    assert shown['stripes'] == {'wall': ['wall:stripe-a', 'wall:stripe-b']}
    assert {'wall:stripe-a', 'wall:stripe-b'} <= set(shown['roles']) and shown['palette']['wall:stripe-a'] != shown['palette']['wall:stripe-b']
    data, report = derive.derive(make.prepare(plan, official)[0], official, id='striped', title='Striped', families=['house'],
                                 kinds=['house'], description='striped walls', verify=False, stripes=False)
    names = [n for n in data['params'] if n.startswith('colour_wall')]
    assert names == ['colour_wall', 'colour_wall_stripe_a', 'colour_wall_stripe_b']
    assert data['params']['colour_wall_stripe_b']['x-parent'] == 'wall' and 'wall stripe B' in data['params']['colour_wall_stripe_b']['x-label']
    template = core.from_data(derive.compact(data))
    base = template.generate()['plan']
    split = template.generate({'colour_wall_stripe_b': 'blue'})['plan']
    before = {e['id']: e for _, e in placements(base)}          # a template's plan is one section, so ids are unique
    moved = {e['id'] for _, e in placements(split) if e['colour'] != before[e['id']]['colour']}
    assert moved and moved == {i for i, e in before.items() if e.get('role') == 'wall:stripe-b'}
    assert all(e['colour'] == 1 for _, e in placements(split) if e['id'] in moved)
    whole = template.generate({'colour_wall': 'green'})['plan']
    assert {e['colour'] for _, e in placements(whole) if role_vocab.canonical(e.get('role', '')) == 'wall'} == {2}
