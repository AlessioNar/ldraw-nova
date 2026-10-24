import { useCallback, useEffect, useState } from "react";
import { collectionApi, timeAgo, type HistoryBatch } from "../../api";
import type { OnChanged } from "./changes";

/** The change log of one source, newest first, with Undo for the last change that can still be undone. */
export default function SourceHistory({ source, version, onChanged }: { source: string; version: number; onChanged: OnChanged }) {
  const [batches, setBatches] = useState<HistoryBatch[] | null>(null);
  const [undoBatch, setUndoBatch] = useState<number | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const load = useCallback(() => collectionApi.history(source).then(r => { setBatches(r.batches); setUndoBatch(r.undo_batch); setError(""); },
    e => setError((e as Error).message)), [source]);
  useEffect(() => { void load(); }, [load, version]);

  async function undo() {
    setBusy(true); setError("");
    try { const r = await collectionApi.undo(source); onChanged(`Undid: ${r.summary}`); await load(); }
    catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  return <section className="source-history" aria-label="Change history">
    <header><h3>History</h3>
      <button type="button" disabled={busy || undoBatch == null} onClick={() => void undo()}>Undo the last change</button></header>
    {error && <div className="banner error" role="alert">{error}</div>}
    {!batches && !error && <p className="muted"><span className="spinner" aria-hidden /> Loading…</p>}
    {batches && !batches.length && <p className="muted small">No changes recorded yet.</p>}
    {batches && batches.length > 0 && <ul>{batches.map(b => <li key={b.batch} className={b.undone || b.kind === "undo" ? "undone" : ""}>
      <span className="muted small mono">{timeAgo(b.ts)}</span>
      <span>{b.summary}{b.note && b.kind !== "undo" ? <span className="muted small"> · {b.note}</span> : null}</span>
      {b.undone && <span className="badge">undone</span>}
      {b.batch === undoBatch && <span className="badge ok">next undo</span>}
    </li>)}</ul>}
  </section>;
}
