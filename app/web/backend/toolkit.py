"""Container integration for the unchanged, standalone ldraw-nova checkout."""
from __future__ import annotations

from pathlib import Path

import settings
import sandbox
import environment_config


# Mandatory, category-independent foundations. Supply these in full before the
# first model call; partial tool reads were skipping design and review rules.
# Subject-specific guides and the CLI reference remain available through tools.
BUILDER_GUIDES = (
    "docs/agent/ldraw-reference.md",
    "docs/agent/geometry.md",
    "docs/agent/visual-design.md",
    "docs/agent/reference-discovery.md",
    "docs/agent/validation.md",
)


# Without a Jev key the Jev procedure is dead weight in every prompt.
KEYWORD_GUIDE = {"docs/agent/reference-discovery.md": "docs/agent/reference-discovery-fts.md"}

# OpenRouter serves TypeSafe's System One API, so jev-rerank's SDK only needs a new base URL and model id.
OPENROUTER_JEV = {"TYPESAFE_BASE_URL": "https://openrouter.ai/api", "TYPESAFE_DEFAULT_MODEL": "typesafe/jev-1.13"}


def jev_environment() -> dict[str, str]:
    """Variables jev-rerank needs: a TypeSafe key, else the OpenRouter key routed to OpenRouter; {} means keyword search."""
    saved = environment_config.snapshot()
    if key := saved.get("TYPESAFE_API_KEY", "").strip():
        return {"TYPESAFE_API_KEY": key}
    if key := saved.get("OPENROUTER_API_KEY", "").strip():
        return {"TYPESAFE_API_KEY": key, **OPENROUTER_JEV}
    return {}


def root() -> Path:
    path = settings.TOOLKIT_DIR
    if not (path / "instructions.md").is_file() or not (path / ".venv/bin/python").exists():
        raise ValueError("LDraw toolkit is missing. Rebuild with docker compose up -d --build from the app/ directory.")
    return path


def workspace(store, chat_id: str) -> Path:
    """A repository-shaped cwd; source resources stay shared and read-only.

    ROOT in the upstream Python package resolves to the installed checkout,
    preserving its data paths and cache. Relative output paths are chat-local.
    """
    source = root()
    cwd = store.chat_dir(chat_id) / "workspace"
    cwd.mkdir(parents=True, exist_ok=True)
    output = store.work_dir(chat_id)
    output.mkdir(parents=True, exist_ok=True)
    sandbox.give_to_agent(output)
    for entry in source.iterdir():
        if entry.name in {"output", ".git", ".env"} or entry.name.startswith(".env."):
            continue
        link = cwd / entry.name
        if not link.exists() and not link.is_symlink():
            link.symlink_to(entry, target_is_directory=entry.is_dir())
    link = cwd / "output"
    if not link.exists() and not link.is_symlink():
        link.symlink_to(output, target_is_directory=True)
    return cwd


def environment() -> dict[str, str]:
    source = root()
    env = environment_config.snapshot()
    return {
        "PATH": f"{source / '.venv/bin'}:{sandbox.SCRIPTS_DIR}:/usr/local/bin:/usr/bin:/bin",
        # Avoid the backend's old ldraw.py shadowing pyldraw3's `ldraw` package.
        "PYTHONPATH": f"{source}:/app",
        # Rosetta can create a root-owned ~/.cache before privileges drop on
        # Apple Silicon. Use the builder's explicitly writable derived cache.
        "XDG_CACHE_HOME": str(source / ".cache"),
        "PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1",
        "LDRAW_DIR": env.get("LDRAW_DIR") or env.get("LDRAWDIR") or str(settings.LDRAW_DIR),
        "LDRAW_NOVA_INVENTORY_DIR": str(settings.INVENTORIES_DIR),
    }


def instructions() -> str:
    return (root() / "instructions.md").read_text()


def guide_paths() -> list[str]:
    # Chosen by the configured key alone, so it is stable within a chat and
    # changes only when the owner adds or removes the key.
    keyword_only = not jev_environment()
    return [KEYWORD_GUIDE.get(path, path) if keyword_only else path for path in BUILDER_GUIDES]


def guide_index() -> str:
    """The foundation guides as a list (path and title), for the lean prompt that does not inline them."""
    source = root()
    lines = []
    for path in guide_paths():
        title = next((l.lstrip("# ").strip() for l in (source / path).read_text().splitlines() if l.startswith("#")), path)
        lines.append(f"- {path}: {title}")
    return "\n".join(lines)


def builder_guides() -> str:
    source = root()
    paths = guide_paths()
    return "\n\n".join(f"--- {path} (complete) ---\n{(source / path).read_text()}" for path in paths)
