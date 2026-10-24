"""LLM model settings, providers and the model catalog."""
from __future__ import annotations

import asyncio
from typing import Any, Optional

import litellm
import yaml
from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

import environment_config
import inference
import llm_config
import model_catalog
import model_discovery
import provider_errors
import providers
from provider_errors import KEY_CHECKS
from .common import not_found

router = APIRouter()


class LlmEntry(BaseModel):
    model_name: Optional[str] = None
    litellm_params: dict[str, Any]
    capabilities: Optional[dict[str, Any]] = None
    auth_mode: str = "api_key"


@router.get("/api/model-catalog")
def model_catalog_list():
    return {"models": [model_catalog.profile(m["model"]) for m in model_catalog.CATALOG]}


@router.get("/api/llm-models")
def llm_models_list():
    entries, default_id = llm_config.builder_entries()
    return {"models": [llm_config.public(e) for e in entries], "default_id": default_id, "worker_id": llm_config.worker_default(),
            "understanding_id": llm_config.understanding_default()}


@router.get("/api/llm-models/{entry_id}/edit")
def llm_models_edit(entry_id: str):
    entry = llm_config.get(entry_id) or not_found()
    return llm_config.public(entry)


@router.post("/api/llm-models")
def llm_models_create(entry: LlmEntry):
    try:
        return llm_config.public(llm_config.create(entry.model_dump()))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


@router.put("/api/llm-models/{entry_id}")
def llm_models_update(entry_id: str, entry: LlmEntry):
    try:
        updated = llm_config.update(entry_id, entry.model_dump())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    return llm_config.public(updated) if updated else not_found()


@router.delete("/api/llm-models/{entry_id}")
def llm_models_delete(entry_id: str):
    return {"deleted": llm_config.delete(entry_id)}


@router.post("/api/llm-models/{entry_id}/default")
def llm_models_default(entry_id: str):
    llm_config.set_default(entry_id)
    return {"default_id": llm_config.list_entries()[1]}


class WorkerBody(BaseModel):
    id: Optional[str] = None


@router.put("/api/llm-workers/default")
def llm_worker_default(body: WorkerBody):
    try:
        llm_config.set_worker_default(body.id or None)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    return {"worker_id": llm_config.worker_default()}


@router.put("/api/llm-understanding/default")
def llm_understanding_default(body: WorkerBody):
    try:
        llm_config.set_understanding_default(body.id or None)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    return {"understanding_id": llm_config.understanding_default()}


@router.post("/api/llm-models/{entry_id}/test")
async def llm_models_test(entry_id: str):
    entry = llm_config.get(entry_id) or not_found()
    if llm_config.unsupported(entry):
        return {"ok": False, "error": llm_config.UNSUPPORTED_MESSAGE, "profile": model_catalog.entry_profile(entry)}
    fingerprint = llm_config.connection_fingerprint(entry)
    discovery = asyncio.create_task(model_discovery.discover(entry))
    async def tested(result):
        metadata = await discovery
        result["connection_status"] = llm_config.record_connection_test(entry_id, fingerprint, result["ok"], metadata)
        result["profile"] = model_catalog.entry_profile(llm_config.get(entry_id) or entry)
        return result
    own = llm_config.own_key(entry)
    pid = provider_errors.provider_id(entry["litellm_params"]["model"])
    if own and pid in KEY_CHECKS:
        checked = await provider_errors.check_key(pid, own, remember=False)
        if checked["status"] == "rejected":
            return await tested({"ok": False, "error": checked["error"]})
    try:
        params = await inference.params_for(entry, {})
        params.setdefault("max_tokens", 4096)
        params.setdefault("timeout", 60)
        await litellm.acompletion(
            **params,
            messages=[{"role": "user", "content": "Reply with the single word: OK"}],
        )
        return await tested({"ok": True, "reply": "Connection successful.",
                       "capabilities": llm_config.capabilities(entry)})
    except Exception as exc:  # noqa: BLE001 - shown to the user as the test result
        return await tested({"ok": False, "error": f"{exc.__class__.__name__}: Check credentials, model access and settings."})


@router.get("/api/llm-models/export", response_class=PlainTextResponse)
def llm_models_export():
    entries, _default = llm_config.builder_entries()
    model_list = [{"model_name": e["model_name"],
                   "litellm_params": llm_config.public(e)["litellm_params"],
                   "capabilities": e["capabilities"], "auth_mode": e.get("auth_mode", "api_key")} for e in entries]
    return yaml.safe_dump({"model_list": model_list}, sort_keys=False, allow_unicode=True)


class ImportBody(BaseModel):
    yaml: str


@router.post("/api/llm-models/import")
def llm_models_import(body: ImportBody):
    try:
        data = yaml.safe_load(body.yaml) or {}
        model_list = data["model_list"] if isinstance(data, dict) else data
        imported = llm_config.import_model_list(model_list)
    except (yaml.YAMLError, KeyError, TypeError, ValueError) as exc:
        raise HTTPException(400, f"not a LiteLLM model_list: {exc}") from None
    return {"imported": [llm_config.public(e) for e in imported]}


@router.get("/api/llm-providers")
def llm_providers():
    names = sorted({getattr(p, "value", p) for p in litellm.provider_list})
    return {"providers": names, "catalogue": providers.catalogue()}


@router.get("/api/llm-providers/{provider}/models")
def llm_provider_models(provider: str):
    models = litellm.models_by_provider.get(provider, [])
    presets = [m["model"] for m in model_catalog.CATALOG if m["model"].startswith(provider + "/")]
    candidates = {m if m.startswith(provider + "/") else provider + "/" + m for m in models} | set(presets)
    return {"models": sorted(m for m in candidates if model_catalog.builder_supported(m))}


@router.get("/api/llm-providers/keys")
def llm_provider_keys():
    """Which providers have a key set (saved here or inherited); never the key itself."""
    env = environment_config.snapshot()
    return {"keys": {p: bool(env.get(name)) for p, (name, _url, _h) in KEY_CHECKS.items()},
            "rejected": provider_errors.rejected_providers()}


@router.post("/api/llm-providers/{provider}/test")
async def llm_provider_test(provider: str):
    if provider not in KEY_CHECKS:
        raise HTTPException(400, "No free key check exists for this provider")
    name = KEY_CHECKS[provider][0]
    key = environment_config.snapshot().get(name)
    if not key:
        return {"ok": False, "status": "missing", "error": f"No key saved. Paste your {name} first."}
    return await provider_errors.check_key(provider, key)
