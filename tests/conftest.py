import importlib.util
from pathlib import Path

import pytest
from ldraw import Parts

from ldraw_tools.common import get_parts, library_path, models_path

MODELS_PACK = Path(__file__).resolve().parents[1] / "tools" / "models_pack.py"


def load_models_pack():
    """tools/models_pack.py as a module, or None where the checkout lacks it (the image)."""
    if not MODELS_PACK.is_file():
        return None
    spec = importlib.util.spec_from_file_location("models_pack", MODELS_PACK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def reference_models():
    """data/models-annotated/, unpacked from models-packed/ on first use (a fresh clone has only the shards)."""
    root = models_path()
    pack = load_models_pack()
    if pack is not None and (pack.PACKED / pack.MANIFEST).is_file():
        assert pack.main(["unpack"]) == 0
    if not any(root.glob("*.mpd")):
        pytest.skip("reference models unavailable: run tools/models_pack.py unpack")
    return root


@pytest.fixture(scope="session")
def parts(tmp_path_factory):
    root = tmp_path_factory.mktemp("library")
    (root / "parts").mkdir()
    (root / "p").mkdir()
    (root / "parts.lst").write_text("3001.dat Brick 2 x 4\n3003.dat Brick 2 x 2\n3022.dat Plate 2 x 2\n")
    (root / "LDConfig.ldr").write_text("\n".join(
        f"0 !COLOUR {name} CODE {code} VALUE #FFFFFF EDGE #333333"
        for code, name in [(0,"Black"),(1,"Blue"),(4,"Red"),(14,"Yellow"),(16,"Main_Colour"),(24,"Edge_Colour")]))
    for code, x, height in [("3001",40,24),("3003",20,24),("3022",20,8)]:
        (root / f"parts/{code}.dat").write_text(
            f"0 Brick\n0 !LDRAW_ORG Part\n4 16 {-x} 0 -20 {x} 0 -20 {x} 0 20 {-x} 0 20\n"
            f"4 16 {-x} {height} -20 {-x} {height} 20 {x} {height} 20 {x} {height} -20\n")
    return Parts(root / "parts.lst")


@pytest.fixture(scope="session")
def official():
    try:
        library = library_path()
    except ValueError:
        pytest.skip("Official integration library unavailable; set LDRAW_DIR or LDRAWDIR")
    if not (library / "parts").is_dir():
        pytest.skip("Official integration library unavailable; set LDRAW_DIR or LDRAWDIR")
    return get_parts()


def mpd(body, name="main.ldr"):
    return f"0 FILE {name}\n0 Test model\n0 Name: {name}\n0 Author: Tests\n0 !LDRAW_ORG Model\n{body}\n0 NOFILE\n".replace("\n", "\r\n")


IDENTITY = "1 0 0 0 1 0 0 0 1"


def ref(part="3001.dat", colour=4, position="0 0 0", matrix=IDENTITY):
    return f"1 {colour} {position} {matrix} {part}"
