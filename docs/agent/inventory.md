# Building from the user's own parts

Read when the chat has a parts mode (Prefer or Only). IDS below is the comma separated inventory ids from the prompt.

- Search: `my_parts` (tool) or `ldraw-agent inventory search IDS "wheel" [--colour CODE]` lists owned (part, colour, quantity), most owned first. Every word must appear in the part number or description, so use distinctive words ("slope", "hinge") and read the sizes in the results; add `--colour` to see one colour. Search by role before choosing a part number from memory.
- Colour substitution: when a part is owned but not in the wanted colour, take the nearest owned colour that keeps the design readable (greys for greys, trans for trans) and recolour the model; keep the part when its shape matters.
- Reading `ldraw-agent inventory check model.mpd IDS --mode only`: one line per short (part, colour) with needed, owned and missing, then "owned in" lines naming the other colours of that part the user owns, the quickest fix. Exit code 1 in Only mode means something is short.
- Quantities: the check counts the physical bill of materials of the whole model, so every instance of a sub-assembly counts; a wheel sub-model used four times needs four sets. Fix the sub-assembly once and every instance follows.
- Unmapped: parts or colours of the user's file without an LDraw equivalent (stickers, prints, newer parts) are not counted as owned and cannot be used; do not guess an equivalent.
- Report: when the check is not complete, list the missing parts (number, colour, count). In Only mode never call the model complete while anything is short.
