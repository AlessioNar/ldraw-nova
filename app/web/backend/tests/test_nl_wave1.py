"""Natural-language wave 1: role edits on any build, the divergence guard, conversational context, matching, colour harmony,
hint/decision agreement and the quick save. The understanding model is never called (model_params is stubbed to None, so
only rules and the keyword reading answer). The workspace parts run the real toolkit like test_workspace.py."""
import asyncio
import json
import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import fast_path
import generator_bridge
import settings
import understand
from main import app
from store import get_store

REGISTRY = json.loads((Path(__file__).with_name("generators_registry.json")).read_text(encoding="utf-8"))
ENTRIES = {e["id"]: e for e in REGISTRY["entries"]}
TINY = ("0 FILE tiny.ldr\n0 Two bricks\n0 Name: tiny.ldr\n0 Author: Tests\n0 !LDRAW_ORG Model\n"
        "1 4 0 0 0 1 0 0 0 1 0 0 0 1 3001.dat\n0 STEP\n1 1 0 -24 0 1 0 0 0 1 0 0 0 1 3003.dat\n0 NOFILE\n")


@pytest.fixture(autouse=True)
def registry(monkeypatch):
    generator_bridge.set_registry(REGISTRY)
    monkeypatch.setattr(fast_path, "history_stats", lambda force=False: None)

    async def no_model(llm_id=None):
        return None
    monkeypatch.setattr(understand, "model_params", no_model)
    yield
    generator_bridge.set_registry(None)


def decide(text, current=None, context=None, previous=None):
    return asyncio.run(fast_path.describe(text, current=current, context=context, previous_text=previous))


def new(kind, family="house", **spec):
    return fast_path.route({"intent": "new_model", "subject": {"family": family, "kind": kind}, **spec})


# ---- matching ---------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("kind,family,generator,preset", [
    ("house", "house", "building", "house"), ("cottage", "house", "building", "cottage"), ("shop", "building", "building", "shop"),
    ("tower", "tower", "building", "tower"), ("castle", "tower", "building", "castle-keep"),
    ("lighthouse", "tower", "building", "lighthouse"), ("warehouse", "building", "building", "warehouse"),
    ("red house", "house", "building", "house"), ("jet", "aircraft", "courier-jet", None), ("car", "car", "grand-tourer", None)])
def test_kinds_route_by_whole_word_to_the_right_design(kind, family, generator, preset):
    decision = new(kind, family)
    assert decision["route"] == "generator" and decision["generator"] == generator and decision.get("preset") == preset


@pytest.mark.parametrize("kind,family", [("caravan", "other"), ("jetty", "other"), ("scarecrow", "other"), ("carriage", "other")])
def test_look_alike_words_are_not_matched_by_substring(kind, family):
    decision = new(kind, family)
    assert decision["route"] in ("needs_ai", "clarify"), decision


def test_a_preset_keeps_its_own_storeys_and_the_fallback_is_labelled():
    house = new("house")
    assert house["params"]["storeys"] == {"count": 2} and house["params"]["roof"]["colour"] == "dark red"
    ship = new("cargo ship", "boat")
    assert ship["route"] == "generator" and ship["closest"] and ship["notes"][0].startswith("closest design:")
    assert not new("jet", "aircraft").get("closest") and not new("house").get("closest")


# ---- colour harmony ---------------------------------------------------------------------------------------------

def test_one_named_colour_gets_a_matching_roof_and_trim():
    decision = new("house", colours={"body": "Blue"})
    assert decision["params"]["wall_colour"] == "blue" and decision["params"]["roof_colour"] == "dark blue"
    assert decision["params"]["trim_colour"] == "white" and any("to go with the blue walls" in n for n in decision["notes"])
    red = new("cottage", colours={"body": "Red"})
    assert red["params"]["roof_colour"] == "dark grey"                                  # never the same as the walls
    both = new("house", colours={"body": "White", "roof": "Black"})
    assert both["params"]["roof_colour"] == "black" and "trim_colour" not in both["params"]
    assert "roof_colour" not in new("car", "car", colours={"body": "Blue"})["params"]  # vehicles are left alone


# ---- conversational context -------------------------------------------------------------------------------------

def test_again_and_a_bit_less_work_against_the_last_change():
    last = {"changes": [{"param": "width", "label": "width", "from": 8, "to": 12}]}
    edit = lambda text, last=last: (fast_path.relative_spec(text, last) or {}).get("edits")
    assert edit("again") == [{"op": "set_param", "target": "width", "value": 16}]
    assert edit("a bit less") == [{"op": "set_param", "target": "width", "value": 10}]
    assert edit("bigger")[0]["value"] == 16 and edit("smaller")[0]["value"] == 8
    shrunk = {"changes": [{"param": "width", "from": 12, "to": 8}]}
    assert edit("bigger", shrunk)[0]["value"] == 12 and edit("again", shrunk)[0]["value"] == 4
    assert fast_path.relative_spec("again", None) is None and fast_path.relative_spec("again", {"changes": [{"param": "roof_colour", "from": "red", "to": "blue"}]}) is None


def test_a_bit_less_is_decided_for_free_on_a_generator_build():
    current = {"generator": "house", "params": {**ENTRIES["house"]["defaults"], "width": 12}}
    context = {"has_model": True, "last": {"changes": [{"param": "width", "label": "width", "from": 8, "to": 12}]}}
    decision = decide("a bit less", current, context)
    assert decision["route"] == "edit" and decision["understood_by"] == "rules" and decision["params"]["width"] == 10
    assert decision["changes"][0]["from"] == 12 and decision["changes"][0]["to"] == 10


def test_the_understanding_call_gets_the_build_and_the_last_turns():
    context = {"has_model": True, "parts": 112, "roles": {"roof": 14, "wall": 80}, "selection": 2,
               "turns": ["make it bigger -> width: 8 -> 12", "a bit less -> width: 12 -> 10"]}
    text = fast_path.context_text(None, context)
    assert "112 parts" in text and "roof 14" in text and "2 parts selected" in text and "width: 8 -> 12" in text
    assert "generator house" in fast_path.context_text({"generator": "house", "params": {"width": 10}}, {})
    assert "CONTEXT: " + text[:30] in understand.tail("again", None, None, "", text)


# ---- role edits by words ----------------------------------------------------------------------------------------

def test_role_rules_read_english_without_a_model():
    edits = lambda t: (understand.role_rules(t) or {}).get("edits")
    assert edits("make the roof blue") == [{"op": "set_colour", "target": "roof", "value": "Blue"}]
    assert edits("windows white") == [{"op": "set_colour", "target": "windows", "value": "White"}]
    assert edits("paint the selection red")[0]["target"] == "selection" and edits("delete these") == [{"op": "remove_feature", "target": "selection"}]
    assert edits("remove the chimney") == [{"op": "remove_feature", "target": "chimney"}]
    assert [e["target"] for e in edits("make the roof red and the door white")] == ["roof", "door"]
    assert edits("make the roof tiles dark grey")[0]["target"] == "roof" and edits("a blue house") is None
    assert understand.role_rules("again", "make the roof blue")["edits"][0]["target"] == "roof"


def test_a_role_edit_is_one_free_step_with_a_report():
    context = {"has_model": True, "roles": {"roof": 4}}
    decision = decide("make the roof blue", None, context)
    assert decision["route"] == "edit" and decision["role_edits"] == [{"op": "recolour", "role": "roof", "colour": "blue"}]
    assert decision["changes"][0]["label"] == "roof colour" and decision["understood_by"] == "rules" and decision["understanding_cost_eur"] == 0.0
    unknown = decide("make the roof sparkly", None, context)
    assert unknown["route"] in ("clarify", "needs_ai")
    unsupported = decide("add a chimney", None, context)
    assert unsupported["route"] == "needs_ai" and "chimney" in unsupported["reason"]


# ---- hint and decision agree ------------------------------------------------------------------------------------

PROMPTS = next((p for p in (Path("/opt/ldraw-nova/docs/plans/active/nl-review/prompts.json"),
                            *(parent / "docs/plans/active/nl-review/prompts.json" for parent in Path(__file__).resolve().parents)) if p.is_file()), None)


@pytest.mark.skipif(PROMPTS is None, reason="docs/plans/active/nl-review/prompts.json is not mounted")
def test_hint_and_decision_agree_on_the_54_prompts():
    contexts = {"new": (None, None),
                "house": ({"generator": "house", "params": dict(ENTRIES["house"]["defaults"])}, {"has_model": True}),
                "car": ({"generator": "grand-tourer", "params": dict(ENTRIES["grand-tourer"]["defaults"])}, {"has_model": True}),
                "nongen": (None, {"has_model": True, "parts": 120, "roles": {"roof": 14, "wall": 80}})}
    rows = json.loads(PROMPTS.read_text(encoding="utf-8"))["prompts"]
    assert len(rows) == 54
    disagree = []
    for row in rows:
        current, context = contexts[row["ctx"]]
        hint = fast_path.rules_guess(row["text"], current, context)["route"]
        said = decide(row["text"], current, context)["route"]
        if hint != said:
            disagree.append((row["id"], row["text"], hint, said))
    agreement = 1 - len(disagree) / len(rows)
    assert agreement >= 0.9, (agreement, disagree)


# ---- the workspace: real toolkit --------------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def traversable_tmp():
    os.chmod(settings.DATA_DIR.parent, 0o755)


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def make_build(client, title, *models):
    slug = client.post("/api/builds", json={"title": title}).json()["id"]
    for text in models:
        assert client.post(f"/api/builds/{slug}/import", json={"filename": "m.mpd", "content": text}).status_code == 200
    return slug


def view(client, slug):
    return client.get(f"/api/builds/{slug}/editor").json()


def say(client, slug, text, **body):
    reply = client.post(f"/api/builds/{slug}/draft/describe", json={"text": text, **body})
    assert reply.status_code == 200, reply.text
    return reply.json()


@pytest.fixture(scope="module")
def house_mpd(client):
    """A real house as plain LDraw text, the way an import or an AI build arrives: no roles stored on it."""
    os.chmod(settings.DATA_DIR.parent, 0o755)
    slug = make_build(client, "nl house source")
    made = client.post(f"/api/builds/{slug}/draft/regenerate", json={"generator": "house", "params": {}})
    assert made.status_code == 200, made.text
    return view(client, slug)["mpd"]


def count_colour(mpd, code):
    return sum(1 for line in mpd.splitlines() if line.startswith("1 ") and line.split()[1] == str(code))


def test_make_the_roof_blue_on_an_imported_build_changes_the_model_in_place(client, house_mpd):
    slug = make_build(client, "nl imported", house_mpd)
    before = client.post(f"/api/builds/{slug}/draft/open", json={}).json()
    roof_before = count_colour(before["mpd"], 320)                        # the generator's default dark red roof
    assert roof_before > 0 and before["draft"]["changes"] == 0
    reply = say(client, slug, "make the roof blue")
    assert reply["route"] == "edit" and "generator" not in reply and reply["understood_by"] == "rules"
    assert reply["changes"][0]["label"] == "roof colour" and reply["changes"][0]["to"] == "blue" and reply["changes"][0]["count"] > 0
    assert reply["draft"]["changes"] == 1 and reply["draft"]["undo_depth"] == 1          # ONE undoable step
    after = view(client, slug)
    assert count_colour(after["mpd"], 320) < roof_before / 2 and count_colour(after["mpd"], 1) >= reply["changes"][0]["count"]
    assert reply["took_ms"] < 20000
    turns = get_store().messages(reply["chat_id"])
    assert turns[-1]["_fast_path"]["changes"][0]["label"] == "roof colour" and turns[-1]["_fast_path"]["cost_eur"] == 0.0
    # the next hint knows the build: a role edit is free, not "AI build: EUR 1-2"
    hint = client.get("/api/describe/estimate", params={"text": "make the door white", "build_id": slug}).json()
    assert hint["route"] == "edit" and hint["hint"].startswith("Fast path: free")
    client.post(f"/api/builds/{slug}/editor/undo", json={"base_revision": after["revision"]})
    assert count_colour(view(client, slug)["mpd"], 320) == roof_before


def test_an_unknown_part_asks_once_with_choices_and_changes_nothing(client, house_mpd):
    slug = make_build(client, "nl unknown", house_mpd)
    client.post(f"/api/builds/{slug}/draft/open", json={})
    reply = say(client, slug, "remove the spoiler")
    assert reply["route"] == "clarify" and "spoiler" in reply["question"] and reply["choices"] and reply["draft"]["changes"] == 0


def test_selection_from_the_canvas_names_the_parts(client, house_mpd):
    slug = make_build(client, "nl selection", house_mpd)
    opened = client.post(f"/api/builds/{slug}/draft/open", json={}).json()
    ids = [f"{sec['name']}:{p['id']}" for sec in opened["plan"]["sections"] for step in sec["steps"] for p in step
           if p["ref"].endswith(".dat")][-2:]                               # "section:id", as the canvas names pieces in submodels
    nothing = say(client, slug, "paint this red")
    assert nothing["route"] == "clarify" and "Select the parts" in nothing["question"]
    reply = say(client, slug, "paint this red", selection=ids)
    assert reply["route"] == "edit", reply
    assert reply["changes"][0]["label"] == "selection colour" and reply["draft"]["undo_depth"] == 1
    gone = say(client, slug, "delete these", selection=ids)
    assert gone["changes"][0]["to"] == "removed" and gone["draft"]["undo_depth"] == 2


SUBMODEL_MPD = """0 FILE main.ldr
0 Name: main.ldr
1 4 0 0 0 1 0 0 0 1 0 0 0 1 3001.dat
1 4 0 -24 0 1 0 0 0 1 0 0 0 1 3001.dat
1 16 80 0 0 1 0 0 0 1 0 0 0 1 wing.ldr
0 FILE wing.ldr
0 Name: wing.ldr
1 2 0 0 0 1 0 0 0 1 0 0 0 1 3001.dat
1 2 0 -24 0 1 0 0 0 1 0 0 0 1 3001.dat
"""


def colours_by_file(mpd):
    out, current = {}, None
    for line in mpd.splitlines():
        if line.startswith("0 FILE "):
            current = line[7:].strip()
            out[current] = []
        elif line.startswith("1 ") and current:
            out[current].append(line.split()[1])
    return out


def test_selection_ids_inside_a_submodel_paint_only_that_submodel(client):
    """The canvas names a part by section and id ("wing.ldr:e1"): the id alone also exists in the main model."""
    slug = make_build(client, "nl submodel selection", SUBMODEL_MPD)
    opened = client.post(f"/api/builds/{slug}/draft/open", json={}).json()
    sections = {sec["name"]: [p["id"] for step in sec["steps"] for p in step if p["ref"].endswith(".dat")] for sec in opened["plan"]["sections"]}
    root, wing = list(sections)[0], next(n for n in sections if n != list(sections)[0])
    shared = set(sections[root]) & set(sections[wing])
    assert shared, sections                                            # the same ids in both files: a bare id would be ambiguous
    pick = sorted(shared)[0]
    reply = say(client, slug, "paint this blue", selection=[f"{wing}:{pick}"])
    assert reply["route"] == "edit" and reply["changes"][0]["label"] == "selection colour" and reply["changes"][0]["count"] == 1, reply
    after = colours_by_file(view(client, slug)["mpd"])
    assert after[root].count("4") == 2 and after[root].count("1") == 0                    # the main model is untouched
    assert sorted(after[wing]) == ["1", "2"]                                               # one wing part is blue now
    gone = say(client, slug, "delete this", selection=[f"{wing}:{pick}"])
    assert gone["changes"][0]["to"] == "removed" and len(colours_by_file(view(client, slug)["mpd"])[wing]) == 1


def test_a_text_edit_never_regenerates_over_a_diverged_draft(client):
    slug = make_build(client, "nl diverged")
    client.post(f"/api/builds/{slug}/draft/regenerate", json={"generator": "house", "params": {}})
    start = view(client, slug)
    live = say(client, slug, "make the roof green")
    assert live["route"] == "edit" and live["generator"] == "house"                       # still the generator's own plan
    first = next(p for step in start["plan"]["sections"][0]["steps"] for p in step)
    moved = {"op": "move", "id": first["id"], "position": [first["at"][0] + 40, first["at"][1], first["at"][2]]}
    r = client.post(f"/api/builds/{slug}/editor/edits", json={"edits": [moved], "base_revision": view(client, slug)["revision"]})
    assert r.status_code == 200, r.text
    edited_mpd = view(client, slug)["mpd"]
    reply = say(client, slug, "make the roof red")
    assert reply["route"] == "edit" and "generator" not in reply
    assert count_colour(view(client, slug)["mpd"], 4) > count_colour(edited_mpd, 4)       # edited in place
    changed = set(edited_mpd.splitlines()) - set(view(client, slug)["mpd"].splitlines())
    assert 0 < len(changed) < len(edited_mpd.splitlines()) / 2                              # only the roof's lines differ
    # a whole new model is asked about, never silently swapped in
    ask = say(client, slug, "a small red car")
    assert ask["route"] == "clarify" and "Yes, start over" in ask["choices"] and ask["pending"]["kind"] == "decision", ask
    assert view(client, slug)["mpd"] != ""                                                # nothing replaced yet
    go = say(client, slug, "Yes, start over")
    assert go["route"] == "generator" and go["replaced_manual_edits"] is True
    # explicit words regenerate from the settings
    again = say(client, slug, "start over")
    assert again["route"] == "generator" and again["generator"] == "grand-tourer"


def test_a_saved_diverged_version_keeps_the_guard_and_the_save_checks_in_the_background(client):
    slug = make_build(client, "nl save")
    client.post(f"/api/builds/{slug}/draft/regenerate", json={"generator": "house", "params": {}})
    v = view(client, slug)
    first = next(p for step in v["plan"]["sections"][0]["steps"] for p in step)
    client.post(f"/api/builds/{slug}/editor/edits", json={"base_revision": v["revision"], "edits": [
        {"op": "move", "id": first["id"], "position": [first["at"][0] + 40, first["at"][1], first["at"][2]]}]})
    started = time.monotonic()
    saved = client.post(f"/api/builds/{slug}/draft/save")
    took = time.monotonic() - started
    assert saved.status_code == 200, saved.text
    row = next(r for r in client.get(f"/api/builds/{slug}/workspace").json()["versions"] if r["version"] == saved.json()["version"])
    assert row["check"] is not None and row["check"]["status"] in ("checking", "ok", "warnings", "failed")
    deadline = time.monotonic() + 240
    while row["check"]["status"] == "checking" and time.monotonic() < deadline:
        time.sleep(2)
        row = next(r for r in client.get(f"/api/builds/{slug}/workspace").json()["versions"] if r["version"] == saved.json()["version"])
    assert row["check"]["status"] in ("ok", "warnings", "failed") and isinstance(row["check"]["warnings"], list)
    assert took < 60                                                                       # the geometry check no longer blocks it
    # reopened clean on the saved version the build's source is still marked: edits go by role, not by regeneration
    reply = say(client, slug, "make the roof blue")
    assert reply["route"] == "edit" and "generator" not in reply
