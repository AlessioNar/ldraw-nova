import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { collectionApi, type CollectionBrowse, type CollectionPart, type PartDetail } from "../../api";
import { colourHex } from "../../ldrawColours";
import PartThumb from "./PartThumb";
import type { OnChanged } from "./changes";

const PAGE = 60;
const SORTS = [["qty", "Most pieces"], ["part", "Part number"], ["name", "Name"], ["colours", "Most colours"]] as const;

/** Everything one part holds across sources: edit a count, remove a line, add more, write a note. */
function PartEditor({ part, onChanged, onSaved }: { part: CollectionPart; onChanged: OnChanged; onSaved: () => void }) {
  const [detail, setDetail] = useState<PartDetail | null>(null);
  const [colours, setColours] = useState<{ code: number; name: string }[]>([]);
  const [note, setNote] = useState(part.note);
  const [newColour, setNewColour] = useState("");
  const [newQty, setNewQty] = useState("1");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => collectionApi.part(part.part).then(setDetail, e => setError((e as Error).message)), [part.part]);
  useEffect(() => { void load(); void collectionApi.colours().then(r => setColours(r.colours), () => undefined); }, [load]);

  async function run(job: () => Promise<void>) {
    setBusy(true); setError("");
    try { await job(); await load(); onSaved(); } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  const setQty = (source: string, colour: number, qty: number, was: number) => qty === was ? Promise.resolve() :
    run(async () => {
      const r = await collectionApi.ops(source, [{ op: "set", part: part.part, colour, qty }], "Edited count");
      if (r.batch != null) onChanged(qty === 0 ? `Removed ${part.part} from ${r.source.name}` : `${part.part}: ${was} to ${qty} in ${r.source.name}`, { source });
    });
  const saveNote = () => note.trim() === part.note ? Promise.resolve() : run(async () => {
    const r = await collectionApi.setNote(part.part, note);
    setNote(r.note);
  });

  return <div className="part-editor">
    {error && <div className="banner error" role="alert">{error}</div>}
    <form className="note-form" onSubmit={e => { e.preventDefault(); void saveNote(); }}>
      <label className="field"><span>Note (where it lives, e.g. “blue tub”)</span>
        <input value={note} maxLength={200} disabled={busy} placeholder="No note" onChange={e => setNote(e.target.value)} onBlur={() => void saveNote()} /></label>
    </form>
    {!detail ? <p className="muted"><span className="spinner" aria-hidden /> Loading…</p> : <>
      <table className="parts-table editor-table">
        <thead><tr><th scope="col">Colour</th><th scope="col">Source</th><th scope="col" className="num">Qty</th><th scope="col"><span className="sr-only">Remove</span></th></tr></thead>
        <tbody>{detail.rows.map(r => <tr key={`${r.source}-${r.colour}`}>
          <td><span className="swatch" style={{ background: colourHex(r.colour) }} aria-hidden /> {r.colour_name}</td>
          <td><Link to={`/parts/${r.source}`}>{r.source_name}</Link> <span className="badge">{r.kind}</span></td>
          <td className="num"><input type="number" min={1} aria-label={`Quantity of ${r.colour_name} in ${r.source_name}`} defaultValue={r.qty} disabled={busy}
            onBlur={e => { const n = Math.floor(Number(e.target.value)); if (n >= 1) void setQty(r.source, r.colour, n, r.qty); else e.target.value = String(r.qty); }}
            onKeyDown={e => e.key === "Enter" && (e.target as HTMLInputElement).blur()} /></td>
          <td><button type="button" className="danger-text" disabled={busy} aria-label={`Remove ${r.colour_name} from ${r.source_name}`} onClick={() => void setQty(r.source, r.colour, 0, r.qty)}>Remove</button></td>
        </tr>)}</tbody>
      </table>
      {detail.maybe.length > 0 && <p className="muted small">{detail.maybe.reduce((n, m) => n + m.qty, 0)} more are “maybe” pieces whose colour is not known yet (see Review); they are not counted above.</p>}
      <form className="add-line" onSubmit={e => {
        e.preventDefault();
        if (!newColour) return;
        void run(async () => {
          const r = await collectionApi.ops("loose", [{ op: "add", part: part.part, colour: Number(newColour), qty: Math.max(1, Math.floor(Number(newQty) || 1)) }], "Added by hand");
          if (r.batch != null) onChanged(`Added ${newQty} × ${part.part} to Loose parts`, { source: "loose" });
        });
      }}>
        <span className="label">Add more to Loose parts</span>
        <select aria-label="Colour to add" value={newColour} disabled={busy} onChange={e => setNewColour(e.target.value)}>
          <option value="">Colour…</option>{colours.map(c => <option key={c.code} value={c.code}>{c.name}</option>)}</select>
        <input type="number" min={1} aria-label="Quantity to add" value={newQty} disabled={busy} onChange={e => setNewQty(e.target.value)} />
        <button type="submit" disabled={busy || !newColour}>Add</button>
      </form>
    </>}
  </div>;
}

function Row({ part, open, onToggle, onChanged, onSaved }: { part: CollectionPart; open: boolean; onToggle: () => void; onChanged: OnChanged; onSaved: () => void }) {
  return <li className={`part-row ${open ? "open" : ""}`}>
    <div className="part-row-main">
      <PartThumb part={part.part} />
      <div className="part-info">
        <p className="part-title"><code>{part.part}</code> <span>{part.description || "Unknown part"}</span></p>
        <p className="part-meta muted small">{[part.category, part.size].filter(Boolean).join(" · ")}{part.sources.length > 0 && ` · ${part.sources.map(s => s.name).join(", ")}`}</p>
        <p className="chips" aria-label="Colours">{part.colours.map(c => <span className="chip" key={c.colour} title={c.name}>
          <span className="swatch" style={{ background: colourHex(c.colour) }} aria-hidden />{c.name} <strong className="mono">{c.qty.toLocaleString()}</strong></span>)}
          {part.maybe > 0 && <span className="chip maybe" title="Colour not known yet: not counted">maybe <strong className="mono">{part.maybe.toLocaleString()}</strong></span>}</p>
        {part.note && !open && <p className="part-note small"><span className="muted">Note:</span> {part.note}</p>}
      </div>
      <div className="part-total"><strong className="mono">{part.total.toLocaleString()}</strong><span className="muted small">pieces</span>
        <button type="button" aria-expanded={open} onClick={onToggle}>{open ? "Close" : "Edit"}</button></div>
    </div>
    {open && <PartEditor part={part} onChanged={onChanged} onSaved={onSaved} />}
  </li>;
}

type Filters = { q: string; colour: string; category: string; size: string; source: string; sort: string; maybe: boolean };
const NONE: Filters = { q: "", colour: "", category: "", size: "", source: "", sort: "qty", maybe: false };

/** The whole collection grouped by part: search, filters by colour, category, size and source, counts, edit in place. */
export default function CollectionView({ version, source, onSource, onChanged, onReview, onAdd }:
  { version: number; source: string; onSource: (id: string) => void; onChanged: OnChanged; onReview: () => void; onAdd: () => void }) {
  const [f, setF] = useState<Filters>({ ...NONE, source });
  const [applied, setApplied] = useState(f.q);
  const [data, setData] = useState<CollectionBrowse | null>(null);
  const [facets, setFacets] = useState<CollectionBrowse["facets"] | null>(null);
  const [limit, setLimit] = useState(PAGE);
  const [openPart, setOpenPart] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);
  const seq = useRef(0);

  useEffect(() => setF(prev => ({ ...prev, source })), [source]);
  useEffect(() => { const t = setTimeout(() => setApplied(f.q.trim()), 250); return () => clearTimeout(t); }, [f.q]);
  useEffect(() => setLimit(PAGE), [applied, f.colour, f.category, f.size, f.source, f.sort, f.maybe]);
  useEffect(() => {
    const mine = ++seq.current;
    setLoading(true);
    collectionApi.browse({ q: applied, colour: f.colour ? Number(f.colour) : null, category: f.category, size: f.size, source: f.source, sort: f.sort, maybe: f.maybe, limit })
      .then(r => {
        if (mine !== seq.current) return;
        setData(r); setError(""); setFacets(r.facets);
      }, e => mine === seq.current && setError((e as Error).message))
      .finally(() => mine === seq.current && setLoading(false));
  }, [applied, f.colour, f.category, f.size, f.source, f.sort, f.maybe, limit, version, tick]);

  const set = (patch: Partial<Filters>) => setF(prev => ({ ...prev, ...patch }));
  const filtered = !!(applied || f.colour || f.category || f.size || f.source || f.maybe);
  const clear = () => { setF({ ...NONE }); onSource(""); };
  const c = data?.collection;
  const empty = c && c.pieces === 0 && !data?.totals.review_items;

  return <section className="collection" aria-label="Collection">
    {c && <p className="collection-summary" aria-live="polite">
      <strong>{c.pieces.toLocaleString()}</strong> pieces · <strong>{c.parts.toLocaleString()}</strong> parts · <strong>{c.colours}</strong> colours · {c.sources} source{c.sources === 1 ? "" : "s"}
      {data.totals.review_items > 0 && <> · <button type="button" className="link-button" onClick={onReview}>{data.totals.review_pieces.toLocaleString()} maybe pieces to review</button></>}
    </p>}
    {empty && <div className="panel parts-empty-state">
      <h2>Your collection is empty</h2>
      <p className="muted">Add a whole set by its number, type what you have (“20 red 2x4 bricks”), or import a Rebrickable or Brickit file.</p>
      <button type="button" className="primary" onClick={onAdd}>+ Add parts</button>
    </div>}
    {!empty && <>
      <div className="parts-filters collection-filters">
        <input type="search" aria-label="Search the collection" placeholder="Search: 2x4 red brick, 3001, tub" value={f.q} onChange={e => set({ q: e.target.value })} />
        <select aria-label="Colour" value={f.colour} onChange={e => set({ colour: e.target.value })}>
          <option value="">All colours</option>{facets?.colours.map(x => <option key={x.colour} value={x.colour}>{x.name} ({x.qty.toLocaleString()})</option>)}</select>
        <select aria-label="Category" value={f.category} onChange={e => set({ category: e.target.value })}>
          <option value="">All categories</option>{facets?.categories.map(x => <option key={x.category} value={x.category}>{x.category} ({x.parts})</option>)}</select>
        <select aria-label="Size" value={f.size} onChange={e => set({ size: e.target.value })}>
          <option value="">Any size</option>{facets?.sizes.map(x => <option key={x.size} value={x.size}>{x.size.replace(/x/g, " × ")} ({x.parts})</option>)}</select>
        <select aria-label="Source" value={f.source} onChange={e => { set({ source: e.target.value }); onSource(e.target.value); }}>
          <option value="">All sources</option>{facets?.sources.map(x => <option key={x.id} value={x.id}>{x.name} ({x.pieces.toLocaleString()})</option>)}</select>
        <select aria-label="Sort" value={f.sort} onChange={e => set({ sort: e.target.value })}>{SORTS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>
        <label className="check"><input type="checkbox" checked={f.maybe} onChange={e => set({ maybe: e.target.checked })} /> Has maybe pieces</label>
        {filtered && <button type="button" className="link-button" onClick={clear}>Clear filters</button>}
      </div>
      {filtered && data && <p className="muted small" aria-live="polite">{data.total.toLocaleString()} part{data.total === 1 ? "" : "s"} · {data.totals.pieces.toLocaleString()} pieces match</p>}
      {error && <div className="banner error" role="alert">{error}</div>}
      {!data && !error && <p className="muted"><span className="spinner" aria-hidden /> Loading…</p>}
      {data && !data.parts.length && <p className="muted">{filtered ? "Nothing matches these filters." : "No parts yet."}</p>}
      {data && data.parts.length > 0 && <ul className={`part-list ${loading ? "loading" : ""}`} aria-label="Parts">
        {data.parts.map(p => <Row key={p.part} part={p} open={openPart === p.part} onToggle={() => setOpenPart(openPart === p.part ? "" : p.part)} onChanged={onChanged} onSaved={() => setTick(t => t + 1)} />)}
      </ul>}
      {data && data.total > data.parts.length && <div className="pager">
        <span className="muted small">Showing {data.parts.length.toLocaleString()} of {data.total.toLocaleString()}</span>
        <button type="button" onClick={() => setLimit(l => l + PAGE)}>Show more</button>
      </div>}
    </>}
  </section>;
}
