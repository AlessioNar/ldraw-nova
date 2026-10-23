"""The shipped catalogue database is read by an unprivileged user from a root-owned folder."""
import sqlite3

from ldraw_tools.common import readonly_uri


def wal_database(tmp_path):
    path = tmp_path / "shipped.db"
    con = sqlite3.connect(path)                          # explicit close: `with` only commits, and an open connection owns the -wal
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE t (x TEXT)")
    con.execute("INSERT INTO t VALUES ('hello')")
    con.commit()
    con.close()
    for leftover in tmp_path.glob("shipped.db-*"):       # as shipped: no -wal or -shm next to it
        leftover.unlink()
    return path


def test_reading_with_the_helper_creates_nothing_beside_a_wal_database(tmp_path):
    path = wal_database(tmp_path)
    assert path.read_bytes()[18:20] == b"\x02\x02"          # really in WAL mode
    with sqlite3.connect(readonly_uri(path), uri=True) as con:
        assert con.execute("SELECT x FROM t").fetchone() == ("hello",)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["shipped.db"]      # no -shm, no -wal: nothing to be denied


def test_plain_read_only_mode_does_create_a_shm_file(tmp_path):
    """The behaviour the helper avoids: mode=ro alone writes -shm, which an unprivileged user cannot do there."""
    path = wal_database(tmp_path)
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as con:
        con.execute("SELECT x FROM t").fetchone()
        assert any(p.name.endswith("-shm") for p in tmp_path.iterdir())
