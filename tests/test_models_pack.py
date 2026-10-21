import hashlib
import json

import pytest

from conftest import load_models_pack, mpd

pack = load_models_pack()
pytestmark = pytest.mark.skipif(pack is None, reason="tools/models_pack.py is not part of this checkout")

LICENCE = "0 !LICENSE Redistributable under CCAL version 2.0 : see CAreadme.txt"


def model(name, extra=""):
    text = mpd("1 16 0 0 0 1 0 0 0 1 0 0 0 1 3001.dat").replace("0 NOFILE", f"{LICENCE}\r\n{extra}0 NOFILE")
    return text.replace("\r\n", "\n").encode()



def write(folder, name, **kw):
    folder.mkdir(exist_ok=True)
    (folder / name).write_bytes(model(name, **kw))
    return folder / name


def run(tmp_path, *args):
    return pack.main(["--packed", str(tmp_path / "packed"), "--target", str(tmp_path / "out"), *args])


def shard_bytes(tmp_path):
    return {p.name: p.read_bytes() for p in (tmp_path / "packed").glob("*.tar.xz")}


@pytest.fixture
def source(tmp_path):
    folder = tmp_path / "out"
    for name in ("106-1.mpd", "10270-1.mpd", "10271-1_B-Model.mpd", "42042-1.mpd"):
        write(folder, name)
    return folder


def test_pack_is_deterministic_and_sharded_by_set_number(tmp_path, source):
    assert run(tmp_path, "pack") == 0
    first = shard_bytes(tmp_path)
    assert set(first) == {"s00100.tar.xz", "s10200.tar.xz", "s42000.tar.xz"}
    manifest = json.loads((tmp_path / "packed" / "manifest.json").read_text())
    assert [m["file"] for m in manifest["models"]] == sorted(m["file"] for m in manifest["models"])
    assert manifest["models"][0]["sha256"] == hashlib.sha256((source / "10270-1.mpd").read_bytes()).hexdigest()
    assert manifest["models"][0]["licence"].startswith("Redistributable")
    assert run(tmp_path, "pack") == 0
    assert shard_bytes(tmp_path) == first


def test_unpack_restores_bytes_and_is_idempotent(tmp_path, source, capsys):
    original = {p.name: p.read_bytes() for p in source.glob("*.mpd")}
    run(tmp_path, "pack")
    for path in source.glob("*.mpd"):
        path.unlink()
    assert run(tmp_path, "unpack") == 0
    assert {p.name: p.read_bytes() for p in source.glob("*.mpd")} == original
    capsys.readouterr()
    assert run(tmp_path, "unpack") == 0
    assert "is current" in capsys.readouterr().out
    (source / "42042-1.mpd").write_bytes(b"damaged")
    assert run(tmp_path, "verify") == 1
    assert run(tmp_path, "unpack") == 0
    assert run(tmp_path, "verify") == 0


def test_add_rewrites_only_the_affected_shard(tmp_path, source):
    run(tmp_path, "pack")
    before = shard_bytes(tmp_path)
    extra = write(tmp_path / "new", "10272-1.mpd", extra="0 Author: Someone\n")
    assert run(tmp_path, "add", str(extra)) == 0
    after = shard_bytes(tmp_path)
    assert [n for n in after if after[n] != before[n]] == ["s10200.tar.xz"]
    assert (source / "10272-1.mpd").read_bytes() == extra.read_bytes()
    assert run(tmp_path, "verify") == 0


def test_add_requires_a_licence_and_valid_ldraw(tmp_path, source):
    run(tmp_path, "pack")
    bare = tmp_path / "new" / "999-1.mpd"
    bare.parent.mkdir()
    bare.write_text("0 FILE a.ldr\n1 16 0 0 0 1 0 0 0 1 0 0 0 1 3001.dat\n")
    assert run(tmp_path, "add", str(bare)) == 1
    assert run(tmp_path, "add", "--licence", "CC BY 4.0", "--author", "Me", str(bare)) == 0
    entry = next(m for m in json.loads((tmp_path / "packed" / "manifest.json").read_text())["models"] if m["file"] == "999-1.mpd")
    assert (entry["licence"], entry["author"]) == ("CC BY 4.0", "Me")
    junk = tmp_path / "new" / "5-1.mpd"
    junk.write_text("0 FILE a.ldr\nhello\n")
    assert run(tmp_path, "add", "--licence", "x", str(junk)) == 1


def test_shipped_shards_match_the_manifest():
    if not (pack.PACKED / pack.MANIFEST).is_file():
        pytest.skip("no models-packed/ in this checkout")
    assert pack.main(["verify", "--packed-only"]) == 0


def test_reference_models_are_available(reference_models):
    assert (reference_models / "10270-1.mpd").is_file()
