"""One owned set with its parts list: what the set needs, what the collection holds, and what is missing.

Per row, `needed` is the quantity in the set (already multiplied by its copies), `owned` what the whole collection holds of that
part and colour, `reserved` what is held back by built sets and models (this set's own rows count when it is built), and
`missing` how many of the row's pieces the collection can no longer cover. `on_hand` is the rest. It is the same arithmetic as
`collection.owned_sets`, one row at a time.
"""
import collection
import collection_store as store
import part_info
import reservations
import set_images


def detail(source_id: str) -> dict:
    """The set record plus every parts row with on-hand and missing counts; 404 (InventoryError) when the source is not a set."""
    data = store.load(source_id)
    record = collection.set_record(data)
    if record is None:
        raise collection.InventoryError(404, "No such set")
    owned, _ = store.owned_totals()
    reserved = reservations.reserved_totals()
    built = record["state"] == "built"
    rows = []
    for r in data.get("rows", []):
        need = int(r["qty"])
        if need <= 0:
            continue
        part, colour = reservations.part_key(r["part"]), int(r["colour"])
        have, held = owned.get((part, colour), 0), reserved.get((part, colour), 0)
        # A parts-only set is not in `reserved` yet; what it would hold counts as if it were.
        missing = min(need, max(0, held + (0 if built else need) - have))
        rows.append({"part": part, "colour": colour, "colour_name": part_info.colour_name(colour), "description": part_info.describe(part),
                     "category": part_info.category(part), "needed": need, "owned": have, "reserved": held, "missing": missing,
                     "on_hand": need - missing})
    rows.sort(key=lambda x: (x["colour_name"], x["part"], x["colour"]))
    pieces = sum(x["needed"] for x in rows)
    missing = sum(x["missing"] for x in rows)
    return {"set": {**record, "image": record["image"] or set_images.image_url(record["set_num"]), "id": source_id,
                    "source_name": data.get("name", source_id), "pieces": pieces, "distinct_parts": len({x["part"] for x in rows}),
                    "reserved": pieces if built else 0, "missing": missing, "on_hand": pieces - missing,
                    "warnings": data.get("warnings") or []},
            "rows": rows}
