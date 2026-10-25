import { useCallback, useEffect, useState } from "react";
import { collectionApi } from "../api";
import AddSetDialog from "../components/parts/AddSetDialog";
import QuickAddDialog from "../components/parts/QuickAddDialog";
import SetsView from "../components/parts/SetsView";
import type { OnChanged } from "../components/parts/changes";

type Dialog = null | "set" | "quick";
type Notice = { text: string; source?: string } | null;

/** My Sets: every set I own as a card with its picture and part counts; one set's page is at /sets/:id. */
export default function Sets() {
  const [version, setVersion] = useState(0);
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
    <SetsView version={version} onChanged={changed} onAdd={() => setDialog("set")} onAddManual={() => setDialog("quick")} />
    {dialog === "set" && <AddSetDialog onClose={() => setDialog(null)} onChanged={changed} />}
    {dialog === "quick" && <QuickAddDialog onClose={() => setDialog(null)} onChanged={changed} />}
    {notice && <div className="parts-notice" role="status">
      <span>{notice.text}</span>
      {notice.source && <button type="button" onClick={() => void undo()}>Undo</button>}
      <button type="button" className="icon-button" aria-label="Dismiss" onClick={() => setNotice(null)}>✕</button>
    </div>}
  </div>;
}
