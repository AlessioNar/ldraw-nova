# 04 Part palette and colours

**Goal:** the person can add parts they choose, in colours from LDraw's colour table.

**Read first:** `ldraw_tools/data/rebrickable-colours.json` and `LDConfig.ldr` (colour names), `app/web/backend/parts_steering.py` (what `my_parts` already searches), and `app/web/frontend/src/components/parts/` (the existing parts UI, to reuse).

**Build:**
- A palette panel. Search by part number or name, reusing the parts search. A "from my inventory" filter uses the chat's inventories when parts mode is on.
- A colour picker that lists LDraw codes with names and swatches. Offer only colours that exist in the table.
- "Add" places the part at the selection's connection point, or at the origin, and then runs task 01's `snap`.
- In parts mode `prefer`, warn when a part is not in the chat's inventory, but do not block. In `only` mode, block it, using the same rules the agent follows in `ldraw_tools/inventory.py`.

**Acceptance:**
- The palette shows available quantities, not owned ones: owned minus the parts already placed in the model being edited. A part with nothing left is shown as used up, and its add button is disabled in `only` mode.
- Deleting a part in the editor makes it available again in the palette straight away, with no reload.
- Every colour code offered maps to a valid LDraw colour.
- In `only` mode, adding a part the inventory cannot cover is refused with the same wording the agent sees.

**Out of scope:** changes to the parts index or the inventory format.
