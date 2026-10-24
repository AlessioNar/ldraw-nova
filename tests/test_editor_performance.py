"""Editor performance work: the same answers as before, found with far less work.

Snapping used to build the whole plan and check every candidate pose against the whole model; it now inspects
what is near the snap. The reference below is the old algorithm (copied from before the change, with the new
shortcuts switched off); the tests insist both give identical placements, errors included.
"""
import copy
import json
import random
from pathlib import Path

import jsonschema
import numpy as np
import pytest

from ldraw_tools import builder, connectivity, editor, fastbounds, incremental
from ldraw_tools.builder import build_plan, check_schema
from ldraw_tools.commands import editor as editor_command
from ldraw_tools.common import jsonable, normalized
from ldraw_tools.editor import apply, import_mpd, rebuild

ROOT = Path(__file__).resolve().parents[1]
GENERATED = ROOT / "app" / "data" / "generated"


# ---------------------------------------------------------------- the old algorithm, as the reference

def _old_snap_target(plan, section, si, ti, ei, edit, parts):
    candidates = editor._earlier_entries(section, ti, ei)
    repeated = [e for e in candidates if "repeat" in e]
    wanted = edit.get("to")
    if wanted is not None:
        match = next((e for e in candidates if e["id"] == wanted), None)
        if match is None:
            editor._fail(f"snap target {wanted} must be an entry placed before it in {section['name']}")
        if match in repeated:
            editor._fail(f"snap target {wanted} is a repeated placement")
        return match
    resolved = editor.placements(plan, parts)
    origin = editor._vector(section["steps"][ti][ei]["at"], "position")

    def distance(entry):
        at = resolved.get((section["name"], entry["id"]), {}).get("at", entry.get("at"))
        if at is None:
            return float("inf")
        return sum((a - b) ** 2 for a, b in zip(at, origin))
    singles = [e for e in candidates if "repeat" not in e]
    if not singles:
        editor._fail(f"entry {section['steps'][ti][ei]['id']} has nothing to snap to in {section['name']}")
    return min(singles, key=distance)


def _old_apply_snap(plan, edit, parts):
    si, ti, ei = editor._locate(plan, edit)
    section = plan["sections"][si]
    entry = section["steps"][ti][ei]
    if "repeat" in entry:
        editor._fail(f"entry {entry['id']} is a repeated placement; snap its template in the plan source")
    if "at" not in entry:
        editor._fail(f"entry {entry['id']} has no explicit position; only dropped parts can snap")
    target = _old_snap_target(plan, section, si, ti, ei, edit, parts)
    snapped = {"id": entry["id"], "ref": entry["ref"], "colour": entry["colour"],
               "snap": {"to": target["id"], "near": copy.deepcopy(entry["at"]), "nearest": True}}
    for key in ("purpose", "role", "yaw", "matrix"):
        if key in entry:
            snapped[key] = copy.deepcopy(entry[key])
    probe = copy.deepcopy(plan)
    probe["sections"][si]["steps"][ti][ei] = snapped
    try:
        _, parsed, _ = build_plan(probe, parts)
    except ValueError as exc:
        editor._fail(f"snap {entry['id']}: {exc}")
    from ldraw_tools.document import section_table
    table = section_table(parsed)
    resolved = {i: (at, matrix) for i, at, matrix in editor._model_placements(table.get(normalized(section["name"])))}
    if entry["id"] not in resolved:
        editor._fail(f"snap {entry['id']}: the build did not place it")
    at, matrix = resolved[entry["id"]]
    baked = {"id": entry["id"], "ref": entry["ref"], "colour": entry["colour"], "at": at}
    if "purpose" in entry:
        baked["purpose"] = entry["purpose"]
    if "role" in entry:
        baked["role"] = entry["role"]
    if not np.allclose(matrix, np.eye(3), atol=1e-9):
        baked["matrix"] = matrix
    section["steps"][ti][ei] = baked


@pytest.fixture
def old_snapping(monkeypatch):
    monkeypatch.setattr(builder, "CULL_SNAP_PREVIEW", False)
    monkeypatch.setattr(connectivity, "LOCAL_SNAP_CHECKS", False)
    monkeypatch.setattr(editor, "_apply_snap", _old_apply_snap)
    fastbounds.uninstall()
    yield
    fastbounds.install()


def outcome(plan, edits, parts):
    try:
        return apply(plan, edits, parts)
    except ValueError as exc:
        return f"refused: {exc}"


def both_ways(plan, edits, parts, monkeypatch):
    """The result of the edits with the new snapping, then with the old: they must be the same."""
    new = outcome(plan, edits, parts)
    with monkeypatch.context() as m:
        m.setattr(builder, "CULL_SNAP_PREVIEW", False)
        m.setattr(connectivity, "LOCAL_SNAP_CHECKS", False)
        m.setattr(editor, "_apply_snap", _old_apply_snap)
        fastbounds.uninstall()
        try:
            old = outcome(plan, edits, parts)
        finally:
            fastbounds.install()
    return new, old


# ---------------------------------------------------------------- snapping: identical to the old algorithm

@pytest.fixture
def shadow(tmp_path):
    path = tmp_path / "shadow"
    (path / "parts").mkdir(parents=True)
    for code, height in [("3001", 24), ("3003", 24), ("3022", 8)]:
        (path / "parts" / f"{code}.dat").write_text(
            "0 !LDCAD SNAP_CLEAR\n"
            "0 !LDCAD SNAP_CYL [id=top] [gender=M] [secs=R 6 4] [pos=10 0 10]\n"
            f"0 !LDCAD SNAP_CYL [id=bottom] [gender=F] [secs=R 6 4] [pos=10 {height} 10]\n")
    return path


@pytest.fixture
def connected(parts, shadow):
    from ldraw import Parts
    catalog = Parts(parts.path)
    catalog.add_connection_shadow(shadow)
    return catalog


def grid_plan():
    """A few separate stacks, so some snaps have neighbours around them and some are far from everything."""
    entries = []
    for i, (x, z) in enumerate([(0, 0), (40, 0), (400, 400), (-400, 0), (0, 800), (800, 800)]):
        entries.append({"id": f"b{i}", "ref": "3001.dat", "colour": 4, "at": [x, 0, z]})
        entries.append({"id": f"p{i}", "ref": "3022.dat", "colour": 14, "at": [x, -24, z]})
    return {"version": 1, "author": "Tests", "sections": [
        {"name": "main.ldr", "description": "grid", "steps": [entries[:6], entries[6:]]}]}


def test_snaps_match_the_old_algorithm_on_the_synthetic_parts(connected, monkeypatch):
    plan = grid_plan()
    ids = [e["id"] for step in plan["sections"][0]["steps"] for e in step]
    rng = random.Random(7)
    checked = placed = 0
    for _ in range(24):
        target = rng.choice(ids)
        at = next(e["at"] for step in plan["sections"][0]["steps"] for e in step if e["id"] == target)
        position = [at[0] + rng.choice([0, 10, -10, 20]), at[1] + rng.choice([-24, -8, 8, 24, -50]),
                    at[2] + rng.choice([0, 10, -20])]
        add = {"op": "add", "section": "main.ldr", "part": rng.choice(["3001.dat", "3003.dat", "3022.dat"]),
               "colour": 1, "position": position}
        if rng.random() < 0.4:
            add["rotation"] = rng.choice([90, 180])
        edits = [add, {"op": "snap", "id": editor._next_id(plan), **({"to": target} if rng.random() < 0.6 else {})}]
        new, old = both_ways(plan, edits, connected, monkeypatch)
        assert new == old
        checked += 1
        placed += not isinstance(new, str)
    assert placed >= 8, f"only {placed} of {checked} snaps found a place; the comparison needs real placements"


def _sections_with_leaves(plan, low, high):
    leaves = lambda s: sum(1 for step in s["steps"] for e in step if not e["ref"].endswith(".ldr"))
    return [s for s in plan["sections"] if low <= leaves(s) <= high]


@pytest.mark.parametrize("model", ["willowbank-cottage-v1.mpd"])
def test_snaps_match_the_old_algorithm_on_a_real_model(official, model, monkeypatch):
    """Random add-and-snap operations on a 1,100-part model, one section at a time, new against old."""
    path = GENERATED / model
    if not path.is_file():
        pytest.skip(f"{model} is not in the checkout")
    plan, _ = import_mpd(path.read_text(encoding="utf-8"), official)
    sections = _sections_with_leaves(plan, 20, 60)       # the old algorithm needs minutes for one snap in a dense section
    assert sections, "the model should have a mid-sized section"
    rng = random.Random(11)
    placed = 0
    for section in rng.sample(sections, min(2, len(sections))):
        entries = [e for step in section["steps"] for e in step if "at" in e and not e["ref"].endswith(".ldr")]
        top = sorted(entries, key=lambda e: e["at"][1])[:max(4, len(entries) // 4)]      # the highest parts: free tops
        for _ in range(2):
            target = rng.choice(top)
            x, y, z = target["at"]
            add = {"op": "add", "section": section["name"], "part": rng.choice(["3001.dat", "3003.dat", "3022.dat"]),
                   "colour": rng.choice([4, 14]), "position": [x + rng.choice([0, 10]), y - 24, z + rng.choice([0, 10])]}
            edits = [add, {"op": "snap", "id": editor._next_id(plan), **({"to": target["id"]} if rng.random() < 0.5 else {})}]
            new, old = both_ways(plan, edits, official, monkeypatch)
            assert new == old
            placed += not isinstance(new, str)
    assert placed >= 1, "at least one snap should find a place or the comparison proves little"


def test_an_unsnapped_target_error_is_the_same_in_both(connected, monkeypatch):
    plan = grid_plan()
    edits = [{"op": "snap", "id": "b0", "to": "p5"}]       # p5 is placed after b0
    new, old = both_ways(plan, edits, connected, monkeypatch)
    assert new == old and new.startswith("refused")


# ---------------------------------------------------------------- the snap neighbourhood

def test_the_neighbourhood_keeps_what_overlaps_and_drops_the_rest(parts):
    from ldraw_tools.snapindex import Neighbourhood
    plan = grid_plan()
    from ldraw import Piece, Vector
    from ldraw.lines import Comment
    models = {}
    from ldraw import Model
    model = models["main.ldr"] = Model(name="main.ldr")
    pieces = {}
    for step in plan["sections"][0]["steps"]:
        for entry in step:
            piece = Piece.place(entry["ref"], colour=entry["colour"], position=Vector(*entry["at"]))
            model.add(Comment(f"// {entry['id']}"))
            model.add(piece)
            pieces[entry["id"]] = piece
    hood = Neighbourhood(models, parts)
    moving = Piece.place("3001.dat", colour=4, position=Vector(0, -48, 0))
    kept, extra = hood.cull(model.objects, pieces["b0"], moving)
    kept_pieces = {o for o in kept if isinstance(o, Piece)}
    assert pieces["b0"] in kept_pieces and pieces["p0"] in kept_pieces and pieces["b1"] in kept_pieces
    assert pieces["b3"] not in kept_pieces and pieces["b5"] not in kept_pieces and not extra


def test_submodel_instances_are_culled_inside_but_never_the_support(connected):
    from ldraw_tools.snapindex import Neighbourhood
    from ldraw import Model, Piece, Vector
    inner = Model(name="wall.ldr")
    for i in range(10):
        inner.add(Piece.place("3001.dat", colour=4, position=Vector(i * 80, 0, 0)))
    root = Model(name="main.ldr")
    near = Piece.place("wall.ldr", colour=4, position=Vector(0, 0, 0))
    far = Piece.place("wall.ldr", colour=4, position=Vector(0, 0, 4000))
    support = Piece.place("3001.dat", colour=4, position=Vector(0, 0, 100))
    for piece in (near, far, support):
        root.add(piece)
    hood = Neighbourhood({"main.ldr": root, "wall.ldr": inner}, connected)
    moving = Piece.place("3001.dat", colour=4, position=Vector(0, -24, 100))
    kept, extra = hood.cull(root.objects, support, moving)
    placed = [o for o in kept if isinstance(o, Piece)]
    assert support in placed and far not in placed and len(placed) == 2      # the far instance is dropped whole
    assert near not in placed                                       # the near one is replaced by a stand-in ...
    [stand_in] = extra.values()
    assert 0 < len([o for o in stand_in.objects if isinstance(o, Piece)]) < 10     # ... that keeps only its nearby end


# ---------------------------------------------------------------- schema checks by content

def test_check_schema_keeps_rejecting_what_the_schema_rejects(parts):
    plan = grid_plan()
    check_schema(plan)
    check_schema(plan)                                              # remembered
    for mutate in (lambda p: p["sections"][0]["steps"][0][0].pop("at"),
                   lambda p: p["sections"][0]["steps"][0][0].__setitem__("colour", -1),
                   lambda p: p["sections"][0]["steps"][0][0].__setitem__("ref", "not a part"),
                   lambda p: p["sections"][0]["steps"][0][0].update(on="p0"),
                   lambda p: p["sections"][0]["steps"].clear(),
                   lambda p: p["sections"][0].__setitem__("name", "bad name"),
                   lambda p: p.__setitem__("version", 2)):
        broken = copy.deepcopy(plan)
        mutate(broken)
        with pytest.raises(jsonschema.ValidationError):
            check_schema(broken)
    check_schema(plan)                                              # still fine after the failures


def test_check_schema_errors_are_the_whole_schemas_first_error(parts):
    broken = grid_plan()
    broken["sections"][0]["steps"][0][1]["colour"] = -5
    with pytest.raises(jsonschema.ValidationError) as ours:
        check_schema(broken)
    full = builder._validators()[0]
    with pytest.raises(jsonschema.ValidationError) as theirs:
        full.validate(broken)
    assert ours.value.message == theirs.value.message


# ---------------------------------------------------------------- incremental rebuild

@pytest.fixture(autouse=True)
def fresh_bases():
    incremental.forget()
    yield
    incremental.forget()


def stack_plan(count=12):
    entries = [{"id": f"s{i}", "ref": "3001.dat" if i % 2 == 0 else "3022.dat", "colour": 4,
                "at": [i * 100, 0, 0]} for i in range(count)]
    return {"version": 1, "author": "Tests", "sections": [
        {"name": "main.ldr", "description": "row", "steps": [entries[:6], entries[6:]]},
        {"name": "tower.ldr", "description": "tower", "steps": [[
            {"id": "t0", "ref": "3001.dat", "colour": 16, "at": [0, 0, 0]},
            {"id": "t1", "ref": "3003.dat", "colour": 1, "at": [0, -24, 0]}]]}]}


def whole_text(plan, parts):
    return build_plan(plan, parts)[0]


def test_an_edit_is_validated_by_what_changed_and_gives_the_same_text(parts, monkeypatch):
    plan = stack_plan()
    assert rebuild(plan, parts) == whole_text(plan, parts)
    calls = []
    real = editor.validate_text
    monkeypatch.setattr(editor, "validate_text", lambda *a, **k: calls.append(1) or real(*a, **k))
    edited = apply(plan, [{"op": "move", "id": "s3", "position": [333, 0, 20]},
                          {"op": "recolour", "id": "s4", "colour": 14},
                          {"op": "add", "section": "main.ldr", "part": "3003.dat", "colour": 1, "position": [0, 0, 400]}], parts)
    assert rebuild(edited, parts) == whole_text(edited, parts)
    assert calls == []                                              # no whole-text validation was needed


def test_the_first_rebuild_and_unusual_plans_still_validate_whole(parts, monkeypatch):
    calls = []
    real = editor.validate_text
    monkeypatch.setattr(editor, "validate_text", lambda *a, **k: calls.append(1) or real(*a, **k))
    plan = stack_plan()
    rebuild(plan, parts)
    assert calls == [1]
    related = copy.deepcopy(plan)
    related["sections"][0]["steps"][1].append({"id": "up", "ref": "3003.dat", "colour": 1, "on": "s0"})
    rebuild(related, parts)                                         # `on` placements depend on others: whole text
    assert calls == [1, 1]


@pytest.mark.parametrize("break_it", [
    lambda p: p["sections"][0]["steps"][0].append({"id": "twin", "ref": "3001.dat", "colour": 4, "at": [0, 0, 0]}),
    lambda p: p["sections"][0]["steps"][0].append({"id": "twin", "ref": "3001.dat", "colour": 4, "at": [0, 0, 0.0004]}),
    lambda p: p["sections"][0]["steps"][0][0].__setitem__("colour", 16),
    lambda p: p["sections"][0]["steps"][0][0].__setitem__("matrix", [[2, 0, 0], [0, 1, 0], [0, 0, 1]]),
    lambda p: p["sections"][0]["steps"][0][0].__setitem__("matrix", [[1, 0, 0], [0, 1, 0], [0, 0, 0]]),
    lambda p: p["sections"][0]["steps"][1][-1].__setitem__("colour", 16),
], ids=["same-spot", "nearly-same-spot", "colour-16-at-the-top", "scaled", "singular", "colour-16-in-submodel-at-top"])
def test_what_whole_validation_rejects_the_incremental_path_rejects_too(parts, break_it):
    plan = stack_plan()
    rebuild(plan, parts)
    # the tower is only used from main.ldr when something places it; make that so for the submodel case
    plan["sections"][0]["steps"][1].append({"id": "uses-tower", "ref": "tower.ldr", "colour": 4, "at": [0, 0, 300]})
    rebuild(plan, parts)
    broken = copy.deepcopy(plan)
    break_it(broken)
    try:
        expected = whole_text_error(broken, parts)
    except AssertionError:
        pytest.skip("this edit is valid for the whole validation too")
    with pytest.raises(ValueError) as raised:
        rebuild(broken, parts)
    assert str(raised.value) == expected


def whole_text_error(plan, parts):
    try:
        _, _, diagnostics = build_plan(plan, parts)
    except ValueError as exc:
        return editor._attribute(plan, str(exc))
    errors = [d for d in diagnostics if d["severity"] == "error"]
    assert errors
    return "Rebuild failed validation: " + "; ".join(d["message"] for d in errors[:3])


def test_a_known_plan_is_answered_without_building(parts, monkeypatch):
    plan = stack_plan()
    first = rebuild(plan, parts)
    moved = apply(plan, [{"op": "move", "id": "s1", "position": [5, 0, 0]}], parts)
    rebuild(moved, parts)
    monkeypatch.setattr(editor, "build_plan", lambda *a, **k: pytest.fail("built a plan it already knew"))
    assert rebuild(plan, parts) == first                            # the undo of the move


def test_rebuild_errors_do_not_poison_later_rebuilds(parts):
    plan = stack_plan()
    rebuild(plan, parts)
    bad = copy.deepcopy(plan)
    bad["sections"][0]["steps"][0].append({"id": "twin", "ref": "3001.dat", "colour": 4, "at": [0, 0, 0]})
    with pytest.raises(ValueError):
        rebuild(bad, parts)
    with pytest.raises(ValueError):
        rebuild(bad, parts)                                          # a failed plan is never remembered as good
    assert rebuild(plan, parts) == whole_text(plan, parts)


# ---------------------------------------------------------------- bounds, caches and the worker entry point

def test_fast_bounds_agree_with_pyldraws(official):
    from ldraw import Matrix, Vector
    from ldraw import inspection
    geometry = official.geometry("3001")
    matrix = Matrix([[0.6, 0, 0.8], [0, 1, 0], [-0.8, 0, 0.6]])
    position = Vector(13.5, -7.25, 100.0)
    fast = fastbounds.transformed_bounds(geometry.points, position=position, matrix=matrix)
    fastbounds.uninstall()
    try:
        slow = inspection.transformed_bounds(geometry.points, position=position, matrix=matrix)
    finally:
        fastbounds.install()
    for a, b in zip((*jsonable(fast.min), *jsonable(fast.max)), (*jsonable(slow.min), *jsonable(slow.max))):
        assert a == pytest.approx(b, abs=1e-9)


def test_caching_parts_returns_the_same_part_object(official):
    first = official.find_part(code="3001")
    assert first is not None and official.find_part(code="3001") is first
    assert official.find_part(code="no-such-part") is None


def _request(tmp_path, **extra):
    return {"plan": str(tmp_path / "plan.json"), "state": str(tmp_path / "state.json"),
            "mpd": str(tmp_path / "preview.mpd"), **extra}


def source_plan(tmp_path):
    path = tmp_path / "source.json"
    path.write_text(json.dumps(stack_plan()))
    return str(path)


def test_the_worker_entry_point_runs_the_cli_actions(parts, tmp_path):
    run = editor_command.run_request
    opened = run(_request(tmp_path, action="open", name="mine", source=source_plan(tmp_path)), None, parts)
    assert opened["ok"] and opened["exit_code"] == 0 and "plan" not in opened and "mpd" not in opened
    assert (tmp_path / "preview.mpd").read_text().startswith("0 FILE")
    edits = tmp_path / "edits.json"
    edits.write_text(json.dumps([{"op": "move", "id": "s3", "section": "main.ldr", "position": [20, -24, 0]}]))
    applied = run(_request(tmp_path, action="apply", edits=str(edits), base_revision=1), None, parts)
    assert applied["ok"] and applied["revision"] == 2 and applied["undo_depth"] == 1
    stale = run(_request(tmp_path, action="apply", edits=str(edits), base_revision=1), None, parts)
    assert not stale["ok"] and stale["conflict"] and stale["exit_code"] == 1
    undone = run(_request(tmp_path, action="undo"), None, parts)
    assert undone["ok"] and json.loads((tmp_path / "plan.json").read_text())["sections"][0]["steps"][0][3]["at"] == [300, 0, 0]
    redone = run(_request(tmp_path, action="redo"), None, parts)
    assert redone["ok"] and redone["redo_depth"] == 0
    edits.write_text(json.dumps([{"op": "move", "id": "nope", "position": [0, 0, 0]}]))
    failed = run(_request(tmp_path, action="apply", edits=str(edits)), None, parts)
    assert not failed["ok"] and failed["exit_code"] == 2 and "Unknown entry id" in failed["error"]
    full = run(_request(tmp_path, action="undo", full=True), None, parts)
    assert "plan" in full and "mpd" in full


def test_the_pipeline_worker_serves_editor_requests(parts, tmp_path):
    from ldraw_tools import pipeline
    worker = pipeline.Pipeline()
    worker.parts, worker.library = parts, Path("/library")
    reply = pipeline._handle(worker, {"op": "editor", "action": "open", **_request(tmp_path), "source": source_plan(tmp_path)})
    assert reply["ok"] and reply["exit_code"] == 0


def test_opening_the_same_model_again_comes_from_the_import_cache(parts, tmp_path, monkeypatch):
    monkeypatch.setattr(editor_command, "CACHE", tmp_path / "cache")
    model = tmp_path / "model.mpd"
    model.write_text("0 FILE m.ldr\n0 Model\n0 Name: m.ldr\n0 Author: T\n0 !LDRAW_ORG Model\n"
                     "1 4 0 0 0 1 0 0 0 1 0 0 0 1 3001.dat\n1 14 0 -24 0 1 0 0 0 1 0 0 0 1 3022.dat\n0 NOFILE\n")
    imports = []
    real = editor_command.import_mpd
    monkeypatch.setattr(editor_command, "import_mpd", lambda *a, **k: imports.append(1) or real(*a, **k))
    one, two = tmp_path / "one", tmp_path / "two"
    one.mkdir(), two.mkdir()
    first = editor_command.run_request(_request(one, action="open", from_mpd=str(model), name="m"), Path("/lib"), parts)
    second = editor_command.run_request(_request(two, action="open", from_mpd=str(model), name="m"), Path("/lib"), parts)
    assert first["ok"] and second["ok"] and imports == [1]
    for name in ("plan.json", "preview.mpd"):
        assert (one / name).read_text() == (two / name).read_text()
    assert json.loads((two / "state.json").read_text())["used"] == json.loads((one / "state.json").read_text())["used"]
    model.write_text(model.read_text().replace("3022.dat", "3003.dat"))          # another file: not the cached one
    editor_command.run_request(_request(tmp_path / "three", action="open", from_mpd=str(model), name="m"), Path("/lib"), parts)
    assert imports == [1, 1]
    # and the opened session keeps working: the first edit after a cache hit is checked incrementally
    edits = tmp_path / "edits.json"
    edits.write_text(json.dumps([{"op": "move", "id": "p1", "position": [40, 0, 0]}]))
    moved = editor_command.run_request(_request(two, action="apply", edits=str(edits), base_revision=1), Path("/lib"), parts)
    assert moved["ok"], moved


def test_idle_jobs_run_between_requests_and_never_ahead_of_one(tmp_path):
    import collections
    import os
    import threading
    from ldraw_tools import pipeline
    read, write = os.pipe()
    stdin = os.fdopen(read, "r", encoding="utf-8")
    ran = []

    def broken():
        raise RuntimeError("a warm-up job that fails")
    idle = collections.deque([lambda: ran.append(1), broken, lambda: ran.append(2)])
    lines = pipeline._lines(stdin, idle)

    def request_after_the_jobs():
        while len(ran) < 2:
            pass
        os.write(write, b'{"op": "ping"}\n')
    threading.Thread(target=request_after_the_jobs, daemon=True).start()
    assert next(lines) == '{"op": "ping"}\n' and ran == [1, 2]       # idle time was used, then the request came

    idle.append(lambda: ran.append(3))
    os.write(write, b'{"op": "ping", "again": true}\n')
    assert "again" in next(lines) and ran == [1, 2]                  # a request that is waiting goes first
    os.close(write)
    assert list(lines) == [] and ran == [1, 2]                       # at the end of input the worker just stops
    stdin.close()


def test_prewarm_jobs_cover_the_parts_a_plan_uses(parts, monkeypatch):
    plan = stack_plan()
    plan["sections"][0]["steps"][0].append({"id": "sym", "ref": "@parts.brick", "colour": 4, "at": [0, 0, 900]})
    loaded = []
    jobs = editor_command.prewarm_jobs(types_namespace(loaded), plan)
    for job in jobs:
        job()
    assert loaded[0] == "3001" and sorted(loaded) == ["3001", "3003", "3022"]    # most used first; submodels and symbols left out
    monkeypatch.setenv("LDRAW_NOVA_EDITOR_PREWARM", "0")
    assert editor_command.prewarm_jobs(types_namespace([]), plan) == []


def types_namespace(loaded):
    import types
    return types.SimpleNamespace(geometry=loaded.append)


def test_the_same_part_at_one_spot_in_two_rotations_is_not_a_possible_duplicate(parts, monkeypatch):
    from ldraw_tools.catalog import resolve_plan
    plan = stack_plan()
    plan["sections"][0]["steps"][0].append({"id": "turned", "ref": "3001.dat", "colour": 4, "at": [0, 0, 0], "yaw": 90})
    table, arrays = incremental._structure(resolve_plan(plan, parts))
    assert not incremental._might_collide("main.ldr", arrays)
    plan["sections"][0]["steps"][0].append({"id": "twin", "ref": "3001.dat", "colour": 4, "at": [0, 0, 0.001], "yaw": 90})
    table, arrays = incremental._structure(resolve_plan(plan, parts))
    assert incremental._might_collide("main.ldr", arrays)
    # and a model with such a pair still takes the quick path after an edit
    plan = stack_plan()
    plan["sections"][0]["steps"][0].append({"id": "turned", "ref": "3001.dat", "colour": 4, "at": [0, 0, 0], "yaw": 90})
    rebuild(plan, parts)
    calls = []
    real = editor.validate_text
    monkeypatch.setattr(editor, "validate_text", lambda *a, **k: calls.append(1) or real(*a, **k))
    rebuild(apply(plan, [{"op": "move", "id": "s5", "position": [555, 0, 0]}], parts), parts)
    assert calls == []


def test_a_batch_that_is_applied_twice_snaps_once(connected, monkeypatch):
    """run_editor derives the inverse by applying the batch, then applies it: the second snap is a repeat."""
    from ldraw_tools.editor import inverse
    plan = grid_plan()
    edits = [{"op": "add", "section": "main.ldr", "part": "3022.dat", "colour": 14, "position": [0, -50, 0]},
             {"op": "snap", "id": editor._next_id(plan)}]
    builds = []
    real = editor.build_plan
    monkeypatch.setattr(editor, "build_plan", lambda *a, **k: builds.append(1) or real(*a, **k))
    editor._SNAPS.clear()
    undo = inverse(plan, edits, connected)
    result = apply(plan, edits, connected)
    assert builds == [1]                                            # one snap for both passes
    assert undo and result["sections"][0]["steps"][-1][-1]["at"] == [0, -32, 0]
    apply(plan, [edits[0], {**edits[1], "to": "b0"}], connected)
    assert len(builds) == 2                                         # a different question is asked afresh
