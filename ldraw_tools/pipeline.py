"""Resident build pipeline: plan, build, geometry check, GLB and optional renders in one process.

Every CLI call pays 15-25 s of start-up (imports plus loading the parts library). `Pipeline` loads the parts once
and keeps them, so a second request costs only the work itself. Results are cached by content hash: the same plan
with the same toolkit version and options is answered from disk.

Outputs are the same as the CLI path: the MPD is `build_plan` text written with `atomic_write` (byte-identical to
`build`), the geometry report is `analyze_geometry` with the `build` command's default options.

Run as a worker (`python -m ldraw_tools.pipeline --serve`) it speaks JSON lines: one request object per stdin line,
one response object per stdout line. The module imports only the standard library at top level so a worker can
start, answer `ping` and be supervised without the parts library.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

CACHE_DIRNAME = ".pipeline-cache"
GEOMETRY_DEFAULTS = dict(detail="full", contacts="auto", output_limit=200, pair_limit=200, offset=0, instance_limit=100000)
VIEWS = ("home", "front", "back", "left", "right", "top", "bottom")
_PACKAGE = Path(__file__).resolve().parent


def canonical(value) -> str:
    """Stable JSON: sorted keys, no whitespace, so equal plans hash equally however they were built."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def toolkit_version() -> str:
    """Hash of the toolkit's own code and shipped data tables; any change invalidates cached outputs."""
    digest = hashlib.sha256()
    for path in sorted(_PACKAGE.rglob("*")):
        if path.is_file() and path.suffix in {".py", ".json"} and "__pycache__" not in path.parts:
            digest.update(path.relative_to(_PACKAGE).as_posix().encode() + b"\0")
            digest.update(path.read_bytes() + b"\0")
    return digest.hexdigest()[:16]


def cache_key(plan: dict, version: str, options: dict | None = None, library: str = "") -> str:
    """sha256 over (toolkit version, parts library, build options, canonical plan). Output file names are not part of it."""
    body = canonical(dict(version=version, library=library, options=options or {}, plan=plan))
    return hashlib.sha256(body.encode()).hexdigest()


def _normalise_options(geometry: bool, glb: bool, renders) -> dict:
    return dict(geometry=bool(geometry), glb=bool(glb), renders=sorted(set(renders or ())))


class Pipeline:
    def __init__(self, library=None):
        self.library_arg = library
        self.parts = self.library = None
        self.version = toolkit_version()
        self.pool = ThreadPoolExecutor(max_workers=int(os.environ.get("LDRAW_NOVA_RENDER_POOL", "2")))
        self.loaded_seconds = 0.0

    def load(self):
        """Import the heavy modules and load the parts once; later calls are free."""
        if self.parts is None:
            started = time.monotonic()
            from .common import get_parts, library_path
            self.library = self.library or library_path(self.library_arg)
            self.parts = get_parts(self.library)
            self.loaded_seconds = time.monotonic() - started
        return self.parts

    def run(self, outdir, *, plan=None, vehicle=None, palette=None, name="model", geometry=True, glb=True,
            renders=(), force=False, generator=None, params=None) -> dict:
        """Build `plan` (or a named vehicle design) into outdir/<name>.mpd and check, convert, render it.

        Returns a JSON-able dict: key, cached, ok, paths, diagnostics, geometry, timings. A build with errors writes
        nothing, exactly like the CLI.
        """
        started = time.monotonic()
        for view in renders or ():
            if view not in VIEWS:
                raise ValueError(f"Unknown view {view}")
        if not name.replace("-", "").replace("_", "").isalnum():
            raise ValueError("name must be letters, digits, - or _")
        outdir = Path(outdir).resolve()
        if self.library is None:
            from .common import library_path
            self.library = library_path(self.library_arg)
        timings = {}
        if sum(x is not None for x in (plan, vehicle, generator)) != 1:
            raise ValueError("Give exactly one of plan, vehicle or generator")
        t = time.monotonic()
        generated = None
        if generator is not None:
            from .generators import generate
            generated = generate(generator, params)
            plan = generated["plan"]
        elif vehicle is not None:
            from .vehicles import vehicle_plan
            plan = vehicle_plan(vehicle, palette)
        if plan.get("includes") or plan.get("assets"):
            raise ValueError("Plans with includes or assets are not cached; build them with the CLI")
        timings["plan"] = round(time.monotonic() - t, 2)
        options = _normalise_options(geometry, glb, renders)
        key = cache_key(plan, self.version, options, str(self.library))
        entry = outdir / CACHE_DIRNAME / key
        if not force and (entry / "result.json").is_file():
            result = self._restore(entry, outdir, name)
            if result:
                result.update(key=key, cached=True, seconds=round(time.monotonic() - started, 3))
                if generated:
                    result["generator"] = {k: generated[k] for k in ("generator", "params", "notes")}
                return result
        shutil.rmtree(entry, ignore_errors=True)
        entry.mkdir(parents=True, exist_ok=True)
        parts = self.load()
        timings["load"] = round(self.loaded_seconds, 2)
        result = self._build(plan, parts, entry, options, timings)
        result["key"] = key
        result["cached"] = False
        result["seconds"] = round(time.monotonic() - started, 3)
        if result["ok"]:
            from .common import jsonable
            (entry / "result.json").write_text(canonical(jsonable(result)), encoding="utf-8")
            result["paths"] = self._restore(entry, outdir, name)["paths"]
        else:
            shutil.rmtree(entry, ignore_errors=True)
            result["paths"] = {}
        if generated:
            result["generator"] = {k: generated[k] for k in ("generator", "params", "notes")}
        return result

    # -- stages -------------------------------------------------------------------------------------------------

    def _build(self, plan, parts, entry: Path, options: dict, timings: dict) -> dict:
        from .builder import build_plan, load_plan
        from .common import atomic_write, dumps
        from .geometry import analyze_geometry
        # load_plan wants a file (it resolves includes); plans here have none, so this is a straight read.
        plan_file = entry / "plan.json"
        plan_file.write_text(dumps(plan) + "\n", encoding="utf-8")
        t = time.monotonic()
        text, model, diagnostics = build_plan(load_plan(plan_file), parts, instance_limit=GEOMETRY_DEFAULTS["instance_limit"])
        timings["build"] = round(time.monotonic() - t, 2)
        failed = any(d["severity"] == "error" for d in diagnostics)
        result = dict(ok=not failed, diagnostics=diagnostics, geometry=None, files={}, timings=timings)
        if failed:
            return result
        mpd = entry / "model.mpd"
        atomic_write(mpd, text)
        result["files"]["mpd"] = "model.mpd"
        # The GLB and renders only need the MPD, so they run beside the geometry pass.
        jobs = {}
        if options["glb"]:
            jobs["glb"] = self.pool.submit(self._timed, "glb", self._glb, mpd, entry / "model.glb")
        if options["renders"]:
            jobs["renders"] = self.pool.submit(self._timed, "renders", self._renders, mpd, entry, options["renders"])
        if options["geometry"]:
            t = time.monotonic()
            geometry = analyze_geometry(model, parts, **GEOMETRY_DEFAULTS)
            timings["geometry"] = round(time.monotonic() - t, 2)
            result["geometry"] = geometry
            result["diagnostics"] = diagnostics + geometry["diagnostics"]
            if any(d["severity"] == "error" for d in result["diagnostics"]):
                result["ok"] = False
        result["checks_passed"] = result["ok"]
        for kind, job in jobs.items():
            try:
                value, seconds = job.result()
                timings[kind] = round(seconds, 2)
                if kind == "glb":
                    result["files"]["glb"] = "model.glb"
                else:
                    result["files"].update({Path(p).name: f"renders/{Path(p).name}" for p in value})
            except Exception as exc:     # a failed preview must not hide a good build
                result.setdefault("warnings", []).append(f"{kind} failed: {exc}")
        return result

    @staticmethod
    def _timed(_label, function, *args):
        t = time.monotonic()
        return function(*args), time.monotonic() - t

    def _glb(self, mpd: Path, out: Path):
        from .external import prepare_glb
        prepare_glb(mpd, self.library, out, self.parts, timeout=180)
        return out

    def _renders(self, mpd: Path, entry: Path, views):
        from .external import render
        folder = entry / "renders"
        report = render(mpd, self.library, folder, views=tuple(views), timeout=120)
        return report["images"]

    def _restore(self, entry: Path, outdir: Path, name: str):
        """Copy a cached entry's files next to the chat's other outputs; None if the entry is incomplete."""
        try:
            result = json.loads((entry / "result.json").read_text(encoding="utf-8"))
            files = result["files"]
            for kind, rel in files.items():
                source = entry / rel
                if not source.is_file():
                    return None
                destination = outdir / (f"{name}{source.suffix}" if kind in {"mpd", "glb"} else rel)
                destination.parent.mkdir(parents=True, exist_ok=True)
                temporary = destination.with_name(destination.name + ".tmp")
                shutil.copyfile(source, temporary)
                temporary.replace(destination)
            result["paths"] = {k: str(outdir / (f"{name}{Path(v).suffix}" if k in {"mpd", "glb"} else v)) for k, v in files.items()}
            return result
        except (OSError, ValueError, KeyError):
            return None


# -- worker ---------------------------------------------------------------------------------------------------------


def _handle(pipeline: Pipeline, request: dict) -> dict:
    op = request.get("op", "build")
    if op == "ping":
        return dict(ok=True, pong=True, version=pipeline.version, loaded=pipeline.parts is not None)
    if op == "load":
        pipeline.load()
        return dict(ok=True, load_seconds=round(pipeline.loaded_seconds, 2))
    if op == "crash" and os.environ.get("LDRAW_NOVA_WORKER_DEBUG") == "1":
        os._exit(3)
    if op == "hang" and os.environ.get("LDRAW_NOVA_WORKER_DEBUG") == "1":
        time.sleep(3600)
    if op == "generators":
        from .generators import REGISTRY
        from .generators.colours import NAMES
        return dict(ok=True, generators=[g.entry() for g in REGISTRY.values()], colours=dict(NAMES))
    if op == "build":
        keys = ("plan", "vehicle", "palette", "name", "geometry", "glb", "renders", "force", "generator", "params")
        return pipeline.run(request["outdir"], **{k: request[k] for k in keys if k in request})
    raise ValueError(f"Unknown op {op!r}")


def serve(stdin=None, stdout=None) -> int:
    """JSON-lines loop. Stray prints from libraries go to stderr, never into the protocol stream."""
    stdin = stdin or sys.stdin
    if stdout is None:
        stdout = os.fdopen(os.dup(1), "w", encoding="utf-8", newline="\n")
        os.dup2(2, 1)
        sys.stdout = sys.stderr
    lock = threading.Lock()
    pipeline = Pipeline()
    for line in stdin:
        if not line.strip():
            continue
        request_id = None
        try:
            request = json.loads(line)
            request_id = request.get("id")
            response = _handle(pipeline, request)
        except Exception as exc:       # the worker outlives any single bad request
            response = dict(ok=False, error=str(exc), error_type=type(exc).__name__)
        response["id"] = request_id
        with lock:
            stdout.write(json.dumps(response, ensure_ascii=False, allow_nan=False, default=str) + "\n")
            stdout.flush()
    return 0


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv != ["--serve"]:
        print("usage: python -m ldraw_tools.pipeline --serve", file=sys.stderr)
        return 2
    return serve()


if __name__ == "__main__":
    sys.exit(main())
