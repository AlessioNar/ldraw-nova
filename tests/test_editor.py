"""Manual plan editing: operations, inverse history, rebuild and snapping."""
import copy
import json
import subprocess
import sys

import pytest
from ldraw import Parts

from ldraw_tools.builder import build_plan, load_plan
from ldraw_tools.common import ROOT, dumps
from ldraw_tools.editor import apply, inverse, placements, rebuild, snap, usage


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


def test_a_batch_can_add_a_part_and_snap_it(connected):
    from ldraw_tools.editor import _next_id
    pre = {"version": 1, "author": "Tests", "sections": [
        {"name": "main.ldr", "description": "snappable", "steps": [
            [{"id": "base", "ref": "3001.dat", "colour": 4, "at": [0, 0, 0]}]]}]}
    edits = [{"op": "add", "section": "main.ldr", "part": "3022.dat", "colour": 14, "position": [0, -50, 0]},
             {"op": "snap", "id": _next_id(pre)}]
    post = roundtrip(pre, edits, connected)                # one batch, one undo: the inverse restores the plan
    added = post["sections"][0]["steps"][-1][-1]
    assert added["ref"] == "3022.dat" and added["at"] == [0, -8, 0]       # snapped, as in the snap test above
    with pytest.raises(ValueError):                        # a snap of an id nothing adds is still refused
        apply(pre, [{"op": "snap", "id": "nope"}], connected)


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


def test_snap_keeps_the_side_a_part_was_dropped_on(connected):
    """A part dropped under another snaps underneath it, one dropped on top stays on top (not the toolkit's first-ranked way)."""
    def dropped(y):
        pre = {"version": 1, "author": "Tests", "sections": [
            {"name": "main.ldr", "description": "snappable", "steps": [
                [{"id": "base", "ref": "3001.dat", "colour": 4, "at": [0, 0, 0]},
                 {"id": "lid", "ref": "3022.dat", "colour": 14, "at": [0, y, 0]}]]}]}
        return snap(pre, "lid", connected)["sections"][0]["steps"][0][1]["at"]
    assert dropped(-50) == [0, -8, 0]
    assert dropped(40) == [0, 24, 0]


def test_usage_counts_leaves_repeats_and_colour_inheritance(parts):
    nested = {"version": 1, "author": "Tests", "sections": [
        {"name": "main.ldr", "description": "d", "steps": [[
            {"id": "a", "ref": "3001.dat", "colour": 4, "at": [0, 0, 0]},
            {"id": "row", "ref": "unit.ldr", "colour": 14, "at": [60, 0, 0], "repeat": {"count": 2, "step": [40, 0, 0]}},
            {"id": "blue", "ref": "unit.ldr", "colour": 1, "at": [200, 0, 0]}]]},
        {"name": "unit.ldr", "description": "d", "steps": [[
            {"id": "leaf", "ref": "3003.dat", "colour": 16, "at": [0, 0, 0]},
            {"id": "own", "ref": "3022.dat", "colour": 0, "at": [0, 0, 0]}]]}]}
    assert usage(nested) == {("3001", 4): 1, ("3003", 14): 2, ("3003", 1): 1, ("3022", 0): 3}


def run_editor_cli(library, *arguments):
    return subprocess.run([sys.executable, "-c",
                           "from ldraw_tools.cli import main; import sys; sys.exit(main())",
                           "--library", str(library), "editor", *arguments],
                          capture_output=True, text=True, cwd=ROOT)


def small_plan():
    return {"version": 1, "author": "Tests", "sections": [
        {"name": "model.ldr", "description": "d", "steps": [
            [{"id": "base", "ref": "3001.dat", "colour": 4, "at": [0, 0, 0]}]]}]}


def editor_session(parts, tmp_path):
    library = parts.path.parent
    plan_path, state_path, mpd_path = (tmp_path / n for n in ("plan.json", "state.json", "preview.mpd"))
    source = tmp_path / "source.json"
    source.write_text(json.dumps(small_plan()))
    opened = run_editor_cli(library, "open", "--plan", plan_path, "--state", state_path, "--mpd", mpd_path,
                            "--from", source, "--name", "cli test")
    return library, plan_path, state_path, mpd_path, json.loads(opened.stdout)


def test_editor_cli_open_apply_undo_roundtrip(parts, tmp_path):
    library, plan_path, state_path, mpd_path, report = editor_session(parts, tmp_path)
    assert report["ok"] and report["revision"] == 1 and report["undo_depth"] == 0
    assert report["plan"]["sections"][0]["steps"][0][0]["id"] == "base"
    assert "3001.dat" in mpd_path.read_text()

    edits = tmp_path / "edits.json"
    edits.write_text(json.dumps([{"op": "add", "section": "model.ldr", "part": "3003.dat",
                                 "colour": 1, "position": [0, -24, 0]}]))
    applied = run_editor_cli(library, "apply", "--plan", plan_path, "--state", state_path, "--mpd", mpd_path,
                             "--base-revision", "1", "--edits", edits)
    report = json.loads(applied.stdout)
    assert applied.returncode == 0 and report["ok"] and report["revision"] == 2 and report["undo_depth"] == 1
    assert report["used"] == [["3001", 4, 1], ["3003", 1, 1]]

    stale = run_editor_cli(library, "apply", "--plan", plan_path, "--state", state_path, "--mpd", mpd_path,
                           "--base-revision", "1", "--edits", edits)
    report = json.loads(stale.stdout)
    assert stale.returncode == 1 and report["conflict"] and report["revision"] == 2

    undid = run_editor_cli(library, "undo", "--plan", plan_path, "--state", state_path, "--mpd", mpd_path)
    report = json.loads(undid.stdout)
    assert undid.returncode == 0 and report["ok"] and report["undo_depth"] == 0 and report["redo_depth"] == 1
    assert report["used"] == [["3001", 4, 1]]

    redone = run_editor_cli(library, "redo", "--plan", plan_path, "--state", state_path, "--mpd", mpd_path)
    report = json.loads(redone.stdout)
    assert redone.returncode == 0 and report["ok"] and report["undo_depth"] == 1 and report["redo_depth"] == 0


def test_editor_cli_undoes_and_redoes_a_batch_whose_edits_depend_on_each_other(parts, tmp_path):
    from ldraw_tools.editor import _next_id
    library, plan_path, state_path, mpd_path, report = editor_session(parts, tmp_path)
    new_id = _next_id(report["plan"])
    edits = tmp_path / "edits.json"
    edits.write_text(json.dumps([{"op": "add", "section": "model.ldr", "part": "3003.dat", "colour": 1, "position": [0, -24, 0]},
                                 {"op": "move", "id": new_id, "position": [40, -24, 0]}]))
    args = ("--plan", plan_path, "--state", state_path, "--mpd", mpd_path)
    applied = json.loads(run_editor_cli(library, "apply", *args, "--base-revision", "1", "--edits", edits).stdout)
    assert applied["ok"] and applied["used"] == [["3001", 4, 1], ["3003", 1, 1]]
    undid = run_editor_cli(library, "undo", *args)
    report = json.loads(undid.stdout)
    assert undid.returncode == 0 and report["ok"], report                  # was: Unknown entry id (inverses ran first-to-last)
    assert report["used"] == [["3001", 4, 1]]
    redone = json.loads(run_editor_cli(library, "redo", *args).stdout)
    assert redone["ok"] and redone["used"] == [["3001", 4, 1], ["3003", 1, 1]]


def test_editor_cli_only_mode_refuses_a_short_edit(parts, tmp_path):
    library, plan_path, state_path, mpd_path, report = editor_session(parts, tmp_path)
    assert report["ok"]
    owned = tmp_path / "owned.json"
    owned.write_text(json.dumps([["3001", 4, 1]]))          # the plan already uses the only owned brick
    edits = tmp_path / "edits.json"
    edits.write_text(json.dumps([{"op": "add", "section": "model.ldr", "part": "3003.dat",
                                 "colour": 1, "position": [0, -24, 0]}]))
    refused = run_editor_cli(library, "apply", "--plan", plan_path, "--state", state_path, "--mpd", mpd_path,
                             "--base-revision", "1", "--mode", "only", "--owned", owned, "--edits", edits)
    report = json.loads(refused.stdout)
    assert refused.returncode == 1 and not report["ok"]
    assert report["short"] == [["3003", 1, 1, 0]]
    assert len(json.loads(plan_path.read_text())["sections"][0]["steps"][0]) == 1  # nothing was written
    assert json.loads(state_path.read_text())["revision"] == 1

    allowed = run_editor_cli(library, "apply", "--plan", plan_path, "--state", state_path, "--mpd", mpd_path,
                             "--base-revision", "1", "--mode", "prefer", "--owned", owned, "--edits", edits)
    report = json.loads(allowed.stdout)
    assert allowed.returncode == 0 and report["ok"] and report["short"] == [["3003", 1, 1, 0]]


def test_editor_cli_failed_rebuild_leaves_files_untouched(parts, tmp_path):
    library, plan_path, state_path, mpd_path, report = editor_session(parts, tmp_path)
    assert report["ok"]
    before = (plan_path.read_text(), state_path.read_text(), mpd_path.read_text())
    edits = tmp_path / "edits.json"
    edits.write_text(json.dumps([{"op": "add", "section": "model.ldr", "part": "3001.dat",
                                 "colour": 4, "position": [0, 0, 0]}]))     # same transform twice fails validation
    failed = run_editor_cli(library, "apply", "--plan", plan_path, "--state", state_path, "--mpd", mpd_path,
                            "--base-revision", "1", "--edits", edits)
    report = json.loads(failed.stdout)
    assert failed.returncode == 2 and report.get("error") and "same world transform" in report["error"]
    assert (plan_path.read_text(), state_path.read_text(), mpd_path.read_text()) == before


# ---------------------------------------------------------------- MPD import

from ldraw_tools.common import ROOT as _ROOT                    # noqa: E402
from ldraw_tools.editor import import_mpd                        # noqa: E402
from ldraw_tools.document import parse_source                  # noqa: E402
from ldraw_tools.inventory import model_bom                      # noqa: E402

GENERATED = _ROOT / "app" / "data" / "generated"


def _placed(text):
    """[(section, ref, colour, position, matrix)] for every type-1 line of an MPD, in file order."""
    result, section = [], None
    for line in text.splitlines():
        words = line.split()
        if words[:2] == ["0", "FILE"]:
            section = words[2].lower()
        elif words[:1] == ["1"]:
            numbers = [float(v) for v in words[2:14]]
            result.append((section, words[14].lower(), int(words[1]), numbers[:3], numbers[3:]))
    return result


def _same_placements(a, b):
    assert len(a) == len(b)
    for (s1, r1, c1, p1, m1), (s2, r2, c2, p2, m2) in zip(a, b):
        assert (s1, r1, c1) == (s2, r2, c2)
        assert p1 == pytest.approx(p2, abs=1e-6) and m1 == pytest.approx(m2, abs=1e-6)


SUBMODEL_MPD = (
    "0 FILE main.ldr\n0 Two towers\n0 Author: Tester\n"
    "0 // left: the left tower\n1 4 0 0 0 1 0 0 0 1 0 0 0 1 tower.ldr\n0 STEP\n"
    "1 1 80 0 0 0 0 -1 0 1 0 1 0 0 tower.ldr\n"
    "0 FILE tower.ldr\n0 A tower\n1 14 0 0 0 1 0 0 0 1 0 0 0 1 3001.dat\n0 STEP\n"
    "1 16 0 -24 0 1 0 0 0 1 0 0 0 1 3003.dat\n"
    "2 24 0 0 0 10 0 0\n1 4 0 -48 0 1 0 0 0 1 0 0 0 1 9999.dat\n")


def test_import_keeps_submodels_steps_and_reports_what_it_skips(parts):
    plan, warnings = import_mpd(SUBMODEL_MPD, parts)
    assert [s["name"] for s in plan["sections"]] == ["main.ldr", "tower.ldr"]
    assert [len(step) for step in plan["sections"][0]["steps"]] == [1, 1]
    left = plan["sections"][0]["steps"][0][0]
    assert left["id"] == "left" and left["ref"] == "tower.ldr" and left["purpose"] == "the left tower"
    assert plan["sections"][0]["steps"][1][0]["matrix"] == [[0, 0, -1], [0, 1, 0], [1, 0, 0]]
    assert len(warnings) == 2 and any("type 2" in w for w in warnings) and any("9999.dat" in w for w in warnings)
    rebuilt = rebuild(plan, parts)
    assert usage(plan, parts) == {("3001", 14): 2, ("3003", 4): 1, ("3003", 1): 1}
    _same_placements(_placed(rebuilt), [p for p in _placed(SUBMODEL_MPD) if p[1] != "9999.dat"])
    # the imported plan is editable like any other
    moved = apply(plan, [{"op": "move", "id": "left", "position": [20, 0, 0]}], parts)
    assert "1 4 20 0 0" in rebuild(moved, parts)


def test_import_names_and_errors(parts):
    plan, _ = import_mpd("1 4 0 0 0 1 0 0 0 1 0 0 0 1 3001.dat\n", parts)           # a bare LDR with no FILE line
    assert plan["sections"][0]["name"] == "model.ldr"
    plan, _ = import_mpd("0 FILE Cool Model.mpd\n1 4 0 0 0 1 0 0 0 1 0 0 0 1 3001.dat\n", parts)
    assert plan["sections"][0]["name"] == "Cool Model.ldr"
    with pytest.raises(ValueError):
        import_mpd("0 FILE a.ldr\n4 16 0 0 0 1 0 0 1 1 0 1 0 1 0\n", parts)


@pytest.mark.parametrize("path", sorted(GENERATED.glob("*.mpd")), ids=lambda p: p.stem)
def test_import_roundtrips_generated_models(official, path, tmp_path):
    text = path.read_text(encoding="utf-8")
    plan, warnings = import_mpd(text, official)
    assert warnings == []
    rebuilt = rebuild(plan, official)
    _same_placements(_placed(rebuilt), _placed(text))
    again = tmp_path / "again.mpd"
    again.write_text(rebuilt, encoding="utf-8")
    assert model_bom(parse_source(again), official) == model_bom(parse_source(path), official)
