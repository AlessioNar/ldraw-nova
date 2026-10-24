"""Builds: a model project with versions (v1, v2, ...). Chats, the 3D editor and generators are ways to work on one.

    data/builds/<slug>/
        build.json     {"id": "b-<slug>", "title", "created_at", "updated_at", "chats": [chat ids],
                        "source": {"kind": generator|ai|import|editor|empty, "generator"?, "params"?}}
        models.jsonl   one record per saved version (the same records a chat keeps)
        output/        the build's work folder (generator output, editor session)
        editor/        lives under output/ like a chat's

Versions are the files data/generated/<slug>-vN.mpd. A model there with no build folder is a build too: it is
listed from its files alone and gets its build.json the first time something acts on it, so nothing existing is
ever lost or renamed.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import time
from pathlib import Path
from typing import Optional

import gallery
import model_versions
import settings
from leocad_render import list_models
from store import ChatStore

SOURCE_KINDS = ("generator", "ai", "import", "editor", "empty")
ORIGINS = ("generator", "ai", "editor", "import")
VERSION_RE = re.compile(r"^(?P<slug>.+)-v(?P<n>\d+)$", re.IGNORECASE)


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60] or "model"


def slug_of_file(filename: str) -> str:
    """The build a model file belongs to: its name without extension and `-vN` version."""
    stem = Path(filename).stem
    return slugify(re.sub(r"-v\d+$", "", stem, flags=re.IGNORECASE))


def next_version(slug: str) -> int:
    return max((v["version"] for v in versions(slug)), default=0) + 1


def group_versions() -> dict[str, dict[int, Path]]:
    """slug -> {version: model file} for every model in data/generated. An unversioned file (a model dropped into
    the folder) counts as v1 of the build named after it, unless a -v1 exists."""
    groups: dict[str, dict[int, Path]] = {}
    for path in list_models(settings.GENERATED_DIR):
        if path.suffix.lower() not in (".mpd", ".ldr"):
            continue
        match = VERSION_RE.match(path.stem)
        if match:
            groups.setdefault(slugify(match["slug"]), {}).setdefault(int(match["n"]), path)
    for path in list_models(settings.GENERATED_DIR):
        if path.suffix.lower() in (".mpd", ".ldr") and not VERSION_RE.match(path.stem):
            groups.setdefault(slugify(path.stem), {}).setdefault(1, path)
    return groups


def versions(slug: str, groups: Optional[dict] = None) -> list[dict]:
    """[{"version", "path"}] of the build's model files, oldest first."""
    found = (groups if groups is not None else group_versions()).get(slug, {})
    return [{"version": n, "path": found[n]} for n in sorted(found)]


# --- the build record --------------------------------------------------------

def dir_of(store: ChatStore, slug: str) -> Path:
    return store.builds_dir / slug


def valid(store: ChatStore, slug: str) -> bool:
    return bool(slug) and not slug.startswith(".") and store.valid_id(store.build_key(slug)) and slug == slugify(slug)


def read(store: ChatStore, slug: str) -> Optional[dict]:
    try:
        return json.loads((dir_of(store, slug) / "build.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def write(store: ChatStore, slug: str, record: dict) -> None:
    folder = dir_of(store, slug)
    folder.mkdir(parents=True, exist_ok=True)
    tmp = folder / "build.json.tmp"
    tmp.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, folder / "build.json")


def exists(store: ChatStore, slug: str) -> bool:
    return valid(store, slug) and (read(store, slug) is not None or bool(versions(slug)))


def _legacy(store: ChatStore, slug: str) -> dict:
    """The record a build without a build.json would get: title and dates from its files, chats by slug."""
    files = versions(slug)
    paths = [v["path"] for v in files]
    title = ""
    if paths:
        title = gallery.description_of(paths[-1]) or gallery.info_heading_of(paths[-1]) or ""
    mtimes = [p.stat().st_mtime for p in paths if p.exists()] or [time.time()]
    chats = chats_for_slug(store, slug)
    origin = origin_of(store, slug, paths[0]) if paths else None
    kind = {"generator": "generator", "ai": "ai", "editor": "editor", "import": "import"}.get(origin or "", "import")
    return {"id": store.build_key(slug), "title": title or slug.replace("-", " ").capitalize(),
            "created_at": min(mtimes), "updated_at": max(mtimes), "chats": [c["id"] for c in chats],
            "source": {"kind": kind}}


def get(store: ChatStore, slug: str) -> Optional[dict]:
    """The build's record (stored, or synthesised for a legacy model), None when there is no such build."""
    if not valid(store, slug):
        return None
    return read(store, slug) or (_legacy(store, slug) if versions(slug) else None)


def ensure(store: ChatStore, slug: str) -> dict:
    """Store the record of a legacy build the first time something acts on it."""
    record = read(store, slug)
    if record is None:
        record = _legacy(store, slug)
        write(store, slug, record)
    store.work_dir(store.build_key(slug)).mkdir(parents=True, exist_ok=True)
    return record


def unique_slug(store: ChatStore, title: str) -> str:
    base = slugify(title)
    slug, n = base, 1
    while dir_of(store, slug).exists() or versions(slug):
        n += 1
        slug = f"{base[:55]}-{n}"
    return slug


def create(store: ChatStore, title: str = "", source: Optional[dict] = None) -> tuple[str, dict]:
    title = " ".join((title or "").split())[:80] or "Untitled build"
    slug = unique_slug(store, title)
    now = time.time()
    record = {"id": store.build_key(slug), "title": title, "created_at": now, "updated_at": now, "chats": [],
              "source": source or {"kind": "empty"}}
    write(store, slug, record)
    store.work_dir(store.build_key(slug)).mkdir(parents=True, exist_ok=True)
    return slug, record


def update(store: ChatStore, slug: str, **fields) -> dict:
    record = ensure(store, slug)
    record.update({k: v for k, v in fields.items() if k in ("title", "source", "chats")})
    record["updated_at"] = time.time()
    write(store, slug, record)
    return record


def touch(store: ChatStore, slug: str) -> None:
    if read(store, slug) is not None:
        update(store, slug)


def attach_chat(store: ChatStore, slug: str, chat_id: str) -> None:
    record = ensure(store, slug)
    if chat_id not in record["chats"]:
        update(store, slug, chats=[*record["chats"], chat_id])


def record_version(store: ChatStore, slug: str, title: str, model: Path, origin: str, warnings: Optional[list] = None,
                   source: Optional[dict] = None) -> dict:
    """Note a saved version in the build's models.jsonl and bring its record up to date."""
    key = store.build_key(slug)
    ensure(store, slug)
    ref = store.add_model(key, title, model, warnings or [], origin=origin)
    fields = {"source": source} if source else {}
    update(store, slug, **fields)
    return ref


# --- listing -----------------------------------------------------------------

class Index:
    """One pass over the chats: which chat published which model file, and which chats touch which build."""

    def __init__(self, store: ChatStore):
        self.store = store
        self.chats = store.list_chats()
        self.owners: dict[Path, tuple[dict, dict]] = {}
        self.by_slug: dict[str, dict[str, dict]] = {}
        for chat in self.chats:
            if chat.get("build_id"):
                self.by_slug.setdefault(chat["build_id"], {})[chat["id"]] = chat
            for ref in store.models(chat["id"]):
                path = store.resolve(chat["id"], ref["model"])
                self.owners.setdefault(path, (chat, ref))
                self.by_slug.setdefault(slug_of_file(path.name), {})[chat["id"]] = chat

    def chats_of(self, slug: str) -> list[dict]:
        found = dict(self.by_slug.get(slug, {}))
        for chat_id in (read(self.store, slug) or {}).get("chats", []):
            chat = self.store.get_chat(chat_id)
            if chat:
                found[chat_id] = chat
        return sorted(found.values(), key=lambda c: c["updated_at"], reverse=True)


def chats_for_slug(store: ChatStore, slug: str) -> list[dict]:
    return Index(store).chats_of(slug)


def all_slugs(store: ChatStore) -> list[str]:
    slugs = set()
    if store.builds_dir.is_dir():
        for p in store.builds_dir.iterdir():
            if p.is_dir() and valid(store, p.name) and (p / "build.json").is_file():
                slugs.add(p.name)
    return sorted(slugs | set(group_versions()))


def origin_of(store: ChatStore, slug: str, path: Path, index: Optional[Index] = None) -> str:
    """generator | ai | editor | import: from the build's own record of the version, else the chats that made it."""
    key = store.build_key(slug)
    for ref in store.models(key):
        if store.resolve(key, ref["model"]) == path and ref.get("origin") in ORIGINS:
            return ref["origin"]
    owner = (index or Index(store)).owners.get(path)
    if owner:
        return "editor" if owner[1].get("origin") == "editor" else "ai"
    return "import"


def version_rows(store: ChatStore, slug: str, with_changes: bool = True, index: Optional[Index] = None) -> list[dict]:
    from routers.common import model_info                      # lazy: routers import this module
    index = index or Index(store)
    key = store.build_key(slug)
    refs = {store.resolve(key, r["model"]): r for r in store.models(key)}
    rows, previous = [], None
    for entry in versions(slug):
        path = entry["path"]
        info = model_info(path)
        ref = refs.get(path)
        owner = index.owners.get(path)
        origin = ref.get("origin") if ref and ref.get("origin") in ORIGINS else None
        if origin is None and owner:
            origin = "editor" if owner[1].get("origin") == "editor" else "ai"
        rows.append({**info, "version": entry["version"], "origin": origin or "import",
                     "title": info["description"] or info["info_heading"] or slug,
                     "created_at": (ref or {}).get("created_at") or info["mtime"],
                     "chat": {"id": owner[0]["id"], "title": owner[0]["title"]} if owner else None,
                     "changes": model_versions.diff(previous, path) if with_changes and previous else None})
        previous = path
    return rows


def summary(store: ChatStore, slug: str, record: dict, index: Index, groups: dict) -> dict:
    from routers.common import is_pending, model_info
    files = versions(slug, groups)
    latest = {**model_info(files[-1]["path"]), "version": files[-1]["version"]} if files else None
    updated = max([record["updated_at"], *([latest["mtime"]] if latest else [])])
    return {"id": slug, "key": store.build_key(slug), "title": record["title"], "created_at": record["created_at"],
            "updated_at": updated, "source": record.get("source") or {"kind": "empty"}, "versions": len(files),
            "latest": latest, "chats": len(index.chats_of(slug)),
            "pending": bool(latest) and is_pending(latest)}


def older_chats(store: ChatStore) -> list[dict]:
    """Chats not linked to any build: no build_id and no published model that belongs to a build."""
    slugs = set(all_slugs(store))
    result = []
    for chat in store.list_chats():
        if chat.get("build_id"):
            continue
        refs = store.models(chat["id"])
        if any(slug_of_file(Path(ref["model"]).name) in slugs for ref in refs
               if store.resolve(chat["id"], ref["model"]).is_file()):
            continue
        result.append(chat)
    return result


def chat_build(store: ChatStore, chat: dict) -> Optional[str]:
    """The build a chat belongs to: attached, else the slug of its latest published model that still exists."""
    if chat.get("build_id"):
        return chat["build_id"]
    for ref in reversed(store.models(chat["id"])):
        path = store.resolve(chat["id"], ref["model"])
        if path.is_file():
            return slug_of_file(path.name)
    return None


def copy_into_generated(store: ChatStore, slug: str, source: Path, suffix: str = ".mpd") -> Path:
    """Reserve the next version file for the build and copy `source` there."""
    settings.GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    target = settings.GENERATED_DIR / f"{slug}-v{next_version(slug)}{suffix}"
    shutil.copyfile(source, target)
    return target
