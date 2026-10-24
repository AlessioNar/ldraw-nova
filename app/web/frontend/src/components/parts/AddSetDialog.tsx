import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { collectionApi, type CatalogSet, type Inventory } from "../../api";
import Dialog from "./Dialog";
import type { OnChanged } from "./changes";

/** Add a whole set by number or name: its parts list from the offline catalogue becomes a source of its own. */
export default function AddSetDialog({ onClose, onChanged }: { onClose: () => void; onChanged: OnChanged }) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<CatalogSet[] | null>(null);
  const [picked, setPicked] = useState<CatalogSet | null>(null);
  const [copies, setCopies] = useState("1");
  const [state, setState] = useState<"parts" | "built">("parts");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState<Inventory | null>(null);
  const seq = useRef(0);

  useEffect(() => {
    const mine = ++seq.current;
    const timer = setTimeout(() => {
      collectionApi.sets(query.trim()).then(r => mine === seq.current && (setResults(r.sets), setError("")),
        e => mine === seq.current && setError((e as Error).message));
    }, query ? 250 : 0);
    return () => clearTimeout(timer);
  }, [query]);

  async function add() {
    if (!picked) return;
    setBusy(true); setError("");
    try {
      const r = await collectionApi.addSet(picked.set_num, Math.max(1, Math.floor(Number(copies) || 1)), state);
      setDone(r.inventory);
      onChanged(`Added set ${picked.set_num}: ${r.inventory.total_pieces.toLocaleString()} pieces`);
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }

  if (done) return <Dialog title="Add a set" onClose={onClose}>
    <p><strong>{done.name}</strong> is now a source with {done.total_pieces.toLocaleString()} pieces in {done.distinct_parts.toLocaleString()} distinct parts.</p>
    {!!done.set?.dropped_pieces && <div className="banner" role="status">{done.set.dropped_pieces.toLocaleString()} pieces of this set have no LDraw part and are not listed.</div>}
    <div className="form-actions">
      <Link className="button" to={`/parts/${done.id}`} onClick={onClose}>View its parts</Link>
      <button type="button" onClick={() => { setDone(null); setPicked(null); }}>Add another set</button>
      <button type="button" className="primary" autoFocus onClick={onClose}>Done</button>
    </div>
  </Dialog>;

  return <Dialog title="Add a set" wide busy={busy} onClose={onClose}>
    <label className="field"><span>Set number or name</span>
      <input autoFocus type="search" value={query} placeholder="8448, or tipper truck" aria-label="Search sets" onChange={e => { setQuery(e.target.value); setPicked(null); }} /></label>
    {error && <div className="banner error" role="alert">{error}</div>}
    {!results && !error && <p className="muted"><span className="spinner" aria-hidden /> Loading the catalogue…</p>}
    {results && !results.length && <p className="muted">No set matches “{query}”.</p>}
    {results && results.length > 0 && <ul className="set-results" aria-label="Sets">
      {results.map(s => <li key={s.set_num}>
        <button type="button" aria-pressed={picked?.set_num === s.set_num} className="set-result" onClick={() => setPicked(s)}>
          <code>{s.set_num}</code><span className="set-name ellipsis">{s.name}</span>
          <span className="muted small">{s.year || ""} · {s.theme.split(" > ")[0]}</span><span className="mono small">{s.num_parts.toLocaleString()} pcs</span>
        </button></li>)}
    </ul>}
    {picked && <div className="set-picked panel">
      <strong>{picked.set_num} {picked.name}</strong>
      <p className="muted small">{picked.year} · {picked.theme} · {picked.num_parts.toLocaleString()} pieces
        {picked.dropped_pieces > 0 && `, ${picked.dropped_pieces.toLocaleString()} without an LDraw part (not listed)`}</p>
      <div className="set-options">
        <label className="field"><span>Copies</span><input type="number" min={1} max={99} value={copies} onChange={e => setCopies(e.target.value)} /></label>
        <fieldset className="set-state"><legend className="small">State</legend>
          <label className="check"><input type="radio" name="set-state" checked={state === "parts"} onChange={() => setState("parts")} /> Taken apart, the parts are loose</label>
          <label className="check"><input type="radio" name="set-state" checked={state === "built"} onChange={() => setState("built")} /> Built, still counted as owned</label>
        </fieldset>
      </div>
    </div>}
    <div className="form-actions">
      <button type="button" disabled={busy} onClick={onClose}>Cancel</button>
      <button type="button" className="primary" disabled={busy || !picked} onClick={() => void add()}>{busy ? "Adding…" : picked ? `Add ${picked.set_num}` : "Add"}</button>
    </div>
  </Dialog>;
}
