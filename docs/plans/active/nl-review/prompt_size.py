"""Size of the understanding call's prompt (no model call). Run in the container like measure_nl.py."""
import json, os, subprocess, sys, tempfile
sys.path.insert(0, "/app/web/backend")
os.environ.setdefault("LDRAW_NOVA_CONFIG_DIR", tempfile.mkdtemp())
import understand

DUMP = "import json\nfrom ldraw_tools import generators as g\nprint(json.dumps({'digest': g.digest()}))\n"
reg = json.loads(subprocess.run(["/opt/ldraw-nova/.venv/bin/python", "-c", DUMP], cwd="/opt/ldraw-nova",
                                capture_output=True, text=True, check=True).stdout)
d = understand.make_digest(reg["digest"])
pre, tl = understand.prefix(d), json.dumps(understand.tool())
tail = understand.tail("make the roof blue", {"intent": "new_model", "subject": {"family": "house", "kind": "house"},
                                              "colours": {"body": "tan", "roof": "dark red"}, "size": {"studs": {"width": 8, "depth": 6}}})
print(json.dumps({"prefix_chars": len(pre), "tool_schema_chars": len(tl), "tail_chars": len(tail),
                  "approx_tokens_in": round((len(pre) + len(tl) + len(tail)) / 3.8)}))
