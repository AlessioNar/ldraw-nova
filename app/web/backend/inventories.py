"""STAND-IN, replaced by the parts-api branch: how a turn's options select the user's parts inventories."""
from __future__ import annotations

PARTS_MODES = ("off", "prefer", "only")


def parts_options(options: dict | None) -> tuple[str, list[str]]:
    """(mode, inventory ids) of a turn; mode is "off" unless a known mode comes with at least one inventory."""
    options = options or {}
    ids = [i for i in options.get("inventory_ids") or [] if isinstance(i, str)]
    mode = options.get("parts_mode")
    return (mode, ids) if mode in PARTS_MODES and mode != "off" and ids else ("off", [])
