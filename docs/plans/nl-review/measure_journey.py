"""Time the deterministic half of the journey with the real resident worker protocol (ldraw_tools.pipeline --serve).

For three generator requests (the params come from results-rules.json, i.e. what describe produced offline):
  worker start -> first build (cold: loads the parts library) -> same build again (cache hit) -> changed params (warm)
  -> the two real call shapes: draft/regenerate = geometry False, builds/generate (a new version) = geometry True, + GLB.
Run inside the throwaway container (see measure_nl.py header). Writes /out/results-journey.json.
"""
import json, subprocess, tempfile, time, uuid, shutil, os

PY, CWD = "/opt/ldraw-nova/.venv/bin/python", "/opt/ldraw-nova"
rules = {r["id"]: r for r in json.load(open("/out/results-rules.json"))}

def start():
    t = time.monotonic()
    p = subprocess.Popen([PY, "-m", "ldraw_tools.pipeline", "--serve"], cwd=CWD, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL, text=True, env={**os.environ, "LDRAW_NOVA_WORKER_DEBUG": "0"})
    return p, t

def ask(p, payload):
    t = time.monotonic()
    p.stdin.write(json.dumps({**payload, "id": uuid.uuid4().hex}) + "\n"); p.stdin.flush()
    reply = json.loads(p.stdout.readline())
    return reply, time.monotonic() - t

cases = [("car", "n01", {"length": 24}), ("house", "n04", {"roof_colour": "blue"}), ("starfighter", "n06", {"wingspan": 20})]
results = []
for label, pid, change in cases:
    base = {k: v for k, v in rules[pid]["params"].items()}
    gen = rules[pid]["generator"]
    out = tempfile.mkdtemp(); os.chmod(out, 0o777)
    p, t0 = start()
    row = {"case": label, "generator": gen, "text": rules[pid]["text"], "describe_ms_offline": rules[pid]["describe_ms"]}
    def build(params, geometry, glb=False, name="model"):
        reply, dt = ask(p, {"op": "build", "outdir": out, "generator": gen, "params": params, "name": name, "geometry": geometry, "glb": glb})
        return {"wall_s": round(dt, 2), "ok": reply.get("ok"), "cached": reply.get("cached"), "timings": reply.get("timings"),
                "parts": (reply.get("geometry") or {}).get("parts") if isinstance(reply.get("geometry"), dict) else None,
                "diagnostics": [d.get("message") for d in reply.get("diagnostics", [])][:3]}
    row["cold_regenerate_geometry_off"] = build(base, False)
    row["cold_wall_incl_process_start_s"] = round(time.monotonic() - t0, 2)
    row["same_again_cache_hit"] = build(base, False)
    changed = {**base, **change}
    row["warm_changed_params_geometry_off"] = build(changed, False)
    row["warm_new_version_geometry_on"] = build({**changed, **{k: (v + 2 if isinstance(v, int) and k in change else v) for k, v in change.items()}}, True) if isinstance(list(change.values())[0], int) else build(changed, True)
    row["warm_new_version_geometry_on_glb"] = build(changed, True, glb=True, name="glb")
    p.stdin.close(); p.terminate()
    results.append(row)
    print(json.dumps(row)[:1500])
json.dump(results, open("/out/results-journey.json", "w"), indent=1)
