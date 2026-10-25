"""Tests run inside the container (they use the baked LDraw library, indexes and
LeoCAD):

    docker compose exec ldraw-nova-app bash -c \
        "pip install -q --break-system-packages -r /app/web/backend/requirements-dev.txt && cd /app/web/backend && pytest -q"

/data and /config are redirected to temp folders before any app module is imported.
"""
import os
import sys
import tempfile
from pathlib import Path

_tmp = Path(tempfile.mkdtemp(prefix="ldraw-nova-web-tests-"))
os.environ["LDRAW_NOVA_DATA_DIR"] = str(_tmp / "data")
os.environ["LDRAW_NOVA_WEB_CONFIG_DIR"] = str(_tmp / "config")
os.environ.setdefault("LDRAW_NOVA_ALLOWED_HOSTS", "testserver")      # Starlette TestClient's Host
os.environ["LDRAW_NOVA_GLB_CACHE_DIR"] = str(_tmp / "glb-cache")
(_tmp / "data" / "output").mkdir(parents=True)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))          # web/backend
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))          # repo root: leocad_render

import pytest  # noqa: E402

import settings  # noqa: E402
import tools  # noqa: E402
from store import ChatStore, get_store  # noqa: E402


@pytest.fixture(autouse=True)
def offline_model_details(monkeypatch):
    """Unit tests never depend on live catalogues or send provider credentials."""
    import model_discovery
    async def unavailable(*args, **kwargs):
        raise RuntimeError("Catalogue offline in tests")
    monkeypatch.setattr(model_discovery, "_fetch_json", unavailable)


@pytest.fixture(autouse=True)
def legacy_layout(monkeypatch):
    """The prompt layout most tests were written for: tail in the system turn, plain `ephemeral` markers, full
    guides, no masking. The new defaults (test_token_savings.py) opt back in by undoing this."""
    monkeypatch.setattr(settings, "TAIL_AFTER_HISTORY", False)
    monkeypatch.setattr(settings, "CACHE_TTL", "5m")
    monkeypatch.setattr(settings, "LEAN_PROMPT", "off")
    monkeypatch.setenv("LDRAW_NOVA_MASK_OLD_TOOL_OUTPUT", "0")


@pytest.fixture
def data_dir() -> Path:
    return _tmp / "data"


@pytest.fixture
def store() -> ChatStore:
    return ChatStore(settings.CHATS_DIR, settings.OUTPUT_DIR)


@pytest.fixture
def ctx():
    """A tool context on a fresh chat of the shared store."""
    # Match /data's traversable ancestors while /config remains private.
    os.chmod(settings.DATA_DIR.parent, 0o755)
    store = get_store()
    chat = store.create_chat()
    return tools.ToolContext(chat["id"], store, lambda *_: None)


# ---------------------------------------------------------------- PostgreSQL (optional)
# Tests that need the database ask for `database`; they skip when LDRAW_NOVA_TEST_DATABASE_URL is unset. It points at
# a throwaway server (a superuser on database `postgres`), e.g.
#   docker run -d --name obd-pg-testdb --tmpfs /var/lib/postgresql -e POSTGRES_PASSWORD=test postgres:18-alpine
#   LDRAW_NOVA_TEST_DATABASE_URL=postgresql://postgres:test@obd-pg-testdb:5432/postgres
# The schema is built once per session by Alembic into a template database; each test gets the same copy, emptied
# (TRUNCATE) between tests.
TEST_DATABASE_URL = os.environ.get("LDRAW_NOVA_TEST_DATABASE_URL", "").strip()


def _server_url():
    from sqlalchemy.engine import make_url
    url = make_url(TEST_DATABASE_URL)
    return url.set(drivername="postgresql+psycopg")


@pytest.fixture(scope="session")
def pg_admin():
    """An autocommit engine on the throwaway server, for creating and dropping databases."""
    if not TEST_DATABASE_URL:
        pytest.skip("LDRAW_NOVA_TEST_DATABASE_URL is not set (no test PostgreSQL)")
    from sqlalchemy import create_engine
    engine = create_engine(_server_url(), isolation_level="AUTOCOMMIT")
    yield engine
    engine.dispose()


def create_database(admin, name: str, template: str | None = None):
    from sqlalchemy import text
    with admin.connect() as connection:
        connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        connection.execute(text(f'CREATE DATABASE "{name}"' + (f' TEMPLATE "{template}"' if template else "")))
    return _server_url().set(database=name)


def drop_database(admin, name: str):
    from sqlalchemy import text
    with admin.connect() as connection:
        connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


@pytest.fixture(scope="session")
def pg_template(pg_admin):
    """A database migrated to head by Alembic, used as the template of every test database."""
    from sqlalchemy import create_engine
    from db import migrate
    name = f"obd_tpl_{os.getpid()}"
    url = create_database(pg_admin, name)
    engine = create_engine(url)
    migrate.upgrade(engine)
    engine.dispose()
    yield name
    drop_database(pg_admin, name)


@pytest.fixture(scope="session")
def pg_test_url(pg_admin, pg_template):
    name = f"obd_test_{os.getpid()}"
    yield create_database(pg_admin, name, template=pg_template)
    import db
    db.reset()
    drop_database(pg_admin, name)


@pytest.fixture
def database(pg_test_url):
    """The app's database layer pointed at the test database, with every table empty."""
    import db
    from sqlalchemy import text
    from db.models import metadata
    db.configure(pg_test_url)
    names = ", ".join(f'"{t.name}"' for t in metadata.sorted_tables)
    with db.session_scope() as session:
        session.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
    yield db
    db.reset()


@pytest.fixture
def fresh_database(pg_admin):
    """An empty database (no schema) for migration tests: yields its URL, dropped afterwards."""
    name = f"obd_empty_{os.getpid()}"
    yield create_database(pg_admin, name)
    drop_database(pg_admin, name)
