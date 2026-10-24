import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { collectionApi, inventoryApi, timeAgo, type Inventory } from "../api";
import AddPartsMenu, { type AddWay } from "../components/parts/AddPartsMenu";
import AddSetDialog from "../components/parts/AddSetDialog";
import BuildablePanel from "../components/parts/BuildablePanel";
import CollectionView from "../components/parts/CollectionView";
import InventoryDetailView from "../components/parts/InventoryDetailView";
import QuickAddDialog from "../components/parts/QuickAddDialog";
import ReviewQueue from "../components/parts/ReviewQueue";
import SourceHistory from "../components/parts/SourceHistory";
import SourcesView from "../components/parts/SourcesView";
import UploadDialog from "../components/parts/UploadDialog";
import type { OnChanged } from "../components/parts/changes";

type View = "collection" | "sources" | "review" | "build";
const VIEWS: View[] = ["collection", "sources", "review", "build"];
type Dialog = null | { kind: AddWay; replace?: string };
type Notice = { text: string; source?: string } | null;

/** My Parts: the collection (every source added together), its sources, the review queue, what can I build; and one source's parts at /parts/:id. */
export default function Parts() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [search, setSearch] = useSearchParams();
  const asked = search.get("view") as View | null;
  const view: View = asked && VIEWS.includes(asked) ? asked : "collection";
  const sourceFilter = search.get("source") ?? "";
  const [sources, setSources] = useState<Inventory[] | null>(null);
  const [error, setError] = useState("");
  const [version, setVersion] = useState(0);
  const [dialog, setDialog] = useState<Dialog>(null);
  const [notice, setNotice] = useState<Notice>(null);
  const [renaming, setRenaming] = useState(false);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => collectionApi.overview().then(r => { setSources(r.sources); setError(""); }, e => setError((e as Error).message)), []);
  useEffect(() => { void load(); }, [load, version]);
  useEffect(() => setRenaming(false), [id]);
  useEffect(() => {
    if (!notice) return;
    const t = setTimeout(() => setNotice(null), 15000);
    return () => clearTimeout(t);
  }, [notice]);

  const changed: OnChanged = useCallback((text, undo) => { setNotice({ text, source: undo?.source }); setVersion(v => v + 1); }, []);
  const go = (v: View, extra: Record<string, string> = {}) => setSearch(v === "collection" && !Object.keys(extra).length ? {} : { view: v, ...extra });
  const real = (sources ?? []).filter(s => !s.virtual);
  const reviewPieces = real.reduce((n, s) => n + (s.review_pieces ?? 0), 0);
  const current = id ? real.find(i => i.id === id) : undefined;

  async function undo() {
    if (!notice?.source) return;
    try { const r = await collectionApi.undo(notice.source); setNotice({ text: `Undid: ${r.summary}` }); setVersion(v => v + 1); }
    catch (e) { setNotice({ text: (e as Error).message }); }
  }
  async function remove(inv: Inventory) {
    if (!confirm(`Delete "${inv.name}" and its history?\n\nChats that use it will no longer steer towards these parts.`)) return;
    setBusy(true);
    try { await inventoryApi.remove(inv.id); setVersion(v => v + 1); if (id === inv.id) navigate("/parts"); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  async function rename(e: FormEvent) {
    e.preventDefault();
    if (!current || !draft.trim()) return;
    setBusy(true);
    try { await inventoryApi.rename(current.id, draft.trim()); setRenaming(false); setVersion(v => v + 1); }
    catch (err) { setError((err as Error).message); }
    finally { setBusy(false); }
  }

  const dialogs = <>
    {dialog?.kind === "quick" && <QuickAddDialog onClose={() => setDialog(null)} onChanged={changed} />}
    {dialog?.kind === "set" && <AddSetDialog onClose={() => setDialog(null)} onChanged={changed} />}
    {dialog?.kind === "import" && <UploadDialog sources={sources ?? []} replaceId={dialog.replace} onClose={() => setDialog(null)} onChanged={changed} />}
  </>;
  const toast = notice && <div className="parts-notice" role="status">
    <span>{notice.text}</span>
    {notice.source && <button type="button" onClick={() => void undo()}>Undo</button>}
    <button type="button" className="icon-button" aria-label="Dismiss" onClick={() => setNotice(null)}>✕</button>
  </div>;

  if (id) {
    return <div className="page parts-page">
      <p className="small"><Link to="/parts?view=sources">← Sources</Link></p>
      {error && <div className="banner error" role="alert">{error}</div>}
      {sources && !current && !error && <p className="muted">This source no longer exists. <Link to="/parts">Back to My Parts</Link></p>}
      {current && <>
        <header className="page-head">
          {renaming ? <form className="rename-form" onSubmit={rename}>
            <input autoFocus aria-label="Source name" value={draft} maxLength={80} onChange={e => setDraft(e.target.value)} onKeyDown={e => e.key === "Escape" && setRenaming(false)} />
            <button type="submit" className="primary" disabled={busy || !draft.trim()}>Save</button>
            <button type="button" onClick={() => setRenaming(false)}>Cancel</button>
          </form> : <div><h1>{current.name}</h1><p className="muted small">
            {current.kind === "set" && current.set ? `Set ${current.set.set_num} · ${current.set.copies} ${current.set.copies === 1 ? "copy" : "copies"} · ` : current.kind === "loose" ? "Manual and quick adds · " : `${current.source_filename ?? "No source file"} · `}added {timeAgo(current.created_at)}</p></div>}
          <div className="head-actions">
            {(current.kind ?? "import") === "import" && <button type="button" disabled={busy} onClick={() => setDialog({ kind: "import", replace: current.id })}>Replace file</button>}
            <button type="button" disabled={busy || renaming || current.kind === "loose"} onClick={() => { setDraft(current.name); setRenaming(true); }}>Rename</button>
            <button type="button" className="danger-text" disabled={busy} onClick={() => void remove(current)}>Delete</button>
          </div>
        </header>
        <InventoryDetailView id={current.id} key={`${current.id}-${version}`} />
        <SourceHistory source={current.id} version={version} onChanged={changed} />
      </>}
      {dialogs}{toast}
    </div>;
  }

  return <div className="page parts-page">
    <header className="page-head"><div><h1>My Parts</h1>
      <p className="muted">Your collection: every source added together. In a chat, choose Prefer or Only my parts and the agent designs with them.</p></div>
      <div className="head-actions"><AddPartsMenu onPick={way => setDialog({ kind: way })} /></div></header>
    {error && <div className="banner error" role="alert">{error}</div>}
    {!sources && !error && <p className="muted"><span className="spinner" aria-hidden /> Loading…</p>}
    {sources && <>
      <div className="segmented parts-tabs" role="group" aria-label="My Parts view">
        <button type="button" aria-pressed={view === "collection"} onClick={() => go("collection")}>Collection</button>
        <button type="button" aria-pressed={view === "sources"} onClick={() => go("sources")}>Sources ({real.length})</button>
        <button type="button" aria-pressed={view === "review"} onClick={() => go("review")}>Review{reviewPieces > 0 ? ` (${reviewPieces.toLocaleString()})` : ""}</button>
        <button type="button" aria-pressed={view === "build"} onClick={() => go("build")}>What can I build?</button>
      </div>
      {view === "collection" && <CollectionView version={version} source={sourceFilter} onSource={s => setSearch(s ? { source: s } : {})}
        onChanged={changed} onReview={() => go("review")} onAdd={() => setDialog({ kind: "quick" })} />}
      {view === "sources" && <SourcesView sources={sources} version={version} onChanged={changed} onRemoved={() => setVersion(v => v + 1)}
        onShow={s => go("collection", { source: s })} onReplace={s => setDialog({ kind: "import", replace: s })} />}
      {view === "review" && <ReviewQueue version={version} onChanged={changed} />}
      {view === "build" && (real.length ? <BuildablePanel inventories={real} /> : <p className="muted">Add some parts first, then see which set models you can build.</p>)}
    </>}
    {dialogs}{toast}
  </div>;
}
