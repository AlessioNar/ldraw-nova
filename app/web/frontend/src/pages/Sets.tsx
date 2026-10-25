import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { collectionApi, type Inventory } from "../api";
import AddSetDialog from "../components/parts/AddSetDialog";
import BuildablePanel from "../components/parts/BuildablePanel";
import QuickAddDialog from "../components/parts/QuickAddDialog";
import ReservedView from "../components/parts/ReservedView";
import SetsView from "../components/parts/SetsView";
import type { OnChanged } from "../components/parts/changes";

type View = "sets" | "reserved" | "build";
const VIEWS: View[] = ["sets", "reserved", "build"];
type Dialog = null | "set" | "quick";
type Notice = { text: string; source?: string } | null;

/** My Sets: every set I own as a card with its picture and part counts; one set's page is at /sets/:id. */
export default function Sets() {
  const [search, setSearch] = useSearchParams();
  const asked = search.get("view") as View | null;
  const view: View = asked && VIEWS.includes(asked) ? asked : "sets";
  const go = (v: View) => setSearch(v === "sets" ? {} : { view: v });
  const [version, setVersion] = useState(0);
  const [sources, setSources] = useState<Inventory[]>([]);
  useEffect(() => { void collectionApi.overview().then(r => setSources(r.sources.filter(x => !x.virtual)), () => undefined); }, [version]);
  const [dialog, setDialog] = useState<Dialog>(null);
  const [notice, setNotice] = useState<Notice>(null);
  useEffect(() => {
    if (!notice) return;
    const t = setTimeout(() => setNotice(null), 15000);
    return () => clearTimeout(t);
  }, [notice]);

  const changed: OnChanged = useCallback((text, undo) => { setNotice({ text, source: undo?.source }); setVersion(v => v + 1); }, []);
  async function undo() {
    if (!notice?.source) return;
    try { const r = await collectionApi.undo(notice.source); setNotice({ text: `Undid: ${r.summary}` }); setVersion(v => v + 1); }
    catch (e) { setNotice({ text: (e as Error).message }); }
  }

  return <div className="page parts-page">
    <header className="page-head"><div><h1>My Sets</h1>
      <p className="muted">The sets you own, LEGO or another brand, with their pictures and how many pieces are on hand.</p></div>
      <div className="head-actions"><button type="button" className="primary" onClick={() => setDialog("set")}>+ Add a set</button></div></header>
    <div className="segmented parts-tabs" role="group" aria-label="My Sets view">
      <button type="button" aria-pressed={view === "sets"} onClick={() => go("sets")}>Sets</button>
      <button type="button" aria-pressed={view === "reserved"} onClick={() => go("reserved")}>Built &amp; reserved</button>
      <button type="button" aria-pressed={view === "build"} onClick={() => go("build")}>What can I build?</button>
    </div>
    {view === "sets" && <SetsView version={version} onChanged={changed} onAdd={() => setDialog("set")} onAddManual={() => setDialog("quick")} />}
    {view === "reserved" && <ReservedView version={version} onChanged={changed} />}
    {view === "build" && (sources.length ? <BuildablePanel inventories={sources} /> : <p className="muted">Add some parts first, then see which set models you can build.</p>)}
    {dialog === "set" && <AddSetDialog onClose={() => setDialog(null)} onChanged={changed} />}
    {dialog === "quick" && <QuickAddDialog onClose={() => setDialog(null)} onChanged={changed} />}
    {notice && <div className="parts-notice" role="status">
      <span>{notice.text}</span>
      {notice.source && <button type="button" onClick={() => void undo()}>Undo</button>}
      <button type="button" className="icon-button" aria-label="Dismiss" onClick={() => setNotice(null)}>✕</button>
    </div>}
  </div>;
}
