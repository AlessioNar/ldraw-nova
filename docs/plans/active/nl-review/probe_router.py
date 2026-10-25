"""Feed hand-written, LLM-shaped spec.v1 dicts to the deterministic router (fast_path.route) to see what it does with them.
No model call. Run in the throwaway container like measure_nl.py. Writes /out/results-probe.json."""
import json, os, subprocess, sys, tempfile
sys.path.insert(0, "/app/web/backend")
os.environ.setdefault("LDRAW_NOVA_CONFIG_DIR", tempfile.mkdtemp())
import generator_bridge, fast_path
DUMP = ("import json\nfrom ldraw_tools import generators as g\nfrom ldraw_tools.generators.colours import NAMES\n"
        "print(json.dumps({'digest': g.digest(), 'entries': [x.entry() for x in g.REGISTRY.values()], 'colours': sorted(NAMES)}))\n")
reg = json.loads(subprocess.run(["/opt/ldraw-nova/.venv/bin/python", "-c", DUMP], cwd="/opt/ldraw-nova", capture_output=True, text=True, check=True).stdout)
generator_bridge.set_registry(reg)
E = {e["id"]: e for e in reg["entries"]}
HOUSE = {"generator": "house", "params": dict(E["house"]["defaults"])}
CAR = {"generator": "grand-tourer", "params": dict(E["grand-tourer"]["defaults"])}
out = {}
def show(label, r):
    slim = {k: v for k, v in r.items() if k in ("route", "generator", "changes", "notes", "reason", "question", "params")}
    if "params" in slim and slim["route"] == "generator": slim["params"] = {k: v for k, v in slim["params"].items() if v != E[slim["generator"]]["defaults"].get(k)}
    out[label] = slim; print(label, "->", json.dumps(slim))

# 1. substring kind matching: what generator does a *kind* hit?
for kind in ["lighthouse", "treehouse", "doghouse", "warehouse", "scarecrow", "caravan", "jetty", "carriage", "cargo ship", "spaceship", "shipyard", "bus stop", "cart", "tank", "ambulance", "digger", "hut", "skyscraper", "farm", "church", "bridge"]:
    e, why = fast_path.choose({"subject": {"family": "other", "kind": kind}})
    show(f"kind:{kind}", {"route": "generator" if e else "needs_ai", "generator": e and e["id"], "reason": why})
# 2. a new_model spec that also carries edits (multi-intent): edits are ignored?
show("multi: red car + 'longer' as edit", fast_path.route({"intent": "new_model", "subject": {"family": "car", "kind": "car"}, "colours": {"body": "Red"}, "edits": [{"op": "scale_param", "target": "whole", "delta": "longer"}]}))
show("multi: replace_model + set_colour", fast_path.route({"intent": "edit", "subject": {"family": "car"}, "edits": [{"op": "replace_model", "target": "whole", "value": "truck"}, {"op": "set_colour", "target": "body", "value": "Green"}]}, CAR))
show("multi: edit house roof blue + wheels bigger", fast_path.route({"intent": "edit", "subject": {"family": "house"}, "edits": [{"op": "set_colour", "target": "roof", "value": "Blue"}, {"op": "scale_param", "target": "wheels", "delta": "bigger"}]}, HOUSE))
# 3. clamping and ambiguity
show("clamp: house 40 x 40", fast_path.route({"intent": "new_model", "subject": {"family": "house", "kind": "house"}, "size": {"studs": {"width": 40, "depth": 40}}}))
show("clamp: house 7 wide", fast_path.route({"intent": "new_model", "subject": {"family": "house", "kind": "house"}, "size": {"studs": {"width": 7}}}))
show("ambiguous 6x8 (studs vs bricks)", fast_path.route({"intent": "new_model", "subject": {"family": "house", "kind": "cottage"}, "size": {"studs": {"width": 6, "depth": 8}, "unit_ambiguous": True}, "ambiguities": [{"field": "size", "question": "Is 6 x 8 in studs or in bricks?", "options": ["6x8 studs", "6x8 bricks"]}]}))
show("size big car (scale only)", fast_path.route({"intent": "new_model", "subject": {"family": "car", "kind": "car"}, "size": {"scale": "large"}}))
show("size big house", fast_path.route({"intent": "new_model", "subject": {"family": "house", "kind": "house"}, "size": {"scale": "large"}}))
show("size tiny house", fast_path.route({"intent": "new_model", "subject": {"family": "house", "kind": "house"}, "size": {"scale": "micro"}}))
# 4. colour harmony: what a one-colour request leaves untouched
show("blue house (body only)", fast_path.route({"intent": "new_model", "subject": {"family": "house", "kind": "house"}, "colours": {"body": "Blue"}}))
show("red house with roof colour same", fast_path.route({"intent": "new_model", "subject": {"family": "house", "kind": "house"}, "colours": {"body": "Dark Red"}}))
show("pink car (colour not in palette?)", fast_path.route({"intent": "new_model", "subject": {"family": "car", "kind": "car"}, "colours": {"body": "Pink", "wheels": "Light Nougat"}}))
# 5. features the generator lacks
show("house + tree + garden features", fast_path.route({"intent": "new_model", "subject": {"family": "house", "kind": "house"}, "features": [{"name": "tree"}, {"name": "garden fence", "required": False}]}))
show("house + required balcony", fast_path.route({"intent": "new_model", "subject": {"family": "house", "kind": "house"}, "features": [{"name": "balcony", "required": True}]}))
show("two storeys (coverage claims it)", fast_path.route({"intent": "new_model", "subject": {"family": "house", "kind": "house"}, "features": [{"name": "two storeys"}]}))
# 6. edits on a generator that cannot do them
show("car: make roof blue", fast_path.route({"intent": "edit", "subject": {"family": "car"}, "edits": [{"op": "set_colour", "target": "roof", "value": "Blue"}]}, CAR))
show("jet: bigger wheels", fast_path.route({"intent": "edit", "subject": {"family": "aircraft"}, "edits": [{"op": "scale_param", "target": "wheels", "delta": "bigger"}]}, {"generator": "courier-jet", "params": dict(E["courier-jet"]["defaults"])}))
show("house: remove the garage (unknown)", fast_path.route({"intent": "edit", "subject": {"family": "house"}, "edits": [{"op": "remove_feature", "target": "garage"}]}, HOUSE))
show("edit with no current (non-generator build)", fast_path.route({"intent": "edit", "subject": {"family": "house"}, "edits": [{"op": "set_colour", "target": "roof", "value": "Blue"}]}, None))
# 7. does the generator catalogue cover roles the person says? (roles per generator)
out["roles"] = {k: v["roles"] for k, v in E.items()}
out["colour_params"] = {k: [p for p, s in v["params"]["properties"].items() if s.get("x-colour")] for k, v in E.items()}
out["size_params"] = {k: [p for p, s in v["params"]["properties"].items() if s.get("type") == "integer"] for k, v in E.items()}
print(json.dumps(out["roles"])); print(json.dumps(out["colour_params"])); print(json.dumps(out["size_params"]))
json.dump(out, open("/out/results-probe.json", "w"), indent=1)
