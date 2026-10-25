"""Make this a template: gate, role preview, the derivation job, saving, and the saved template offered next to the generators.

Like the other workspace tests this runs the real toolkit (and its library); without them the module is skipped.
"""
import os
import time

import pytest
from fastapi.testclient import TestClient

import generator_bridge
import settings
from main import app
from routers import templates_api

pytestmark = pytest.mark.skipif(not (settings.TOOLKIT_PYTHON.exists() and (settings.LDRAW_DIR / "parts").is_dir()),
                                reason="needs the toolkit and the LDraw library")

TINY = ("0 FILE tiny.ldr\n0 Two bricks\n0 Name: tiny.ldr\n0 Author: Tests\n0 !LDRAW_ORG Model\n"
        "1 4 0 0 0 1 0 0 0 1 0 0 0 1 3001.dat\n0 STEP\n1 1 0 -24 0 1 0 0 0 1 0 0 0 1 3003.dat\n0 NOFILE\n")


@pytest.fixture(autouse=True)
def traversable_tmp():
    os.chmod(settings.DATA_DIR.parent, 0o755)


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def truck_build(client, title="Red flatbed"):
    slug = client.post("/api/builds", json={"title": title}).json()["id"]
    made = client.post(f"/api/builds/{slug}/generate", json={"generator": "template:flatbed-truck", "params": {}})
    assert made.status_code == 200, made.text
    return slug


def wait_for(client, slug, job, timeout=300):
    end = time.time() + timeout
    while time.time() < end:
        row = client.get(f"/api/builds/{slug}/template/jobs/{job}").json()
        if row["state"] != "running":
            return row
        time.sleep(1)
    raise AssertionError("the job did not finish")


def test_a_build_with_too_few_parts_does_not_pass_the_gate(client):
    slug = client.post("/api/builds", json={"title": "tiny"}).json()["id"]
    assert client.post(f"/api/builds/{slug}/import", json={"filename": "t.mpd", "content": TINY}).status_code == 200
    checked = client.post(f"/api/builds/{slug}/template/check", json={})
    assert checked.status_code == 200, checked.text
    body = checked.json()
    assert body["gate"]["ok"] is False and "too little" in body["gate"]["reasons"][0]
    assert "preview" not in body


def test_from_a_build_to_a_template_offered_next_to_the_generators(client):
    slug = truck_build(client)
    checked = client.post(f"/api/builds/{slug}/template/check", json={}).json()
    assert checked["gate"]["ok"] and checked["gate"]["parts"] == 59
    shown = checked["preview"]
    assert shown["roles"]["wheel"]["count"] == 6 and shown["palette"]["wheel"] == 14 and "red flatbed" in checked["suggested_kinds"]
    mpd = client.get(shown["url"])
    assert mpd.status_code == 200 and mpd.text.lstrip().startswith("0 ")

    # a correction is applied to the preview
    first = client.get(f"/api/builds/{slug}/editor").json()["plan"]["sections"][0]
    entry = first["steps"][0][0]
    fixed = client.post(f"/api/builds/{slug}/template/check",
                        json={"gate": False, "corrections": [{"section": first["name"], "id": entry["id"], "role": "wing"}]}).json()
    assert fixed["gate"] is None and fixed["preview"]["corrected"] == 1 and fixed["preview"]["roles"]["wing"]["count"] >= 1

    started = client.post(f"/api/builds/{slug}/template/jobs",
                          json={"title": "Red flatbed truck", "kinds": ["Red Flatbed", "lorry"],
                                "corrections": [{"section": first["name"], "id": entry["id"], "role": "wing"}]})
    assert started.status_code == 200, started.text
    job = started.json()["job"]
    assert client.post(f"/api/builds/{slug}/template/jobs", json={"title": "again"}).status_code == 409
    row = wait_for(client, slug, job)
    assert row["state"] == "done", row
    result = row["result"]
    assert result["knobs"] and result["kinds"] == ["red flatbed", "lorry"] and result["source"]["build"] == slug
    assert result["source"]["version"] == 1 and "wing" in result["roles"]
    off = result["knobs"][0]["param"]

    saved = client.post(f"/api/builds/{slug}/template/jobs/{job}/save", json={"disabled": [off], "kinds": ["flatbed lorry"]})
    assert saved.status_code == 200, saved.text
    template_id = saved.json()["id"]
    assert template_id == "red-flatbed-truck" and saved.json()["generator"] == f"template:{template_id}"
    assert (settings.TEMPLATES_DIR / f"{template_id}.json").is_file()
    assert client.get("/api/templates").json()["templates"][0]["kinds"] == ["flatbed lorry"]

    listed = client.get("/api/generators").json()["generators"]
    entry = next(g for g in listed if g["id"] == f"template:{template_id}")
    assert entry["kind"] == "template" and off not in entry["params"]["properties"]
    assert "template:flatbed-truck" in {g["id"] for g in listed}

    # the next build can start from it, and the build records it for "Change settings"
    other = client.post("/api/builds", json={"title": "Another"}).json()["id"]
    made = client.post(f"/api/builds/{other}/generate", json={"generator": f"template:{template_id}", "params": {"colour_body": "blue"}})
    assert made.status_code == 200, made.text
    assert made.json()["generator"] == f"template:{template_id}"
    assert client.get(f"/api/builds/{slug}/template/jobs/{job}/files/../result.json").status_code in (404, 422)

    # the bridge hands the decision engine the new template too
    generator_bridge.set_registry(None)
    registry = generator_bridge.cached()
    assert registry is None or any(e["id"] == f"template:{template_id}" for e in registry["entries"])

    assert client.delete(f"/api/templates/{template_id}").status_code == 200
    assert client.get("/api/templates").json()["templates"] == []
    assert all(g["id"] != f"template:{template_id}" for g in client.get("/api/generators").json()["generators"])


def test_unknown_jobs_and_ids_are_refused(client):
    slug = client.post("/api/builds", json={"title": "x"}).json()["id"]
    assert client.get(f"/api/builds/{slug}/template/jobs/{'0' * 12}").status_code == 404
    assert client.get(f"/api/builds/{slug}/template/jobs/not-a-job").status_code == 404
    assert client.delete("/api/templates/../x").status_code in (404, 405)
    assert client.delete("/api/templates/nope").status_code == 404
    assert templates_api.ID
