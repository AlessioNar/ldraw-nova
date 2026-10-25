import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { reservationsApi, timeAgo, type BuiltSet, type Reservation } from "../../api";
import type { OnChanged } from "./changes";
import ShortageDialog from "./ShortageDialog";

/** Reserved: the models and sets whose parts are held back, each with a release button. */
export default function ReservedView({ version, onChanged }: { version: number; onChanged: OnChanged }) {
  const [data, setData] = useState<{ reservations: Reservation[]; built_sets: BuiltSet[] } | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const [shortage, setShortage] = useState<string | null>(null);
  const load = useCallback(() => reservationsApi.list().then(r => { setData(r); setError(""); }, e => setError((e as Error).message)), []);
  useEffect(() => { void load(); }, [load, version]);

  async function run(key: string, text: string, action: () => Promise<unknown>) {
    setBusy(key); setError("");
    try { await action(); onChanged(text); await load(); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(""); }
  }
  if (error && !data) return <div className="banner error" role="alert">{error}</div>;
  if (!data) return <p className="muted"><span className="spinner" aria-hidden /> Loading…</p>;
  const empty = !data.reservations.length && !data.built_sets.length;
  return <div className="reserved-view">
    {error && <div className="banner error" role="alert">{error}</div>}
    <p className="muted">Parts held by models and sets you built do not count as free in the editor palette and in "What can I build?". Releasing them makes them free again.</p>
    {empty && <p className="muted">Nothing is reserved. Use "I built this" on a model in <Link to="/models">My Builds</Link>, or add a set as built.</p>}
    {data.reservations.length > 0 && <>
      <h2>Models</h2>
      <ul className="reserved-list">{data.reservations.map(r => <li key={r.id}>
        <span className="ellipsis"><strong>{r.model}</strong> <span className="muted small">{r.pieces.toLocaleString()} pieces · {timeAgo(r.created_at)}</span></span>
        {r.short_pieces > 0 && <button type="button" className="warn-text" onClick={() => setShortage(r.model)} title="Pieces reserved beyond what you own">short by {r.short_pieces}</button>}
        <button type="button" disabled={busy === r.id} onClick={() => void run(r.id, `Released ${r.model}`, () => reservationsApi.release(r.id))}>Release</button>
      </li>)}</ul>
    </>}
    {data.built_sets.length > 0 && <>
      <h2>Built sets</h2>
      <ul className="reserved-list">{data.built_sets.map(s => <li key={s.source}>
        <span className="ellipsis"><strong>{s.name}</strong> <span className="muted small">{s.pieces.toLocaleString()} pieces</span></span>
        <button type="button" disabled={busy === s.source} onClick={() => void run(s.source, `Took apart ${s.name}`, () => reservationsApi.setSetState(s.source, "parts"))}>Take apart</button>
      </li>)}</ul>
    </>}
    {shortage && <ShortageDialog model={shortage} title={`Parts for ${shortage}`} onClose={() => setShortage(null)} />}
  </div>;
}
