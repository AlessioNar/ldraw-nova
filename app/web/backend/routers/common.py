"""Helpers shared by the routers: 404s and how a model file is described to the UI."""
from __future__ import annotations

from pathlib import Path
from typing import Optional
from urllib.parse import quote

from fastapi import HTTPException

import gallery
import settings
from leocad_render import bom_path_for, snapshot_path_for
from paths import rel_to
from store import ChatStore


def not_found(what: str = "not found"):
    raise HTTPException(status_code=404, detail=what)



def file_url(path: Path, versioned: bool = False) -> Optional[str]:
    """URL of an existing file the web UI may load, else None: /files/... in a
    web-visible folder of /data (generated/, chats/). data/output is agent-only: never served.

    `versioned` adds the file's mtime, for images that can be re-rendered under
    the same name: browsers reuse an image already on the page by URL alone."""
    try:
        rel = rel_to(path, settings.DATA_DIR)
    except ValueError:
        return None
    if rel.split("/", 1)[0] not in settings.WEB_DIRS:
        return None
    url = "/files/" + quote(rel)
    if not path.is_file():
        return None
    return f"{url}?v={path.stat().st_mtime_ns}" if versioned else url


def model_info(path: Path) -> dict:
    """A model file in the collection, as the UI sees it: its snapshot (status,
    image_url), BOM (bom_status, bom_url), part count (from the BOM), notes
    (info_url and info_heading, from its .md)."""
    exists = path.is_file()
    status, error = gallery.status_of(path, "snapshot") if exists else ("missing", None)
    bom_status, bom_error = gallery.status_of(path, "bom") if exists else ("missing", None)
    stat = path.stat() if exists else None
    return {
        "file": path.name, "name": path.stem, "description": gallery.description_of(path) if exists else "",
        "model_url": file_url(path), "image_url": file_url(snapshot_path_for(path), versioned=True),
        "bom_url": file_url(bom_path_for(path), versioned=True), "parts": gallery.part_count(path) if exists else None,
        "info_url": file_url(gallery.info_path_for(path), versioned=True),
        "info_heading": gallery.info_heading_of(path) if exists else None,
        "size": stat.st_size if stat else 0, "mtime": stat.st_mtime if stat else 0,
        "status": status, "error": error, "bom_status": bom_status, "bom_error": bom_error,
    }


def is_pending(info: dict) -> bool:
    return info["status"] in ("queued", "rendering") or info["bom_status"] in ("queued", "rendering")


def chat_models(store: ChatStore, chat_id: str) -> dict[str, dict]:
    """The models a chat produced (references into data/generated), keyed by id."""
    result = {}
    for ref in store.models(chat_id):
        path = store.resolve(chat_id, ref["model"])
        result[ref["id"]] = {**model_info(path), "id": ref["id"], "name": ref["name"],
                             "warnings": ref.get("warnings", []), "created_at": ref["created_at"]}
    return result


def model_chat_index(store: ChatStore) -> dict[Path, list[dict]]:
    """model path -> the chats that produced it."""
    index: dict[Path, list[dict]] = {}
    for chat in store.list_chats():
        for ref in store.models(chat["id"]):
            chats = index.setdefault(store.resolve(chat["id"], ref["model"]), [])
            if not any(c["id"] == chat["id"] for c in chats):
                chats.append({"id": chat["id"], "title": chat["title"]})
    return index
