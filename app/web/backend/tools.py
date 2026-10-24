"""Tools the agents can call. Schemas are OpenAI function-calling format,
which LiteLLM translates for every provider.

Where things go:
  /data/generated/        finished models (flat) + their .png snapshots: the My Builds page
  /data/output/<chat>/    the chat's work folder: scripts' cwd, notes, plans, scratch files
  /data/chats/<chat>/renders/   extra renders shown in the chat
"""
from __future__ import annotations

import inspect
import hashlib
import json
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable
from urllib.parse import quote

import sandbox
import settings
import toolkit
import builds
import gallery
import parts_steering
from budget import BudgetLedger, WorkerLimits
from leocad_render import bom_path_for, snapshot_path_for
from paths import safe_join
import store as store_module
from store import ChatStore

MAX_WRITE_BYTES = 2 * 1024 * 1024


class ToolError(Exception):
    """An error message meant for the agent (it can fix its call and retry)."""


@dataclass
class ToolContext:
    chat_id: str
    store: ChatStore
    emit: Callable[[str, dict], None]
    options: dict = field(default_factory=dict)           # the turn's validated options (parts steering, ...)
    write_root: Path | None = None                        # a worker's own folder: file tools write only here (planner: None)
    ledger: BudgetLedger = field(default_factory=BudgetLedger)   # the run's shared budget (workers reserve from it)
    limits: WorkerLimits = field(default_factory=WorkerLimits)   # the turn's worker semaphore, delegation count, stray guard
    origin: str | None = None                             # "editor" when the person publishes from the 3D editor

    @property
    def work_dir(self) -> Path:
        return self.store.work_dir(self.chat_id)

    @property
    def chat_dir(self) -> Path:
        return self.store.chat_dir(self.chat_id)


@dataclass
class ToolResult:
    content: str                                          # what the LLM sees
    models: list[dict] = field(default_factory=list)      # model references added to the chat
    images: list[Path] = field(default_factory=list)      # PNGs to show (and send to vision models)
    parts_check: dict | None = None                       # inventory check of a published model, stored with the tool message
    record: dict = field(default_factory=dict)            # extra fields for the tool message (a worker's `_usage`, `_worker`)


# --- helpers ---------------------------------------------------------------

_slug = builds.slugify
_next_version = builds.next_version


def resolve_path(ctx: ToolContext, path: str, *, write: bool = False) -> Path:
    """Paths the agent may use. Reading: the model collection and its own work
    folder. Writing (write_file): only its work folder.
    Relative paths are relative to the work folder."""
    path = (path or "").strip()
    roots = [ctx.work_dir] if write else [ctx.work_dir, settings.GENERATED_DIR, settings.TOOLKIT_DIR]
    if any(p.startswith(".") for p in Path(path).parts if p not in (".", "..")):
        raise ToolError("Hidden configuration and runtime files are not accessible through file tools")
    base = ctx.write_root or ctx.work_dir               # a worker's relative paths are relative to its own folder
    if path.startswith("output/") or path == "output":
        path = str(ctx.work_dir / path.removeprefix("output").lstrip("/"))
    elif not write and not path.startswith("/") and (settings.TOOLKIT_DIR / path).exists():
        path = str(settings.TOOLKIT_DIR / path)
    if path.startswith("/"):
        for root in roots:
            if path == str(root) or path.startswith(str(root) + "/"):
                resolved = safe_join(root, path[len(str(root)):])
                break
        else:
            raise ToolError(f"{path} is outside the allowed folders: {', '.join(map(str, roots))}")
    else:
        resolved = safe_join(base, path)
    if resolved is None:
        raise ToolError(f"{path} escapes the allowed folders")
    if write and ctx.write_root is not None:
        root = ctx.write_root.resolve()
        if resolved != root and root not in resolved.parents:
            raise ToolError(f"{path} is outside your write folder; write only inside {ctx.write_root}")
    return resolved


async def _run_and_collect(ctx: ToolContext, argv: list[str], timeout: int, cwd: Path | None = None) -> ToolResult:
    result = await run_command(ctx, argv, timeout, cwd)
    return ToolResult(result.as_text())


async def run_command(ctx: ToolContext, argv: list[str], timeout: int, cwd: Path | None = None) -> sandbox.RunResult:
    env = toolkit.environment()
    jev = toolkit.jev_environment()
    env.update(jev)
    key = jev.get("TYPESAFE_API_KEY", "")
    workspace = toolkit.workspace(ctx.store, ctx.chat_id)       # also links output/ and the toolkit
    return await sandbox.run(argv, cwd or workspace, timeout=max(1, min(timeout, 1800)),
                             env_extra=env, secrets=(key,),
                             on_output=lambda channel, text: ctx.emit("tool_output", {"channel": channel, "delta": text}))


async def t_run_python(ctx: ToolContext, code: str, timeout: int = 60) -> ToolResult:
    # A worker's script and cwd are its own folder. This only steers relative paths: the process can still open
    # absolute paths under output/, which the stray-write guard (worker_isolation) detects and undoes afterwards.
    scripts = (ctx.write_root or ctx.work_dir) / "generators"
    scripts.mkdir(parents=True, exist_ok=True)
    script = scripts / f"run-{time.strftime('%Y%m%d-%H%M%S')}-{int(time.time() * 1000) % 1000:03d}.py"
    script.write_text(code)
    sandbox.give_to_agent(scripts)
    sandbox.give_to_agent(script)
    result = await _run_and_collect(ctx, ["python3", str(script)], timeout, ctx.write_root)
    result.content += f"\nSaved generator: {artifact_url(ctx, script)}"
    return result


async def t_run_shell(ctx: ToolContext, command: str, timeout: int = 60) -> ToolResult:
    return await _run_and_collect(ctx, ["bash", "-o", "pipefail", "-c", command], timeout)


async def t_run_toolkit(ctx: ToolContext, arguments: list[str], timeout: int = 300) -> ToolResult:
    if not arguments or not all(isinstance(a, str) and "\0" not in a for a in arguments):
        raise ToolError("arguments must be a nonempty array of CLI argument strings")
    return await _run_and_collect(ctx, ["./ldraw-agent", *arguments], timeout)


async def t_report_progress(ctx: ToolContext, summary: str) -> ToolResult:
    summary = summary.strip()[:2000]
    ctx.emit("progress", {"summary": summary})
    return ToolResult(summary)


async def t_view_image(ctx: ToolContext, path: str) -> ToolResult:
    source = resolve_path(ctx, path)
    if not source.is_file() or source.stat().st_size > 16 * 1024 * 1024:
        raise ToolError("Choose an existing PNG smaller than 16 MB")
    data = source.read_bytes()
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ToolError("view_image accepts PNG renders")
    target = ctx.chat_dir / "renders" / (hashlib.sha256(data).hexdigest()[:24] + ".png")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return ToolResult(f"Opened {path} for visual review.", images=[target])


def artifact_url(ctx: ToolContext, path: Path) -> str:
    return f"/api/chats/{ctx.chat_id}/artifacts/" + quote(path.relative_to(ctx.work_dir).as_posix())


async def t_publish_model(ctx: ToolContext, path: str, name: str | None = None) -> ToolResult:
    source = resolve_path(ctx, path, write=True)
    if not source.is_file() or source.suffix.lower() not in {".mpd", ".ldr"}:
        raise ToolError("Publish a self-contained .mpd or .ldr from this chat's output folder")
    if source.stat().st_size > 32 * 1024 * 1024:
        raise ToolError("Model exceeds the 32 MB publication limit")
    if not any(line.lstrip().startswith("1 ") for line in source.read_text(encoding="utf-8", errors="replace").splitlines()):
        raise ToolError("Add at least one brick before saving a version")
    review = ctx.work_dir / "publication" / uuid.uuid4().hex[:12]
    review.mkdir(parents=True)
    sandbox.give_to_agent(review.parent)
    sandbox.give_to_agent(review)
    # Validate and render the same captured revision even if another tool or
    # the user edits the working source while publication is running.
    source_bytes = source.read_bytes()
    revision = review / "model.mpd"
    revision.write_bytes(source_bytes)
    report = review / "validation.json"
    ctx.emit("progress", {"summary": "Checking the model before publication."})
    validation = await run_command(ctx, ["./ldraw-agent", "validate", str(revision), "--geometry",
                                         "--detail", "summary", "--report", str(report)], 1800)
    if validation.exit_code not in (0, 1) or not report.is_file():
        return ToolResult("Error: toolkit validation could not finish; model was not published.\n" + validation.as_text())
    warnings = ["Physical buildability is not proven; read the validation and visual review reports."]
    if validation.exit_code == 1:
        warnings.append("Toolkit validation failed. This revision is for inspection and needs repair.")
    chat = None if ctx.store.is_build(ctx.chat_id) else ctx.store.get_chat(ctx.chat_id)
    attached = (chat or {}).get("build_id")          # a chat attached to a build publishes into that build
    if attached:
        return _propose(ctx, attached, source_bytes, name or source.stem, warnings, validation.exit_code == 0)
    slug = _slug(name or source.stem)
    settings.GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    # Reserve synchronously before the next await, including concurrent chats.
    target = settings.GENERATED_DIR / f"{slug}-v{_next_version(slug)}.mpd"
    target.write_bytes(source_bytes)
    ctx.emit("progress", {"summary": "Rendering the published model and exporting its parts list."})
    gallery.publishing.add(target.resolve())
    try:
        rendered = await run_command(ctx, ["./ldraw-agent", "render", str(target), "--outdir", str(review),
                                          "--views", "home"], 600)
    finally:
        gallery.publishing.discard(target.resolve())
    image, bom = review / "home.png", review / "leocad-bom.csv"
    if rendered.exit_code == 0 and image.exists() and bom.exists():
        shutil.copyfile(image, snapshot_path_for(target))
        shutil.copyfile(bom, bom_path_for(target))
    else:
        warnings.append("Preview/BOM rendering failed; the model can still be opened in 3D.")
    ref = ctx.store.add_model(ctx.chat_id, name or source.stem, target, warnings, origin=ctx.origin,
                              source=source.relative_to(ctx.work_dir).as_posix() if ctx.work_dir in source.parents else None)
    if ctx.store.is_build(ctx.chat_id):
        builds.touch(ctx.store, ctx.chat_id[len(store_module.BUILD_PREFIX):])
    ctx.emit("model", {"id": ref["id"], "name": ref["name"]})
    model_url = "/files/generated/" + quote(target.name)
    result = {"model_url": model_url, "source": artifact_url(ctx, source),
              "card_url": f"/chat/{ctx.chat_id}#model-{ref['id']}",
              "viewer_url": "/viewer/viewer.html?model=" + quote(model_url, safe=""),
              "download_url": model_url + "?download=1",
              "sha256": hashlib.sha256(source_bytes).hexdigest(),
              "validation": artifact_url(ctx, report), "checks_passed": validation.exit_code == 0,
              "validation_path": str(report),
              "physical_validity": "not_proven", "warnings": warnings,
              "note": "Open the preview with view_image and complete visual review and compare-bom before final delivery."}
    if image.exists():
        result["preview"] = artifact_url(ctx, image)
        result["preview_path"] = str(image)
    if bom.exists():
        result["bom"] = artifact_url(ctx, bom)
        result["bom_path"] = str(bom)
    content = json.dumps(result, indent=2)
    check, note = await parts_check(ctx, revision)
    return ToolResult(f"{content}\n\n{note}" if note else content, models=[ref], parts_check=check)


def _propose(ctx: ToolContext, slug: str, model: bytes, name: str, warnings: list, checks_passed: bool) -> ToolResult:
    """A model published by a chat attached to a build is not a version: it waits in the build as a proposal
    the person keeps in the draft, previews or discards (docs/plans/build-workspace/CONTRACT.md)."""
    import usage
    import workspace
    from routers import editor
    builds.ensure(ctx.store, slug)
    key = ctx.store.build_key(slug)
    session = editor._session(ctx.store, key)
    files = builds.versions(slug)
    base = session[2] if session else (files[-1]["path"].read_text(encoding="utf-8", errors="replace") if files else "")
    cost = usage.usage_totals(ctx.store.messages(ctx.chat_id))["cost_eur"]
    record = workspace.create_proposal(ctx.store, slug, model.decode("utf-8", errors="replace"), ctx.chat_id, base, cost,
                                       warnings)
    s = record["summary"]
    message = (f"The change is waiting for review in the build (proposal {record['id']}: {s['added']} added, "
               f"{s['removed']} removed, {s['moved']} moved). It is NOT a new version: the person keeps it in their draft, "
               "previews it or discards it. Say in your final message that the change is waiting for review in the build.")
    ctx.emit("proposal", {"id": record["id"], "build": slug})
    result = {"proposal": record["id"], "build": slug, "summary": s, "checks_passed": checks_passed,
              "warnings": warnings, "message": message}
    return ToolResult(json.dumps(result, indent=2))


async def parts_check(ctx: ToolContext, model: Path) -> tuple[dict | None, str]:
    """Compare a published model with the chat's inventories: (payload for the UI, text for the agent).

    Never fails the publication: a check that cannot run only leaves a one-line note.
    """
    mode, invs = parts_steering.selection(ctx.options)
    if mode == "off":
        return None, ""
    ids = parts_steering.id_list(invs)
    try:
        if not invs:
            raise ValueError("none of the selected inventories could be read")
        ctx.emit("progress", {"summary": "Checking the model against your parts inventory."})
        report = model.parent / "parts-check.json"
        argv = ["./ldraw-agent", "inventory", "check", str(model), ids, "--mode", mode, "--json",
                "--store", str(settings.INVENTORIES_DIR), "--report", str(report)]
        run = await run_command(ctx, argv, 600)           # exit 1 = short in "only" mode
        data = json.loads(report.read_text(encoding="utf-8")) if report.is_file() else {}
        if run.exit_code not in (0, 1) or not isinstance(data, dict) or data.get("error") or "short" not in data:
            raise ValueError(str(data.get("error") if isinstance(data, dict) and data.get("error") else f"exit code {run.exit_code}")[:160])
        check = parts_steering.check_payload(data)
    except Exception as exc:  # noqa: BLE001 - the model is published either way
        reason = " ".join(str(exc).split())[:160]
        return None, f"PARTS CHECK unavailable ({reason}); the model was published anyway."
    return check, parts_steering.check_note(check, ids)


async def t_my_parts(ctx: ToolContext, query: str = "", colour: int | None = None, limit: int = parts_steering.DEFAULT_SEARCH_RESULTS,
                     inventory: str | None = None, queries: list | None = None) -> ToolResult:
    if queries is not None:
        if (not isinstance(queries, list) or not 0 < len(queries) <= parts_steering.MAX_BATCH_QUERIES
                or not all(isinstance(q, str) for q in queries)):
            raise ToolError(f"queries must be a list of 1 to {parts_steering.MAX_BATCH_QUERIES} strings")
        found = [json.loads((await t_my_parts(ctx, q, colour, limit, inventory)).content) for q in queries]
        return ToolResult(json.dumps(dict(results=[dict(query=q.strip()[:200], **r) for q, r in zip(queries, found)]), separators=(",", ":")))
    mode, invs = parts_steering.selection(ctx.options)
    if mode == "off":
        raise ToolError("my_parts is only available when this chat uses the owner's parts inventories")
    if not invs:
        raise ToolError("none of the inventories selected for this chat could be read")
    try:
        chosen = parts_steering.pick_inventories(invs, inventory)
    except ValueError as exc:
        raise ToolError(str(exc)) from None
    if colour is not None and (isinstance(colour, bool) or not isinstance(colour, int) or colour < 0):
        raise ToolError("colour must be an LDraw colour code (a non-negative integer)")
    if not isinstance(query, str) or isinstance(limit, bool) or not isinstance(limit, int):
        raise ToolError("query must be a string and limit an integer")
    limit = max(1, min(limit, parts_steering.MAX_SEARCH_RESULTS))
    argv = ["./ldraw-agent", "inventory", "search", parts_steering.id_list(chosen), "--limit", str(limit + 1), "--json",
            "--store", str(settings.INVENTORIES_DIR), *(["--colour", str(colour)] if colour is not None else [])]
    if query.strip():
        argv += ["--", query.strip()[:200]]               # after --: a query starting with "-" is not an option
    run = await run_command(ctx, argv, 120)
    try:
        data = json.loads(run.stdout)
    except ValueError:
        data = None
    matches = data.get("matches") if isinstance(data, dict) else None
    if run.exit_code != 0 or not isinstance(matches, list):
        raise ToolError("inventory search failed: " + " ".join(str((data or {}).get("error") if isinstance(data, dict) and data.get("error") else run.as_text()).split())[:300])
    rows = [dict(part=str(m["part"]), colour=int(m["colour"]), colour_name=parts_steering.safe_name(m.get("colour_name")),
                 qty=int(m["qty"]), description=parts_steering.safe_name(m.get("description"), 80) if m.get("description") else "")
            for m in matches[:limit]]
    return ToolResult(json.dumps(dict(inventories=[parts_steering.safe_name(i.name) for i in chosen], count=len(rows),
                                      more=len(matches) > limit, matches=rows), separators=(",", ":")))


async def t_buildable_models(ctx: ToolContext, query: str = "", limit: int = parts_steering.DEFAULT_BUILDABLE_RESULTS,
                             max_missing_share: float | None = None) -> ToolResult:
    mode, invs = parts_steering.selection(ctx.options)
    if mode == "off":
        raise ToolError("buildable_models is only available when this chat uses the owner's parts inventories")
    if not invs:
        raise ToolError("none of the inventories selected for this chat could be read")
    if not isinstance(query, str) or isinstance(limit, bool) or not isinstance(limit, int):
        raise ToolError("query must be a string and limit an integer")
    if max_missing_share is not None and (isinstance(max_missing_share, bool) or not isinstance(max_missing_share, (int, float))
                                          or not 0 <= max_missing_share <= 1):
        raise ToolError("max_missing_share must be a number between 0 and 1")
    limit = max(1, min(limit, parts_steering.MAX_BUILDABLE_RESULTS))
    report = ctx.work_dir / "buildable-models.json"
    report.unlink(missing_ok=True)
    argv = ["./ldraw-agent", "inventory", "buildable", parts_steering.id_list(invs), "--cache-only", f"--limit={limit}", "--json",
            "--store", str(settings.INVENTORIES_DIR), "--report", str(report),
            *([f"--max-missing-share={max_missing_share}"] if max_missing_share is not None else []),
            *([f"--query={query.strip()[:100]}"] if query.strip() else [])]
    run = await run_command(ctx, argv, 120)
    try:
        data = json.loads(report.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = None
    if run.exit_code != 0 or not isinstance(data, dict) or not isinstance(data.get("results"), list):
        raise ToolError("buildable_models failed: " + " ".join(str((data or {}).get("error") if isinstance(data, dict) and data.get("error") else run.as_text()).split())[:300])
    if data.get("pending"):
        raise ToolError("the catalogue of bundled models has not been scanned yet; the owner starts it with What can I build? on the My Parts page. "
                        "Continue without it.")
    rows = [dict(file=str(r["file"]), set_id=r.get("set_id"), description=" ".join(str(r.get("description") or "").split())[:140],
                 completeness=round(float(r["completeness"]) * 100), pieces=int(r["pieces"]), missing=int(r["missing"]),
                 top_missing=[f"{m['part']} {parts_steering.safe_name(m.get('colour_name'))} x{m['missing']}" for m in r.get("top_missing", [])[:3]])
            for r in data["results"][:limit]]
    return ToolResult(json.dumps(dict(inventories=[parts_steering.safe_name(i.name) for i in invs], count=len(rows),
                                      unmapped_share=data.get("unmapped_share"), models=rows,
                                      note="Bundled set models ranked by the share of their pieces the inventories cover (colour exact). "
                                           "Use a close one as a reference: find it by file name with the reference-discovery commands."),
                                 separators=(",", ":")))


async def t_list_files(ctx: ToolContext, path: str = "") -> ToolResult:
    folder = resolve_path(ctx, path) if path else toolkit.workspace(ctx.store, ctx.chat_id)
    if not folder.is_dir():
        raise ToolError(f"{path or folder} is not a folder")
    entries = sorted(folder.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    lines = [f"{p.name}/" if p.is_dir() else f"{p.name}\t{p.stat().st_size} bytes" for p in entries[:500]]
    if len(entries) > 500:
        lines.append(f"... and {len(entries) - 500} more")
    return ToolResult(f"{folder}:\n" + ("\n".join(lines) or "(empty)"))


async def t_read_file(ctx: ToolContext, path: str, max_chars: int | None = None, offset: int = 0) -> ToolResult:
    file = resolve_path(ctx, path)
    if not file.is_file():
        raise ToolError(f"{path} is not a file")
    if file.stat().st_size > 8 * 1024 * 1024:
        raise ToolError("File exceeds 8 MB; inspect it with a bounded toolkit or shell command")
    data = file.read_bytes()
    if b"\0" in data[:4096]:
        raise ToolError(f"{path} is a binary file ({len(data)} bytes)")
    text = data.decode(errors="replace")
    limit = max(1000, min(settings.read_file_max_chars() if max_chars is None else max_chars, 200_000))
    total = len(text)
    offset = max(0, offset)
    text = text[offset:offset + limit]
    if offset + limit < total:
        text += f"\n... [truncated: {total} characters total; continue at offset {offset + limit}]"
    return ToolResult(text)


async def t_write_file(ctx: ToolContext, path: str, content: str, append: bool = False) -> ToolResult:
    file = resolve_path(ctx, path, write=True)
    if len(content.encode()) > MAX_WRITE_BYTES:
        raise ToolError(f"content is larger than {MAX_WRITE_BYTES} bytes")
    file.parent.mkdir(parents=True, exist_ok=True)
    with file.open("a" if append else "w", encoding="utf-8") as fh:
        fh.write(content)
    for owned in [file, *file.parents]:            # the agent's own scripts must be able to edit them
        if owned == ctx.work_dir or ctx.work_dir not in owned.parents:
            break
        sandbox.give_to_agent(owned)
    return ToolResult(f"{'Appended to' if append else 'Wrote'} {file} ({file.stat().st_size} bytes)")


# --- registry --------------------------------------------------------------

def _fn(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties, "required": required},
    }}


TOOLS: dict[str, tuple[dict, Callable[..., Awaitable[ToolResult]]]] = {
    "run_toolkit": (_fn("run_toolkit",
        "Run the standalone LDraw Nova CLI. All commands are available: doctor, spec, discover, catalog, "
        "study, extract, examples, build, validate, inspect, render, compare-bom, manual, vehicle, spaceship, "
        "technic, mechanism and more. Read instructions.md and relevant docs first. Output goes under output/. "
        "Arguments are an array, without shell quoting or the executable. Use --help to inspect subcommands.",
        {"arguments": {"type": "array", "items": {"type": "string"}},
         "timeout": {"type": "integer", "description": "Seconds, default 300, maximum 1800"}},
        ["arguments"]), t_run_toolkit),
    "publish_model": (_fn("publish_model",
        "Publish an output MPD revision as an interactive model card in this chat and the My Builds collection. "
        "Preserves source bytes, runs toolkit geometry validation, renders a snapshot and BOM. Failed checks "
        "are flagged for repair. Use after generating a model and again after final visual review. "
        "Use the returned card_url for interactive links, not the raw model_url. "
        "Return links to plans, generator, manifests, reports and reviews alongside the card.",
        {"path": {"type": "string"}, "name": {"type": "string"}}, ["path"]), t_publish_model),
    "view_image": (_fn("view_image", "Open a PNG from the toolkit examples or output for actual visual review. "
        "The image is shown to you and the user. Use on rendered views before recording a visual review.",
        {"path": {"type": "string"}}, ["path"]), t_view_image),
    "report_progress": (_fn("report_progress", "Show a concise progress update: current phase, design decisions, "
        "completed checks, remaining work. Use throughout substantial builds, without private internal reasoning.",
        {"summary": {"type": "string"}}, ["summary"]), t_report_progress),
    "run_python": (_fn("run_python", "Run Python with the LDraw Nova virtualenv (pyldraw3, numpy, jsonschema). "
        "Working directory has the toolkit repository layout. Use its builder/serializer; write under output/. "
        "Code is saved for reproducibility. Call publish_model for user-visible model cards.",
        {"code": {"type": "string"}, "timeout": {"type": "integer", "description": "Seconds, default 60, max 1800"}},
        ["code"]), t_run_python),
    "run_shell": (_fn("run_shell", "Run bash in the toolkit workspace, with pipefail and live output. "
        "./ldraw-agent, ./check-model.sh, ./prepare-glb.sh and global CLIs are available. "
        "TYPESAFE_API_KEY comes from Settings; never print it. Write under output/. "
        "Call publish_model to show a generated model in the chat.",
        {"command": {"type": "string"}, "timeout": {"type": "integer", "description": "Seconds, default 60, max 1800"}},
        ["command"]), t_run_shell),
    "my_parts": (_fn("my_parts", "Search the parts the user owns (the inventories selected for this chat), most owned first. "
        "Rows: LDraw part number, LDraw colour code, colour name, owned quantity, description. Use it to pick parts and "
        "colours the user has before designing, and to find an owned substitute. query: words of the part number or "
        "description (all must match, e.g. 'plate 2 x 4'); empty lists the most owned. colour: LDraw colour code. "
        "inventory: one selected inventory (id or name), default all selected. "
        "queries: up to 5 queries in one call (same colour/limit/inventory) instead of one call each; returns {results:[{query,...}]}.",
        {"query": {"type": "string"}, "colour": {"type": "integer", "description": "LDraw colour code"},
         "limit": {"type": "integer", "description": f"Rows, default {parts_steering.DEFAULT_SEARCH_RESULTS}, max {parts_steering.MAX_SEARCH_RESULTS}"},
         "inventory": {"type": "string", "description": "Inventory id or name; default: all selected"},
         "queries": {"type": "array", "items": {"type": "string"}, "description": f"Up to {parts_steering.MAX_BATCH_QUERIES} queries in one call"}}, []), t_my_parts),
    "buildable_models": (_fn("buildable_models", "Rank the bundled LDraw set models by how much of each the chat's selected "
        "inventories cover (pieces matched by part and colour). Use it to say 'you can almost build X from set Y' and to pick a "
        "reference. Rows: file, set id, description, completeness %, pieces, missing, top missing lines. "
        "query: words of the description; max_missing_share: 0-1, e.g. 0.1 for nearly complete models.",
        {"query": {"type": "string"}, "limit": {"type": "integer", "description": f"Rows, default {parts_steering.DEFAULT_BUILDABLE_RESULTS}, max {parts_steering.MAX_BUILDABLE_RESULTS}"},
         "max_missing_share": {"type": "number"}}, []), t_buildable_models),
    "list_files": (_fn("list_files", "List toolkit resources or this chat's output/ (default: repository root).",
        {"path": {"type": "string"}}, []), t_list_files),
    "read_file": (_fn("read_file", "Read toolkit instructions, documentation, examples or your output files. "
        "Use offset to continue a truncated file. Query PDFs through run_toolkit spec.",
        {"path": {"type": "string"}, "max_chars": {"type": "integer"}, "offset": {"type": "integer"}},
        ["path"]), t_read_file),
    "write_file": (_fn("write_file", "Write a plan, generator, notes or report in output/. "
        "Bare relative paths also resolve under output/. Shared toolkit resources cannot be changed.",
        {"path": {"type": "string"}, "content": {"type": "string"}, "append": {"type": "boolean"}},
        ["path", "content"]), t_write_file),
}

async def t_delegate(ctx: ToolContext, role: str | None = None, task: str | None = None, inputs: list[str] | None = None,
                     tools: list[str] | None = None, outputs: list[str] | None = None, max_steps: int | None = None,
                     max_eur: float | None = None, summary_chars: int | None = None) -> ToolResult:
    import subagents                                      # lazy: subagents imports this module
    return await subagents.delegate(ctx, role, task, inputs, tools, outputs, max_steps, max_eur, summary_chars)


TOOLS["delegate"] = (_fn("delegate",
    "Hand one bounded, self-contained job to a cheaper worker model; it returns status, a short summary, its output "
    "files and cost. role: builder (writes a submodel), researcher (finds parts), reviewer (checks files). task: what to "
    "produce incl. interface dimensions, at most 1500 chars. inputs: paths under output/ it may read. tools: optional "
    "subset of read_file, list_files, my_parts, view_image, write_file, run_toolkit, run_python (default per role; never "
    "publish_model, delegate or run_shell). outputs: plain file names it must create; they land in output/workers/<id>/. "
    "max_steps default per role (builder 20), at most 40. max_eur default 0.10, at most 1. summary_chars default 600, "
    "at most 1500. An invalid contract is rejected with the reason; fix it and call again.",
    {"role": {"type": "string", "enum": ["builder", "researcher", "reviewer"]}, "task": {"type": "string"},
     "inputs": {"type": "array", "items": {"type": "string"}}, "tools": {"type": "array", "items": {"type": "string"}},
     "outputs": {"type": "array", "items": {"type": "string"}}, "max_steps": {"type": "integer"},
     "max_eur": {"type": "number"}, "summary_chars": {"type": "integer"}},
    ["role", "task"]), t_delegate)

import editor_tools  # noqa: E402 - needs ToolResult above; adds editor_open / editor_state / editor_edit
TOOLS.update({name: (editor_tools.SCHEMAS[name], fn) for name, fn in editor_tools.FUNCTIONS.items()})
TOOL_SCHEMAS = [schema for schema, _fn_ in TOOLS.values()]


async def dispatch(ctx: ToolContext, name: str, arguments: str | dict) -> ToolResult:
    if name not in TOOLS:
        return ToolResult(f"Error: unknown tool {name!r}. Available: {', '.join(TOOLS)}")
    try:
        args = json.loads(arguments or "{}") if isinstance(arguments, str) else dict(arguments or {})
    except json.JSONDecodeError as exc:
        return ToolResult(f"Error: tool arguments are not valid JSON ({exc}). Call {name} again with valid JSON.")
    _schema, fn = TOOLS[name]
    try:
        inspect.signature(fn).bind(ctx, **args)
    except TypeError as exc:
        return ToolResult(f"Error: bad arguments for {name}: {exc}")
    try:
        return await fn(ctx, **args)
    except ToolError as exc:
        return ToolResult(f"Error: {exc}")
    except Exception as exc:  # noqa: BLE001 - a failing tool must not end the turn
        return ToolResult(f"Error: {name} failed: {exc.__class__.__name__}: {exc}")
