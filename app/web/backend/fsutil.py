"""Small shared helpers of the collection storage (no imports of other backend modules, so nothing can cycle)."""
from __future__ import annotations

import tempfile
import threading
from pathlib import Path

lock = threading.Lock()                     # check-then-write of ids and names; the collection store's lock too


def atomic_write(path: Path, data: bytes) -> None:
    """Temp file in the same folder, then rename: readers never see a partial inventory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".", suffix=".tmp", delete=False) as f:
            tmp = Path(f.name)
            f.write(data)
        tmp.chmod(0o644)
        tmp.replace(path)
        tmp = None
    finally:
        if tmp:
            tmp.unlink(missing_ok=True)
