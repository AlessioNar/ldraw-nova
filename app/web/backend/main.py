"""FastAPI app: chat API + SSE, LLM settings, file/library routes, 3D viewer and SPA.

Run (the image's default CMD):
    uvicorn main:app --app-dir /app/web/backend --host 0.0.0.0 --port 8000

The routes live in routers/, one module per area.
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

import environment_config

environment_config.initialize()

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

import build_worker
import generator_bridge
import db
import request_log
import run_state
import sandbox
import settings
from db import migrate as db_migrate
from routers import builds_api, chats, collection, costs_api, describe_api, editor, editor_palette, environment, files, inventories, llm_models, models_gallery, pipeline, reservations, spa, understand_api, workspace_api
import builds
import migrate_json
import storage
from store import get_store, reset_store

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


async def _prune_request_log_daily() -> None:
    while True:
        await asyncio.sleep(24 * 3600)
        await asyncio.to_thread(request_log.prune)


async def _import_json_on_first_start() -> None:
    """LDRAW_NOVA_DB_IMPORT=auto: dry run, commit and verify of the JSON chats and builds when none was imported yet."""
    try:
        done = await asyncio.to_thread(migrate_json.first_start, settings.DATA_DIR)
    except Exception as exc:  # noqa: BLE001 - a failed import leaves the files in use, it never stops the app
        logging.getLogger("migrate_json").error("the first-start import failed: %s: %s", exc.__class__.__name__, str(exc)[:300])
        return
    if done is not None:
        logging.getLogger("migrate_json").log(logging.INFO if done else logging.ERROR,
                                               "first-start import %s (report in %s/)", "verified" if done else "FAILED verification",
                                               settings.DATA_DIR / migrate_json.REPORT_DIR_NAME)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    for folder in (settings.GENERATED_DIR, settings.CHATS_DIR, settings.OUTPUT_DIR):
        folder.mkdir(parents=True, exist_ok=True)
    sandbox.give_to_agent(settings.GENERATED_DIR)      # agent scripts may publish models there
    # The schema is brought up to date before anything uses the database; if it cannot be reached the app still
    # starts (the chats and builds then stay files) and /api/environment says "Database: unavailable".
    if await asyncio.to_thread(db_migrate.upgrade_when_ready):
        await asyncio.to_thread(request_log.prune)
        await _import_json_on_first_start()
    storage.refresh()
    reset_store()
    if get_store().pg:
        await asyncio.to_thread(builds.reconcile, get_store())      # a version record for every model file of a recorded build
    pruning = asyncio.create_task(_prune_request_log_daily())
    generator_bridge.warm()      # refresh the generator registry after a toolkit update, before the first request
    yield
    pruning.cancel()
    await asyncio.to_thread(db.dispose)
    for chat_id in list(run_state._runs):
        await run_state.cancel(chat_id)
    await build_worker.shutdown()


app = FastAPI(title="Open Brick Designer", lifespan=lifespan)


@app.exception_handler(RequestValidationError)
async def invalid_request(_request: Request, exc: RequestValidationError):
    # Pydantic includes submitted values by default, including credentials.
    return JSONResponse(status_code=422, content={"detail": [
        {"loc": error["loc"], "msg": error["msg"], "type": error["type"]} for error in exc.errors()
    ]})


# DNS rebinding: a malicious page can make its own hostname resolve to 127.0.0.1, after which the browser treats
# our API as same-origin (Origin and Host both name the attacker's domain). Only IP literals and these names are
# served; add a LAN name with LDRAW_NOVA_ALLOWED_HOSTS=mypc.local,nas (comma separated).
ALLOWED_HOSTNAMES = {"localhost", "host.docker.internal"}


def host_allowed(host_header: str | None) -> bool:
    host = (host_header or "").strip().lower()
    if host.startswith("["):                      # [::1]:8765
        host = host[1:].split("]", 1)[0]
    elif host.count(":") == 1:                    # name:port, ipv4:port
        host = host.split(":", 1)[0]
    host = host.rstrip(".")
    if not host:
        return False
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        pass
    extra = {h.strip().lower() for h in os.environ.get("LDRAW_NOVA_ALLOWED_HOSTS", "").split(",") if h.strip()}
    return host in ALLOWED_HOSTNAMES or host in extra


@app.middleware("http")
async def reject_unknown_host(request: Request, call_next):
    if not host_allowed(request.headers.get("host")):
        return JSONResponse({"detail": "Unknown Host header. Open the app by localhost or its IP address, or list this "
                                       "name in LDRAW_NOVA_ALLOWED_HOSTS."}, status_code=421)
    return await call_next(request)


def body_limit_mb(path: str) -> int:
    # An inventory upload carries a CSV of up to 25 MB (the route enforces that) inside its JSON body.
    return 27 if path in ("/api/inventories", "/api/collection/imports/preview") else 18


class ChunkedBodyLimit:
    """Caps request bodies that arrive without a Content-Length (chunked), which the check below cannot see.

    Past the limit it answers 413 itself and tells the app the client went away (an exception would be
    swallowed by the framework's body parsing and reported as a 400)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if scope["type"] != "http" or not path.startswith("/api/") or any(k == b"content-length" for k, _ in scope["headers"]):
            return await self.app(scope, receive, send)
        limit = body_limit_mb(path) * 1024 * 1024
        total = 0
        started = rejected = False

        async def limited_receive():
            nonlocal total, rejected
            if rejected:
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] == "http.request":
                total += len(message.get("body", b""))
                if total > limit and not started:
                    rejected = True
                    response = JSONResponse({"detail": f"Request exceeds {limit // (1024 * 1024)} MB"}, status_code=413)
                    await response(scope, receive, send)
                    return {"type": "http.disconnect"}
            return message

        async def tracking_send(message):
            nonlocal started
            if rejected:
                return
            started = started or message["type"] == "http.response.start"
            await send(message)

        await self.app(scope, limited_receive, tracking_send)


@app.middleware("http")
async def same_origin_api(request: Request, call_next):
    if request.url.path.startswith("/api/"):
        origin = request.headers.get("origin")
        if request.headers.get("sec-fetch-site") == "cross-site" or (
            origin and urlsplit(origin).netloc != request.headers.get("host")
        ):
            return JSONResponse({"detail": "Cross-origin API requests are not allowed"}, status_code=403)
        try:
            size = int(request.headers.get("content-length", "0"))
        except ValueError:
            return JSONResponse({"detail": "Invalid Content-Length"}, status_code=400)
        limit = body_limit_mb(request.url.path)
        if size > limit * 1024 * 1024:
            return JSONResponse({"detail": f"Request exceeds {limit} MB"}, status_code=413)
    response = await call_next(request)
    if request.url.path.startswith("/api/environment") or request.url.path.endswith("/edit"):
        response.headers["Cache-Control"] = "no-store"
    return response


app.add_middleware(ChunkedBodyLimit)   # outermost: also guards bodies the middlewares above never see in full

for module in (builds_api, costs_api, environment, llm_models, chats, editor, editor_palette, models_gallery, files, inventories, collection, reservations, understand_api, describe_api, pipeline, workspace_api):
    app.include_router(module.router)
spa.mount_static(app)
app.include_router(spa.router)   # catch-all: after every other route and mount
