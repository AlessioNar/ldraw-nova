"""The editor's answer as a difference: plan splices, the warm-up request and the refused-snap time budget."""
import json
import random

import pytest
from ldraw import Parts

from ldraw_tools import editor, fastsnap
from ldraw_tools.commands import editor as editor_command
from ldraw_tools.commands.editor import plan_delta
from ldraw_tools.editor import apply, rebuild


def splice(plan, splices):
    """What a client does with the splices (the same few lines as EditorCore.applyDelta)."""
    result = json.loads(json.dumps(plan))
    for sp in splices:
        step = result["sections"][sp["section"]]["steps"][sp["step"]]
        step[sp["at"]:sp["at"] + sp["remove"]] = sp["insert"]
    return result


def plan_of(count=12):
    entries = [{"id": f"s{i}", "ref": "3001.dat" if i % 2 == 0 else "3022.dat", "colour": 4, "at": [i * 100, 0, 0]}
               for i in range(count)]
    return {"version": 1, "author": "Tests", "sections": [
        {"name": "main.ldr", "description": "row", "steps": [entries[:6], entries[6:]]},
        {"name": "tower.ldr", "description": "tower", "steps": [[
            {"id": "t0", "ref": "3001.dat", "colour": 16, "at": [0, 0, 0]},
            {"id": "t1", "ref": "3003.dat", "colour": 1, "at": [0, -24, 0]}]]}]}


def test_the_delta_of_an_add_is_that_entry_alone(parts):
    old = plan_of()
    new = apply(old, [{"op": "add", "section": "main.ldr", "part": "3001.dat", "colour": 4, "position": [5, -24, 0]}], parts)
    delta = plan_delta(old, new)
    assert delta == [{"section": 0, "step": 1, "at": 6, "remove": 0, "insert": [new["sections"][0]["steps"][1][-1]]}]
    assert splice(old, delta) == new


def test_deltas_replay_to_the_same_plan_over_random_edits(parts):
    rng = random.Random(5)
    plan = plan_of()
    for _ in range(40):
        ids = [e["id"] for s in plan["sections"] for st in s["steps"] for e in st if e["id"].startswith(("s", "e"))]
        kind = rng.choice(["add", "move", "recolour", "delete", "rotate"])
        if kind == "add" or not ids:
            edit = {"op": "add", "section": "main.ldr", "part": "3003.dat", "colour": 14, "position": [rng.randint(-5, 5) * 20, -24, 40]}
        elif kind == "move":
            edit = {"op": "move", "id": rng.choice(ids), "section": "main.ldr", "position": [rng.randint(-9, 9) * 20, 0, 20]}
        elif kind == "recolour":
            edit = {"op": "recolour", "id": rng.choice(ids), "section": "main.ldr", "colour": rng.choice([1, 4, 14])}
        elif kind == "rotate":
            edit = {"op": "rotate", "id": rng.choice(ids), "section": "main.ldr", "axis": "y", "degrees": 90}
        else:
            edit = {"op": "delete", "id": rng.choice(ids), "section": "main.ldr"}
        new = apply(plan, [edit], parts)
        delta = plan_delta(plan, new)
        if delta is not None:
            assert splice(plan, delta) == new, edit
        plan = new


def test_a_change_of_shape_has_no_delta(parts):
    old = plan_of()
    emptied = apply(old, [{"op": "delete", "id": f"s{i}", "section": "main.ldr"} for i in range(6, 12)], parts)
    assert len(emptied["sections"][0]["steps"]) == 1
    assert plan_delta(old, emptied) is None
    assert plan_delta(old, {**old, "author": "someone else"}) is None
    renamed = json.loads(json.dumps(old))
    renamed["sections"][1]["description"] = "changed"
    assert plan_delta(old, renamed) is None
    assert plan_delta(old, json.loads(json.dumps(old))) == []


def request(tmp_path, **extra):
    return {"plan": str(tmp_path / "plan.json"), "state": str(tmp_path / "state.json"),
            "mpd": str(tmp_path / "preview.mpd"), **extra}


def test_apply_undo_and_redo_report_their_delta_and_a_replay_equals_the_files(parts, tmp_path):
    run = editor_command.run_request
    source = tmp_path / "source.json"
    source.write_text(json.dumps(plan_of()))
    assert run(request(tmp_path, action="open", name="m", source=str(source)), None, parts)["ok"]
    mine = json.loads((tmp_path / "plan.json").read_text())
    edits = tmp_path / "edits.json"

    def step(action, base):
        nonlocal mine
        reply = run(request(tmp_path, action=action, edits=str(edits), base_revision=base), None, parts)
        assert reply["ok"] and reply["exit_code"] == 0, reply
        assert "plan" not in reply and "mpd" not in reply
        assert reply["delta"] is not None
        mine = splice(mine, reply["delta"])
        assert mine == json.loads((tmp_path / "plan.json").read_text())
        return reply["revision"]

    revision = 1
    for n in range(3):
        edits.write_text(json.dumps([{"op": "add", "section": "main.ldr", "part": "3001.dat", "colour": 4, "position": [n * 20, -24, 60]}]))
        revision = step("apply", revision)
    edits.write_text(json.dumps([{"op": "move", "id": "s2", "section": "main.ldr", "position": [0, 0, 100]}]))
    revision = step("apply", revision)
    for action in ("undo", "undo", "redo", "undo", "undo", "undo", "redo"):
        revision = step(action, revision)
    # the preview text on disk is the plan's build, so a client that replays the splices shows what a reload shows
    assert (tmp_path / "preview.mpd").read_text().splitlines() == rebuild(mine, parts).splitlines()


def test_a_refused_action_has_no_delta(parts, tmp_path):
    run = editor_command.run_request
    source = tmp_path / "source.json"
    source.write_text(json.dumps(plan_of()))
    run(request(tmp_path, action="open", name="m", source=str(source)), None, parts)
    refused = run(request(tmp_path, action="undo"), None, parts)
    assert not refused["ok"] and refused.get("delta") is None


def test_warm_queues_the_plans_part_types_without_touching_the_session(parts, tmp_path, monkeypatch):
    import collections
    monkeypatch.delenv("LDRAW_NOVA_EDITOR_PREWARM", raising=False)
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan_of()))
    idle = collections.deque()
    reply = editor_command.run_request({"action": "warm", "plan": str(plan_path)}, None, parts, idle=idle)
    assert reply["ok"] and reply["exit_code"] == 0 and reply["jobs"] == 3 == len(idle)
    assert editor_command.run_request({"action": "warm", "plan": str(tmp_path / "missing.json")}, None, parts)["exit_code"] == 2
    assert not (tmp_path / "state.json").exists()


# ---------------------------------------------------------------- the time budget of a refused snap

@pytest.fixture
def connected(parts, tmp_path):
    path = tmp_path / "shadow"
    (path / "parts").mkdir(parents=True)
    for code, height in [("3001", 24), ("3003", 24), ("3022", 8)]:
        (path / "parts" / f"{code}.dat").write_text(
            "0 !LDCAD SNAP_CLEAR\n"
            "0 !LDCAD SNAP_CYL [id=top] [gender=M] [secs=R 6 4] [pos=10 0 10]\n"
            f"0 !LDCAD SNAP_CYL [id=bottom] [gender=F] [secs=R 6 4] [pos=10 {height} 10]\n")
    catalog = Parts(parts.path)
    catalog.add_connection_shadow(path)
    return catalog


def refused_snap(connected, monkeypatch, budget):
    """Snap a brick to one whose free studs and sockets are both covered by parts that are not connected to it: every pose collides. Returns (reply, poses examined)."""
    plan = {"version": 1, "author": "Tests", "sections": [{"name": "main.ldr", "description": "x", "steps": [[
        {"id": "b", "ref": "3001.dat", "colour": 4, "at": [0, 0, 0]},
        {"id": "x", "ref": "3001.dat", "colour": 4, "at": [20, -24, 0]}, {"id": "y", "ref": "3001.dat", "colour": 4, "at": [20, 24, 0]}]]}]}
    seen = []
    original = fastsnap.collision_pairs
    monkeypatch.setattr(fastsnap, "collision_pairs", lambda *a, **k: seen.append(1) or original(*a, **k))
    monkeypatch.setattr(fastsnap, "BUDGET_SECONDS", budget)
    edits = [{"op": "add", "section": "main.ldr", "part": "3001.dat", "colour": 1, "position": [0, -24, 0]},
             {"op": "snap", "id": editor._next_id(plan), "to": "b"}]
    try:
        reply = apply(plan, edits, connected)
    except ValueError as exc:
        reply = str(exc)
    return reply, len(seen)


def test_a_refused_snap_stops_examining_when_its_time_is_up(connected, monkeypatch):
    unbounded, many = refused_snap(connected, monkeypatch, None)
    bounded, few = refused_snap(connected, monkeypatch, 1e-9)
    assert isinstance(unbounded, str) and isinstance(bounded, str), (unbounded, bounded)
    assert "collides" in unbounded or "no candidate" in unbounded
    assert few < many, (few, many)
    assert "no candidate" in bounded


def test_the_budget_never_changes_a_snap_that_is_quick(connected, monkeypatch):
    plan = {"version": 1, "author": "Tests", "sections": [{"name": "main.ldr", "description": "x", "steps": [[
        {"id": "b", "ref": "3001.dat", "colour": 4, "at": [0, 0, 0]}]]}]}
    edits = [{"op": "add", "section": "main.ldr", "part": "3022.dat", "colour": 14, "position": [0, -24, 0]},
             {"op": "snap", "id": "e1", "to": "b"}]
    monkeypatch.setattr(fastsnap, "BUDGET_SECONDS", None)
    exact = apply(plan, edits, connected)
    editor._SNAPS.clear()
    monkeypatch.setattr(fastsnap, "BUDGET_SECONDS", 0.25)
    assert apply(plan, edits, connected) == exact
