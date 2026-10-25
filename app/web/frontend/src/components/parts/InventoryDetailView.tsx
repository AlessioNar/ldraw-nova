import { useEffect, useState } from "react";
import { inventoryApi, type InventoryDetail } from "../../api";
import { colourHex } from "../../ldrawColours";
import ChangeDialog from "./ChangeDialog";
import type { OnChanged } from "./changes";

const PAGE = 50;

/** One inventory's parts: search by part number or description, filter by colour, page through. */
export default function InventoryDetailView({ id, onChanged }: { id: string; onChanged: OnChanged }) {
  const [query, setQuery] = useState("");
  const [applied, setApplied] = useState("");
  const [colour, setColour] = useState<number | null>(null);
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState<InventoryDetail | null>(null);
  const [colours, setColours] = useState<InventoryDetail["top_colours"]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [changing, setChanging] = useState<InventoryDetail["rows"] | null>(null);

  useEffect(() => { const t = setTimeout(() => { setApplied(query.trim()); setOffset(0); }, 250); return () => clearTimeout(t); }, [query]);
  useEffect(() => {
    let alive = true;
    setLoading(true);
    inventoryApi.detail(id, { query: applied, colour, limit: PAGE, offset }).then(r => {
      if (!alive) return;
      setData(r); setError("");
      // The colour list describes the whole inventory, so keep the unfiltered one.
      if (!applied && colour == null) setColours(r.top_colours);
    }, e => alive && setError((e as Error).message)).finally(() => alive && setLoading(false));
    return () => { alive = false; };
  }, [id, applied, colour, offset]);
  useEffect(() => { setQuery(""); setApplied(""); setColour(null); setOffset(0); setData(null); setColours([]); setPicked(new Set()); }, [id]);
  const keyOf = (r: { part: string; colour: number }) => `${r.part}/${r.colour}`;
  const toggle = (key: string) => setPicked(prev => { const next = new Set(prev); if (!next.delete(key)) next.add(key); return next; });

  const total = data?.total ?? 0;
  const inv = data?.inventory;
  const filtered = !!applied || colour != null;
  return <section className="inventory-detail" aria-label="Inventory parts">
    {inv && <p className="muted small">
      {inv.total_pieces.toLocaleString()} pieces · {inv.distinct_parts.toLocaleString()} distinct parts · {inv.distinct_colours.toLocaleString()} colours
      {inv.spare_pieces > 0 && ` · ${inv.spare_pieces.toLocaleString()} spares`}
      {(inv.unmapped_parts > 0 || inv.unmapped_colours > 0) && <span className="warn-text"> · {inv.unmapped_parts} unrecognised parts, {inv.unmapped_colours} unrecognised colours (the agent will not see these)</span>}
    </p>}
    <div className="parts-filters">
      <input type="search" aria-label="Search parts" placeholder="Search part number or description" value={query} onChange={e => setQuery(e.target.value)} />
      <select aria-label="Colour" value={colour ?? ""} onChange={e => { setColour(e.target.value === "" ? null : Number(e.target.value)); setOffset(0); }}>
        <option value="">All colours</option>
        {colours.map(c => <option key={c.colour} value={c.colour}>{c.colour_name} ({c.qty.toLocaleString()})</option>)}
        {colour != null && !colours.some(c => c.colour === colour) && <option value={colour}>Colour {colour}</option>}
      </select>
    </div>
    {error && <div className="banner error" role="alert">{error}</div>}
    {!data && !error && <p className="muted"><span className="spinner" aria-hidden /> Loading…</p>}
    {data && <>
      {picked.size > 0 && <div className="bulk-bar" role="status">
        <span>{picked.size} line{picked.size === 1 ? "" : "s"} selected</span>
        <button type="button" className="primary" onClick={() => setChanging(data.rows.filter(r => picked.has(keyOf(r))))}>Change colour or part…</button>
        <button type="button" className="link-button" onClick={() => setPicked(new Set())}>Clear selection</button>
      </div>}
      <div className={`table-wrap ${loading ? "loading" : ""}`}>
        <table className="parts-table">
          <thead><tr><th scope="col"><input type="checkbox" aria-label="Select all lines on this page" checked={data.rows.length > 0 && data.rows.every(r => picked.has(keyOf(r)))}
            onChange={e => setPicked(prev => { const next = new Set(prev); data.rows.forEach(r => e.target.checked ? next.add(keyOf(r)) : next.delete(keyOf(r))); return next; })} /></th><th scope="col">Part</th><th scope="col">Description</th><th scope="col">Colour</th><th scope="col" className="num">Qty</th><th scope="col"><span className="sr-only">Change</span></th></tr></thead>
          <tbody>{data.rows.map(r => <tr key={`${r.part}-${r.colour}`}>
            <td><input type="checkbox" aria-label={`Select ${r.part} ${r.colour_name}`} checked={picked.has(keyOf(r))} onChange={() => toggle(keyOf(r))} /></td>
            <td><code>{r.part}</code></td>
            <td>{r.description || <span className="muted">—</span>}</td>
            <td><span className="swatch" style={{ background: colourHex(r.colour) }} aria-hidden /> {r.colour_name}</td>
            <td className="num">{r.qty.toLocaleString()}</td>
            <td><button type="button" aria-label={`Change ${r.part} ${r.colour_name}`} onClick={() => setChanging([r])}>Change…</button></td>
          </tr>)}</tbody>
        </table>
      </div>
      {!data.rows.length && <p className="muted">{filtered ? "No parts match this search." : "This inventory has no parts."}</p>}
      {total > 0 && <div className="pager">
        <span className="muted small" aria-live="polite">{offset + 1}–{Math.min(offset + PAGE, total)} of {total.toLocaleString()}</span>
        <button type="button" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>Previous</button>
        <button type="button" disabled={offset + PAGE >= total} onClick={() => setOffset(offset + PAGE)}>Next</button>
      </div>}
    </>}
    {changing && <ChangeDialog source={id} sourceName={inv?.name ?? id} lines={changing} onClose={() => setChanging(null)} onChanged={onChanged} />}
  </section>;
}
