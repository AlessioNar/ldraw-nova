import { useEffect, useMemo, useState } from "react";
import { paletteApi } from "../../api";
import type { EditorColour, PaletteItem, PartsMode } from "../../api";
import { addEdits, addStatus, availableNow } from "./paletteLogic";
import type { PlanLike, SelectionPoint, UsedRow } from "./paletteLogic";

/** Parts palette for the manual editor: search, pick a colour, add. Quantities follow the live model, never a reload. */
export default function Palette({ chatId, plan, mode, usedRows, selection, onAdd, disabled }: {
  chatId: string;
  plan: PlanLike;
  mode: PartsMode;
  /** The editor view's current `parts.rows`; passing fresh rows after every edit keeps "available" live. */
  usedRows: UsedRow[];
  selection?: SelectionPoint | null;
  /** Apply the edits (the editor view sends them with its base revision); reject with an Error to show its message. */
  onAdd: (edits: Record<string, unknown>[]) => Promise<void>;
  disabled?: boolean;
}) {
  const [colours, setColours] = useState<EditorColour[]>([]);
  const [colour, setColour] = useState(4);
  const [colourFilter, setColourFilter] = useState("");
  const [query, setQuery] = useState("");
  const [mine, setMine] = useState(false);
  const [items, setItems] = useState<PaletteItem[]>([]);
  const [more, setMore] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => { paletteApi.colours().then(r => setColours(r.colours)).catch(e => setMessage(String(e.message ?? e))); }, []);
  useEffect(() => {
    if (!query.trim() && !mine) { setItems([]); setMore(false); return; }
    let stale = false;
    const timer = setTimeout(() => {
      paletteApi.search(chatId, { query: query.trim(), inventoryOnly: mine, limit: 20 })
        .then(r => { if (!stale) { setItems(r.items); setMore(r.more); setMessage(null); } })
        .catch(e => { if (!stale) { setItems([]); setMessage(String(e.message ?? e)); } });
    }, 250);
    return () => { stale = true; clearTimeout(timer); };
  }, [chatId, query, mine]);

  const shown = useMemo(() => {
    const f = colourFilter.trim().toLowerCase();
    return f ? colours.filter(c => c.name.toLowerCase().includes(f) || String(c.code) === f) : colours;
  }, [colours, colourFilter]);
  const chosen = colours.find(c => c.code === colour);

  async function add(item: PaletteItem) {
    setBusy(true); setMessage(null);
    try {
      try { await onAdd(addEdits(plan, item.part, colour, selection, true)); }
      catch (e) {
        if (!/snap/i.test(String((e as Error).message))) throw e;
        await onAdd(addEdits(plan, item.part, colour, selection, false));   // nothing to snap to: place it unsnapped
      }
    } catch (e) { setMessage(String((e as Error).message ?? e)); }
    finally { setBusy(false); }
  }

  return (
    <aside className="editor-palette" aria-label="Part palette">
      <div className="editor-palette-search">
        <input type="search" placeholder="Part number or name" value={query} onChange={e => setQuery(e.target.value)} />
        {mode !== "off" && (
          <label><input type="checkbox" checked={mine} onChange={e => setMine(e.target.checked)} /> From my inventory</label>
        )}
      </div>

      <div className="editor-palette-colours">
        <input type="search" placeholder="Filter colours" value={colourFilter} onChange={e => setColourFilter(e.target.value)} />
        <div className="editor-palette-chosen">
          {chosen && <><span className="editor-swatch" style={{ background: chosen.hex }} /> {chosen.name} ({chosen.code})</>}
        </div>
        <div className="editor-palette-swatches" role="listbox" aria-label="Colour">
          {shown.map(c => (
            <button key={c.code} type="button" role="option" aria-selected={c.code === colour} title={`${c.name} (${c.code})`}
              className={`editor-swatch${c.code === colour ? " selected" : ""}`} style={{ background: c.hex }}
              onClick={() => setColour(c.code)} />
          ))}
        </div>
      </div>

      {message && <p className="editor-palette-message" role="alert">{message}</p>}
      <ul className="editor-palette-results">
        {items.map(item => {
          const row = item.colours.find(c => c.colour === colour);
          const status = addStatus(mode, row, usedRows, item.part);
          const total = item.colours.reduce((sum, c) => sum + availableNow(c, usedRows, item.part), 0);
          const usedUp = mode !== "off" && item.in_inventory && total === 0;
          return (
            <li key={item.part} className={usedUp ? "used-up" : undefined}>
              <div>
                <strong>{item.part}</strong> <span>{item.description}</span>
                {mode !== "off" && (
                  <div className="editor-palette-qty">
                    {usedUp ? "Used up" : item.in_inventory ? `${total} available` : "Not in inventory"}
                    {item.in_inventory && !usedUp && <> · in {chosen?.name ?? colour}: {status.available}</>}
                  </div>
                )}
                {status.warning && <div className="editor-palette-warning">{status.warning}{status.allowed ? "" : ": not allowed in Only mode"}</div>}
              </div>
              <button type="button" disabled={disabled || busy || !status.allowed} onClick={() => add(item)}>Add</button>
            </li>
          );
        })}
      </ul>
      {more && <p className="editor-palette-more">More matches: refine the search.</p>}
    </aside>
  );
}
