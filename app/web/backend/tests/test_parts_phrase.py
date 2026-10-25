import parts_phrase

INVENTORIES = [{"id": "fire-station", "name": "Fire Station"}, {"id": "loose", "name": "Loose"}]


def test_offer_names_one_inventory(monkeypatch):
    monkeypatch.setattr(parts_phrase.inventories, "list_inventories", lambda: INVENTORIES)
    got = parts_phrase.offer("use the parts from my fire station")
    assert got["route"] == "clarify" and got["parts_offer"] == {"id": "fire-station", "name": "Fire Station", "mode": "prefer"}
    assert parts_phrase.offer("Only use parts of the Fire Station.")["parts_offer"]["mode"] == "only"


def test_offer_ignores_other_text_and_unknown_sets(monkeypatch):
    monkeypatch.setattr(parts_phrase.inventories, "list_inventories", lambda: INVENTORIES)
    assert parts_phrase.offer("make the roof red") is None
    assert parts_phrase.offer("use the parts from my zoo") is None
