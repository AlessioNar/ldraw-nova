"""Manual plan editing: operations, inverse history, rebuild and snapping."""
import copy

import pytest
from ldraw import Parts

from ldraw_tools.builder import build_plan, load_plan
from ldraw_tools.common import ROOT, dumps
from ldraw_tools.editor import apply, inverse, placements, rebuild, snap


def plan():
    return {"version": 1, "author": "Tests", "sections": [
        {"name": "main.ldr", "description": "editable", "steps": [
            [{"id": "base", "ref": "3001.dat", "colour": 4, "at": [0, 0, 0], "purpose": "base brick"},
             {"id": "deck", "ref": "3022.dat", "colour": 14, "at": [40, 0, 0], "yaw": 90}],
            [{"id": "top", "ref": "3003.dat", "colour": 1, "on": "base"}]]}]}


@pytest.fixture
def shadow(tmp_path):
    path = tmp_path / 'shadow'
    (path / 'parts').mkdir(parents=True)
    for code, height in [('3001', 24), ('3003', 24), ('3022', 8)]:
        (path / 'parts' / f'{code}.dat').write_text(
            '0 !LDCAD SNAP_CLEAR\n'
            '0 !LDCAD SNAP_CYL [id=top] [gender=M] [secs=R 6 4] [pos=10 0 10]\n'
            f'0 !LDCAD SNAP_CYL [id=bottom] [gender=F] [secs=R 6 4] [pos=10 {height} 10]\n')
    return path


@pytest.fixture
def connected(parts, shadow):
    catalog = Parts(parts.path)
    catalog.add_connection_shadow(shadow)
    return catalog


def same(a, b):
    return dumps(a) == dumps(b)


def roundtrip(pre, edits, parts=None):
    """Apply edits, then their inverses, and expect the original plan back."""
    snapshot = copy.deepcopy(pre)
    post = apply(pre, edits, parts)
    assert same(snapshot, pre), "apply must not mutate its input"
    undone = apply(post, list(reversed(inverse(pre, edits, parts))), parts)
    assert same(undone, pre)
    return post


def test_move_roundtrip_and_placement(parts):
    post = roundtrip(plan(), [{"op": "move", "id": "deck", "position": [80, -24, 0]}], parts)
    deck = post["sections"][0]["steps"][0][1]
    assert deck["at"] == [80.0, -24.0, 0.0] and deck["yaw"] == 90 and "on" not in deck


def test_move_of_relational_entry_drops_the_relation(parts):
    post = apply(plan(), [{"op": "move", "id": "top", "position": [0, -48, 0]}], parts)
    top = post["sections"][0]["steps"][1][0]
    assert top["at"] == [0.0, -48.0, 0.0] and "on" not in top


def test_add_and_delete_roundtrip(parts):
    pre = plan()
    post = roundtrip(pre, [{"op": "add", "section": "main.ldr", "part": "3003.dat",
                           "colour": 1, "position": [0, -24, 0], "purpose": "added brick"}], parts)
    added = post["sections"][0]["steps"][-1][-1]
    assert added["id"] == "e1" and added["ref"] == "3003.dat" and added["colour"] == 1


def test_rotate_roundtrip(parts):
    post = roundtrip(plan(), [{"op": "rotate", "id": "deck", "axis": "y", "degrees": 90}], parts)
    assert post["sections"][0]["steps"][0][1]["yaw"] == 180


def test_rotate_off_yaw_axis_turns_entry_into_matrix(parts):
    post = apply(plan(), [{"op": "rotate", "id": "deck", "axis": "x", "degrees": 90}], parts)
    deck = post["sections"][0]["steps"][0][1]
    assert "yaw" not in deck and deck["matrix"] == [[0, 0, 1], [1, 0, 0], [0, 1, 0]]


def test_recolour_roundtrip(parts):
    post = roundtrip(plan(), [{"op": "recolour", "id": "deck", "colour": 0}], parts)
    assert post["sections"][0]["steps"][0][1]["colour"] == 0


def test_delete_roundtrip_with_empty_step(parts):
    post = roundtrip(plan(), [{"op": "delete", "id": "top"}], parts)
    assert len(post["sections"][0]["steps"]) == 1


def test_batch_roundtrip_applies_in_order(parts):
    edits = [{"op": "move", "id": "deck", "position": [60, 0, 0]},
             {"op": "recolour", "id": "base", "colour": 1},
             {"op": "add", "section": "main.ldr", "part": "3003.dat", "colour": 4, "position": [0, -24, 0]}]
    post = roundtrip(plan(), edits, parts)
    assert post["sections"][0]["steps"][0][1]["at"] == [60.0, 0.0, 0.0]
    assert post["sections"][0]["steps"][0][0]["colour"] == 1


def test_invalid_operations_are_rejected_with_messages(parts):
    pre = plan()
    cases = [
        ({"op": "teleport", "id": "base"}, "Unknown operation"),
        ({"op": "move", "id": "nope", "position": [0, 0, 0]}, "Unknown entry id nope"),
        ({"op": "move", "id": "deck", "position": [0, 0]}, "three finite numbers"),
        ({"op": "move", "id": "deck", "position": [2e6, 0, 0]}, "coordinate range"),
        ({"op": "rotate", "id": "deck", "axis": "q", "degrees": 90}, "axis"),
        ({"op": "rotate", "id": "deck", "axis": "y", "degrees": "half"}, "finite number"),
        ({"op": "recolour", "id": "deck", "colour": True}, "colour code"),
        ({"op": "recolour", "id": "deck", "colour": 99}, "colour 99"),
        ({"op": "add", "section": "missing.ldr", "part": "3003.dat", "colour": 4, "position": [0, 0, 0]}, "Unknown section"),
        ({"op": "add", "section": "main.ldr", "part": "9999.dat", "colour": 4, "position": [0, 0, 0]}, "Unknown part 9999.dat"),
        ({"op": "add", "section": "main.ldr", "part": "3003.dat", "colour": 99, "position": [0, 0, 0]}, "colour 99"),
        ({"op": "add", "section": "main.ldr", "part": "3003.dat", "colour": 24, "position": [0, 0, 0]}, "colour 24"),
    ]
    for edit, message in cases:
        with pytest.raises(ValueError, match=message):
            apply(pre, [edit], parts)
    assert same(pre, plan()), "rejected edits must leave the input untouched"
    solo = {"version": 1, "author": "Tests", "sections": [
        {"name": "solo.ldr", "description": "one entry", "steps": [
            [{"id": "only", "ref": "3001.dat", "colour": 4, "at": [0, 0, 0]}]]}]}
    with pytest.raises(ValueError, match="would have no steps left"):
        apply(solo, [{"op": "delete", "id": "only"}], parts)


def test_repeated_entry_is_rejected_with_a_message(parts):
    repeated = plan()
    repeated["sections"][0]["steps"][0][1]["repeat"] = {"count": 3, "step": [20, 0, 0]}
    with pytest.raises(ValueError, match="repeated placement"):
        apply(repeated, [{"op": "rotate", "id": "deck", "axis": "y", "degrees": 90}], parts)


def test_rebuild_matches_build_plan_for_examples(official):
    for name in ("shadow-snap.plan.json", "bridge.plan.json"):
        loaded = load_plan(ROOT / "examples" / name)
        assert rebuild(loaded, official) == build_plan(loaded, official)[0]


def test_rebuild_after_edit_contains_the_new_part(parts):
    post = apply(plan(), [{"op": "add", "section": "main.ldr", "part": "3003.dat",
                          "colour": 1, "position": [60, -24, 0]}], parts)
    assert "1 1 60 -24 0" in rebuild(post, parts)


def test_rebuild_failure_names_the_offending_entry(parts):
    broken = plan()
    broken["sections"][0]["steps"][0][0]["ref"] = "9999.dat"
    with pytest.raises(ValueError, match="entry base in main.ldr"):
        rebuild(broken, parts)
    painted = plan()
    painted["sections"][0]["steps"][0][1]["colour"] = 99
    with pytest.raises(ValueError, match="entry deck in main.ldr"):
        rebuild(painted, parts)


def test_placements_resolve_relational_entries(parts):
    resolved = placements(plan(), parts)
    assert resolved[("main.ldr", "top")]["at"] == [0, -24, 0]


def test_snap_locks_a_dropped_part(connected):
    pre = {"version": 1, "author": "Tests", "sections": [
        {"name": "main.ldr", "description": "snappable", "steps": [
            [{"id": "base", "ref": "3001.dat", "colour": 4, "at": [0, 0, 0]},
             {"id": "lid", "ref": "3022.dat", "colour": 14, "at": [0, -50, 0]}]]}]}
    post = roundtrip(pre, [{"op": "snap", "id": "lid"}], connected)
    lid = post["sections"][0]["steps"][0][1]
    assert lid["at"] == [0, -8, 0] and "snap" not in lid
    text = rebuild(post, connected)
    assert "1 14 0 -8 0" in text


def test_snap_target_must_be_placed_earlier(connected):
    pre = {"version": 1, "author": "Tests", "sections": [
        {"name": "main.ldr", "description": "snappable", "steps": [
            [{"id": "lid", "ref": "3022.dat", "colour": 14, "at": [0, -50, 0]},
             {"id": "base", "ref": "3001.dat", "colour": 4, "at": [0, 0, 0]}]]}]}
    with pytest.raises(ValueError, match="nothing to snap to"):
        snap(pre, "lid", connected)


def test_snap_explicit_target(connected):
    pre = {"version": 1, "author": "Tests", "sections": [
        {"name": "main.ldr", "description": "snappable", "steps": [
            [{"id": "base", "ref": "3001.dat", "colour": 4, "at": [0, 0, 0]},
             {"id": "other", "ref": "3003.dat", "colour": 4, "at": [500, 0, 0]},
             {"id": "lid", "ref": "3022.dat", "colour": 14, "at": [0, -50, 0]}]]}]}
    post = snap(pre, "lid", connected, to="base")
    assert post["sections"][0]["steps"][0][2]["at"] == [0, -8, 0]
