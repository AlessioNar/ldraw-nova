"""The parts collection API: browse, edit with history and undo, review queue, imports with preview, sets, quick add, thumbnails.

The older /api/inventories routes keep working; a "source" here is one of those inventories.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

import collection
import collection_export
import inventories
import part_images
import part_info
import quick_add
import set_catalog
from inventories import InventoryError

router = APIRouter()


def failure(exc: InventoryError) -> HTTPException:
    return HTTPException(exc.status, str(exc))


class Ops(BaseModel):
    ops: list[dict]
    note: Optional[str] = ""


class NewPreview(BaseModel):
    name: str
    csv: str
    include_spares: Optional[bool] = False
    family_colours: Optional[dict] = None
    filename: Optional[str] = ""
    format: Optional[str] = ""          # override the detected format
    mapping: Optional[dict] = None      # generic CSV: {part, colour, qty, system}


class Diff(BaseModel):
    replace: str


class Commit(BaseModel):
    name: Optional[str] = None
    replace: Optional[str] = None


class NewSet(BaseModel):
    set_num: str
    copies: Optional[int] = 1
    state: Optional[str] = "parts"


class QuickText(BaseModel):
    text: str


class QuickItems(BaseModel):
    items: list[dict]
    source: Optional[str] = inventories.LOOSE_ID


class Note(BaseModel):
    note: str


class ResolveReview(BaseModel):
    source: str
    id: Optional[str] = None
    family: Optional[str] = None
    colour: Optional[int] = None
    qty: Optional[int] = None
    discard: Optional[bool] = False


# --- browsing ----------------------------------------------------------------

@router.get("/api/collection")
def collection_overview():
    quick_add.warm()
    return {"sources": collection.sources(), **{k: v for k, v in collection.browse(limit=0).items() if k in ("collection", "facets")}}


@router.get("/api/collection/parts")
def collection_parts(q: str = "", colour: str = "", category: str = "", size: str = "", source: str = "", sort: str = "qty",
                     limit: int = Query(100, ge=0, le=300), offset: int = Query(0, ge=0), maybe: bool = False):
    try:
        colours = [int(c) for c in colour.split(",") if c.strip()]
    except ValueError:
        raise HTTPException(400, "colour must be LDraw colour codes separated by commas") from None
    ids = [s for s in source.split(",") if s.strip()] or None
    return collection.browse(q[:200], colours, category[:60], size[:20], ids, sort, limit, offset, maybe)


@router.get("/api/collection/parts/{part}")
def collection_part(part: str):
    try:
        return collection.part_detail(part)
    except InventoryError as exc:
        raise failure(exc) from None


@router.put("/api/collection/parts/{part}/note")
def collection_note(part: str, body: Note):
    try:
        return collection.set_note(part, body.note)
    except InventoryError as exc:
        raise failure(exc) from None


@router.get("/api/collection/colours")
def collection_colours():
    return {"colours": [{"code": c, "name": n} for c, n in sorted(part_info.colour_names().items())]}


# --- editing, history, undo --------------------------------------------------

@router.post("/api/collection/sources/{source_id}/ops")
def collection_ops(source_id: str, body: Ops):
    try:
        return collection.apply_ops(source_id, body.ops, body.note or "")
    except InventoryError as exc:
        raise failure(exc) from None


@router.get("/api/collection/sources/{source_id}/history")
def collection_history(source_id: str, limit: int = 30):
    try:
        return collection.history(source_id, limit)
    except InventoryError as exc:
        raise failure(exc) from None


@router.post("/api/collection/sources/{source_id}/undo")
def collection_undo(source_id: str):
    try:
        return collection.undo(source_id)
    except InventoryError as exc:
        raise failure(exc) from None


# --- review queue ------------------------------------------------------------

@router.get("/api/collection/review")
def collection_review():
    return collection.review_queue()


@router.post("/api/collection/review")
def collection_review_resolve(body: ResolveReview):
    """One piece (id) or a whole colour family: give it an exact colour, or discard it (not mine)."""
    try:
        if body.discard:
            if not body.id:
                raise InventoryError(400, "Say which piece to discard")
            return collection.apply_ops(body.source, [{"op": "review_discard", "id": body.id}], "Not mine")
        if body.colour is None:
            raise InventoryError(400, "Choose a colour")
        if body.family:
            return collection.resolve_family(body.source, body.family, body.colour)
        if not body.id:
            raise InventoryError(400, "Say which piece, or which colour family")
        return collection.apply_ops(body.source, [{"op": "review_resolve", "id": body.id, "colour": body.colour, "qty": body.qty}], "Review")
    except InventoryError as exc:
        raise failure(exc) from None


# --- imports: preview first, then commit -------------------------------------

@router.post("/api/collection/imports/preview")
async def import_preview(body: NewPreview):
    try:
        return await collection.preview_import(body.name, body.csv, bool(body.include_spares), body.family_colours, body.filename or "",
                                               body.format or "", body.mapping)
    except InventoryError as exc:
        raise failure(exc) from None


@router.post("/api/collection/imports/{preview_id}/diff")
def import_diff(preview_id: str, body: Diff):
    try:
        return collection.preview_diff(preview_id, body.replace)
    except InventoryError as exc:
        raise failure(exc) from None


@router.post("/api/collection/imports/{preview_id}/commit")
def import_commit(preview_id: str, body: Commit):
    try:
        return collection.commit_import(preview_id, body.name, body.replace)
    except InventoryError as exc:
        raise failure(exc) from None


# --- exports -------------------------------------------------------------------

class ExportLines(BaseModel):
    format: str
    lines: list[dict]
    name: Optional[str] = "parts"


def _export_summary(built: dict) -> dict:
    return {k: v for k, v in built.items() if k != "body"}


@router.get("/api/collection/export/formats")
def export_formats():
    return {"formats": [{"id": k, "title": v[0]} for k, v in collection_export.FORMATS.items()]}


@router.get("/api/collection/export/preview")
def export_preview(format: str, scope: str = "collection"):
    """What the file would hold and what it cannot: counts and the omitted lines with the reason."""
    try:
        return _export_summary(collection_export.export(format, scope))
    except InventoryError as exc:
        raise failure(exc) from None


@router.get("/api/collection/export")
def export_download(format: str, scope: str = "collection"):
    try:
        built = collection_export.export(format, scope)
    except InventoryError as exc:
        raise failure(exc) from None
    return Response(built["body"], media_type=f"{built['media_type']}; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{built["filename"]}"', "X-Export-Omitted": str(built["omitted_lines"])})


@router.post("/api/collection/export/lines")
def export_lines(body: ExportLines, download: bool = False):
    """Exports lines the caller already has (a shortage list: {part, colour (LDraw), qty}); download=true answers with the file."""
    try:
        built = collection_export.build(body.format, collection_export.clean_lines(body.lines), (body.name or "parts")[:60])
    except InventoryError as exc:
        raise failure(exc) from None
    if not download:
        return {**_export_summary(built), "body": built["body"]}
    return Response(built["body"], media_type=f"{built['media_type']}; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{built["filename"]}"', "X-Export-Omitted": str(built["omitted_lines"])})


# --- sets ----------------------------------------------------------------------

def _catalog() -> set_catalog.Catalog:
    try:
        return set_catalog.load()
    except set_catalog.CatalogError as exc:
        raise HTTPException(503, str(exc)) from None


def _set_json(s: dict) -> dict:
    return {"set_num": s["set_num"], "name": s["name"], "year": s["year"], "theme": s["theme"], "num_parts": s["num_parts"],
            "mapped_pieces": s["mapped_pieces"], "dropped_pieces": s["dropped_part"] + s["dropped_colour"]}


@router.get("/api/sets")
def sets_search(query: str = "", limit: int = Query(20, ge=1, le=50)):
    return {"sets": [_set_json(s) for s in _catalog().search(query[:100], limit)]}


@router.get("/api/sets/{set_num}")
def sets_get(set_num: str):
    catalog = _catalog()
    found = catalog.get(set_num)
    if found is None:
        raise HTTPException(404, "No such set in the catalogue")
    lines = catalog.lines(found["set_num"])
    return {**_set_json(found), "distinct_parts": len({p for p, _, _ in lines}), "lines": len(lines),
            "owned_sources": [s["id"] for s in inventories.list_inventories() if (s.get("set") or {}).get("set_num") == found["set_num"]]}


@router.post("/api/collection/sets")
def sets_add(body: NewSet):
    try:
        return collection.add_set(body.set_num, 1 if body.copies is None else body.copies, body.state or "parts")
    except InventoryError as exc:
        raise failure(exc) from None


# --- quick add: understand first, add on confirm -----------------------------

@router.post("/api/collection/quick-add/parse")
def quick_parse(body: QuickText):
    return quick_add.parse(body.text)


@router.post("/api/collection/quick-add")
def quick_commit(body: QuickItems):
    try:
        return collection.quick_add(body.items, body.source or inventories.LOOSE_ID)
    except InventoryError as exc:
        raise failure(exc) from None


# --- thumbnails ----------------------------------------------------------------

@router.get("/api/parts/{part}/thumb")
async def part_thumb(part: str):
    """The cached neutral picture of a part (PNG, rendered on first view); 202 with a placeholder while it is being made."""
    part = collection.part_key(part)
    if not part_images.valid(part):
        raise HTTPException(404, "No such part")
    path = await part_images.get(part)
    if path is not None:
        return FileResponse(path, media_type="image/png", headers={"Cache-Control": "public, max-age=604800, immutable"})
    return Response(part_images.PLACEHOLDER, status_code=202, media_type="image/svg+xml",
                    headers={"Cache-Control": "no-store", "X-Thumb-Status": part_images.status(part)})
