"""FastAPI app: chat API + SSE, LLM settings, file/library routes, 3D viewer and SPA.

Run (the image's default CMD):
    uvicorn main:app --app-dir /app/web/backend --host 0.0.0.0 --port 8000

The routes live in routers/, one module per area.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

import environment_config

environment_config.initialize()

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

import run_state
import sandbox
import settings
from routers import chats, environment, files, inventories, llm_models, models_gallery, spa
from store import get_store

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    for folder in (settings.GENERATED_DIR, settings.CHATS_DIR, settings.OUTPUT_DIR):
        folder.mkdir(parents=True, exist_ok=True)
    sandbox.give_to_agent(settings.GENERATED_DIR)      # agent scripts may publish models there
    get_store()
    yield
    for chat_id in list(run_state._runs):
        await run_state.cancel(chat_id)


app = FastAPI(title="LDraw Nova agent chat", lifespan=lifespan)


@app.exception_handler(RequestValidationError)
async def invalid_request(_request: Request, exc: RequestValidationError):
    # Pydantic includes submitted values by default, including credentials.
    return JSONResponse(status_code=422, content={"detail": [
        {"loc": error["loc"], "msg": error["msg"], "type": error["type"]} for error in exc.errors()
    ]})


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
        # An inventory upload carries a CSV of up to 25 MB (the route enforces that) inside its JSON body.
        limit = 27 if request.url.path == "/api/inventories" else 18
        if size > limit * 1024 * 1024:
            return JSONResponse({"detail": f"Request exceeds {limit} MB"}, status_code=413)
    response = await call_next(request)
    if request.url.path.startswith("/api/environment") or request.url.path.endswith("/edit"):
        response.headers["Cache-Control"] = "no-store"
    return response


for module in (environment, llm_models, chats, models_gallery, files, inventories):
    app.include_router(module.router)
spa.mount_static(app)
app.include_router(spa.router)   # catch-all: after every other route and mount
