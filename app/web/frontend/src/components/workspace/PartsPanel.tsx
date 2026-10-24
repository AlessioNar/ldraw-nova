import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { paletteApi, type PaletteItem } from "../../api";
import { colourHex } from "../../ldrawColours";
import { useColours } from "../../workspace/colours";
import type { Workspace } from "../../workspace/useWorkspace";
import ColourPicker from "./ColourPicker";

type Row = { part: string; description: string; colour: number; colourName: string; owned: number; used: number };
const LIMIT = 40;

/** Debounced palette search against the build's editor session; refetches after canvas edits so counts stay live. */
function usePalette(id: string, query: string, inventoryOnly: boolean, tick: number) {
  const [state, setState] = useState<{ items: PaletteItem[]; more: boolean; error: string | null; done: boolean }>({ items: [], more: false, error: null, done: false });
  useEffect(() => {
    let stale = false;
    const t = setTimeout(() => {
      paletteApi.search(`b-${id}`, { query: query.trim(), inventoryOnly, limit: LIMIT })
        .then((r) => { if (!stale) setState({ items: r.items, more: r.more, error: null, done: true }); })
        .catch((e: Error) => { if (!stale) setState({ items: [], more: false, error: e.message, done: true }); });
    }, query ? 250 : tick ? 500 : 0);
    return () => { stale = true; clearTimeout(t); };
  }, [id, query, inventoryOnly, tick]);
  return state;
}

const remainingBar = (left: number, owned: number) => `${owned > 0 ? Math.max(0, Math.min(100, (left / owned) * 100)) : 0}%`;

/** Owned parts first, with what is left after what the draft uses, then the full library. Clicking a row arms the Add tool. */
export default function PartsPanel({ ws }: { ws: Workspace }) {
  const [query, setQuery] = useState("");
  const colours = useColours();
  const mine = usePalette(ws.id, query, true, ws.editTick);
  const library = usePalette(ws.id, query, false, ws.editTick);
  const disabled = ws.mode !== "edit" || !!ws.viewing;
  const colourName = colours.find((c) => c.code === ws.colour)?.name ?? `Colour ${ws.colour}`;

  const rows = useMemo<Row[]>(() => mine.items.flatMap((it) =>
    it.colours.filter((c) => c.owned > 0).map((c) => ({ part: it.part, description: it.description, colour: c.colour, colourName: c.name, owned: c.owned, used: c.used }))), [mine.items]);
  const ownedParts = new Set(mine.items.map((i) => i.part));

  return (
    <section className="ws-panel" role="tabpanel" aria-label="Parts">
      <input type="search" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Part number or name" aria-label="Search parts" />
      <div className="ws-row-label"><span className="label">Colour for new bricks</span><span className="mono small">{colourName}</span></div>
      <ColourPicker value={ws.colour} onPick={ws.setColour} label="Colour for new bricks" />

      <div className="ws-row-label"><span className="label">Your parts</span><span className="muted small">left after this draft, live</span></div>
      {mine.error && <p className="ws-warn small">{mine.error}</p>}
      <ul className="ws-parts">
        {rows.map((r) => {
          const left = r.owned - r.used;
          return (
            <li key={`${r.part}-${r.colour}`}>
              <button className={`ws-part${left <= 0 ? (left < 0 ? " zero" : " low") : left / r.owned < 0.25 ? " low" : ""}`} disabled={disabled} onClick={() => ws.addPart(r.part, r.colour)}
                title={disabled ? "Switch to Edit mode to add parts" : `Add ${r.description} in ${r.colourName}`}>
                <span className="ws-pic" style={{ background: colourHex(r.colour) }} />
                <span className="ws-nm">{r.description || r.part}<small>{r.part} · {r.colourName}</small></span>
                <span className="ws-left">{left < 0 ? `missing ${-left}` : `${left} left`}
                  <i><b style={{ width: remainingBar(left, r.owned) }} /></i></span>
              </button>
            </li>
          );
        })}
        {mine.done && rows.length === 0 && !mine.error && (
          <li className="ws-none muted small">{query ? "None of your parts match." : <>No parts list yet. <Link to="/parts">Add one in My Parts</Link> and its parts show up here.</>}</li>
        )}
      </ul>

      <div className="ws-row-label"><span className="label">Full library</span>{library.more && <span className="muted small">refine the search for more</span>}</div>
      {library.error && <p className="ws-warn small">{library.error}</p>}
      <ul className="ws-parts">
        {library.items.map((it) => (
          <li key={it.part}>
            <button className="ws-part" disabled={disabled} onClick={() => ws.addPart(it.part, ws.colour)} title={disabled ? "Switch to Edit mode to add parts" : `Add ${it.description} in ${colourName}`}>
              <span className="ws-pic" style={{ background: colourHex(ws.colour) }} />
              <span className="ws-nm">{it.description || it.part}<small>{it.part}</small></span>
              {!ownedParts.has(it.part) && !it.in_inventory && <span className="ws-tag">not owned</span>}
            </button>
          </li>
        ))}
        {library.done && library.items.length === 0 && !library.error && <li className="ws-none muted small">{query ? "No part matches." : "Search for a part by number or name."}</li>}
      </ul>
      <p className="muted small">Parts you do not own are marked. They still go in the model, and the parts check counts them as missing.</p>
    </section>
  );
}
