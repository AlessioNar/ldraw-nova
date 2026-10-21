import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { inventoryApi, timeAgo, type Inventory } from "../api";
import BuildablePanel from "../components/parts/BuildablePanel";
import InventoryDetailView from "../components/parts/InventoryDetailView";
import UploadDialog from "../components/parts/UploadDialog";

/** The user's named parts inventories, and one inventory's contents at /parts/:id. */
export default function Parts() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [search, setSearch] = useSearchParams();
  const tab = search.get("view") === "build" ? "build" : "lists";
  const [items, setItems] = useState<Inventory[] | null>(null);
  const [error, setError] = useState("");
  const [uploading, setUploading] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => inventoryApi.list().then(r => { setItems(r.inventories); setError(""); }, e => setError((e as Error).message)), []);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => setRenaming(false), [id]);

  const current = id ? items?.find(i => i.id === id) : undefined;

  async function remove(inv: Inventory) {
    if (!confirm(`Delete "${inv.name}"?\n\nChats that use it will no longer steer towards these parts.`)) return;
    setBusy(true);
    try { await inventoryApi.remove(inv.id); await load(); if (id === inv.id) navigate("/parts"); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  async function rename(e: FormEvent) {
    e.preventDefault();
    if (!current || !draft.trim()) return;
    setBusy(true);
    try { await inventoryApi.rename(current.id, draft.trim()); setRenaming(false); await load(); }
    catch (err) { setError((err as Error).message); }
    finally { setBusy(false); }
  }

  const upload = <button type="button" className="primary" onClick={() => setUploading(true)}>+ Upload parts list</button>;
  const dialog = uploading && <UploadDialog onClose={() => setUploading(false)} onImported={() => void load()} />;

  if (id) {
    return <div className="page parts-page">
      <p className="small"><Link to="/parts">← My Parts</Link></p>
      {error && <div className="banner error" role="alert">{error}</div>}
      {items && !current && !error && <p className="muted">This inventory no longer exists. <Link to="/parts">Back to My Parts</Link></p>}
      {current && <>
        <header className="page-head">
          {renaming ? <form className="rename-form" onSubmit={rename}>
            <input autoFocus aria-label="Inventory name" value={draft} maxLength={80} onChange={e => setDraft(e.target.value)} onKeyDown={e => e.key === "Escape" && setRenaming(false)} />
            <button type="submit" className="primary" disabled={busy || !draft.trim()}>Save</button>
            <button type="button" onClick={() => setRenaming(false)}>Cancel</button>
          </form> : <div><h1>{current.name}</h1><p className="muted small">{current.source_filename ?? "No source file"} · added {timeAgo(current.created_at)}</p></div>}
          <div className="head-actions">
            <button type="button" disabled={busy || renaming} onClick={() => { setDraft(current.name); setRenaming(true); }}>Rename</button>
            <button type="button" className="danger-text" disabled={busy} onClick={() => void remove(current)}>Delete</button>
          </div>
        </header>
        <InventoryDetailView id={current.id} />
      </>}
      {dialog}
    </div>;
  }

  return <div className="page parts-page">
    <header className="page-head"><div><h1>My Parts</h1>
      <p className="muted">Upload the bricks you own as a Rebrickable CSV. In a chat, choose Prefer or Only my parts and the agent designs with them.</p></div>
      <div className="head-actions">{upload}</div></header>
    {error && <div className="banner error" role="alert">{error}</div>}
    {!!items?.length && <div className="segmented parts-tabs" role="group" aria-label="My Parts view">
      <button type="button" aria-pressed={tab === "lists"} onClick={() => setSearch({})}>Parts lists</button>
      <button type="button" aria-pressed={tab === "build"} onClick={() => setSearch({ view: "build" })}>What can I build?</button>
    </div>}
    {!!items?.length && tab === "build" && <BuildablePanel inventories={items} />}
    {!items && !error && <p className="muted"><span className="spinner" aria-hidden /> Loading…</p>}
    {items && !items.length && !error && <div className="panel parts-empty-state">
      <h2>No parts lists yet</h2>
      <p className="muted">Export your inventory from Rebrickable (My Parts, Export, Rebrickable CSV) or any tool that writes Part, Color and Quantity columns, then upload it here.</p>
      {upload}
    </div>}
    {!!items?.length && tab === "lists" && <div className="card-grid parts-grid">{items.map(inv => <article className="panel inventory-card" key={inv.id}>
      <h2 className="ellipsis"><Link to={`/parts/${inv.id}`}>{inv.name}</Link></h2>
      <p className="muted small ellipsis">{inv.source_filename ?? "No source file"} · {timeAgo(inv.created_at)}</p>
      <p className="inventory-pieces"><strong>{inv.total_pieces.toLocaleString()}</strong> pieces</p>
      <p className="muted small">{inv.distinct_parts.toLocaleString()} distinct parts · {inv.distinct_colours.toLocaleString()} colours</p>
      {(inv.unmapped_parts > 0 || inv.unmapped_colours > 0) && <p className="small"><span className="badge warn">{inv.unmapped_parts + inv.unmapped_colours} unrecognised</span></p>}
      <div className="card-actions">
        <Link className="button" to={`/parts/${inv.id}`}>View parts</Link>
        <button type="button" className="danger-text" disabled={busy} aria-label={`Delete ${inv.name}`} onClick={() => void remove(inv)}>Delete</button>
      </div>
    </article>)}</div>}
    {dialog}
  </div>;
}
