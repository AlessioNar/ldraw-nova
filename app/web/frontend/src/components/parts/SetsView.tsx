import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { setsApi, type OwnedSet, type SetImageSettings } from "../../api";
import type { OnChanged } from "./changes";

type Sort = "name" | "year" | "complete";
const SORTS: { id: Sort; label: string }[] = [{ id: "name", label: "Name" }, { id: "year", label: "Year (newest)" }, { id: "complete", label: "Completeness (least first)" }];
const share = (s: OwnedSet) => (s.pieces ? s.on_hand / s.pieces : 1);

/** My sets: a card per owned set (LEGO or another brand) with its picture, state and how many pieces are on hand or missing. */
export default function SetsView({ version, onChanged, onAdd, onAddManual }:
  { version: number; onChanged: OnChanged; onAdd: () => void; onAddManual: () => void }) {
  const [sets, setSets] = useState<OwnedSet[] | null>(null);
  const [images, setImages] = useState<SetImageSettings | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const [stamp, setStamp] = useState(0);              // bumped after a picture changes so the browser refetches it
  const [query, setQuery] = useState("");
  const [brand, setBrand] = useState("");
  const [state, setState] = useState("");
  const [sort, setSort] = useState<Sort>("name");
  const pick = useRef<HTMLInputElement>(null);
  const target = useRef<OwnedSet | null>(null);

  useEffect(() => { setsApi.list().then(r => { setSets(r.sets); setError(""); }, e => setError((e as Error).message)); }, [version]);
  useEffect(() => { setsApi.imageSettings().then(setImages, () => {}); }, []);

  const brands = useMemo(() => [...new Set((sets ?? []).map(s => s.brand))].sort(), [sets]);
  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    const list = (sets ?? []).filter(s => (!brand || s.brand === brand) && (!state || s.state === state)
      && (!q || s.name.toLowerCase().includes(q) || s.set_num.toLowerCase().includes(q) || s.source_name.toLowerCase().includes(q)));
    return list.sort((a, b) => sort === "year" ? b.year - a.year || a.name.localeCompare(b.name)
      : sort === "complete" ? share(a) - share(b) || a.name.localeCompare(b.name) : a.name.localeCompare(b.name));
  }, [sets, query, brand, state, sort]);

  async function toggleFetch(on: boolean) {
    setBusy("fetch");
    try { setImages(await setsApi.setRemoteFetch(on)); setStamp(Date.now()); setError(""); } catch (e) { setError((e as Error).message); } finally { setBusy(""); }
  }
  async function upload(file: File | undefined) {
    const s = target.current;
    if (!file || !s) return;
    setBusy(s.id);
    try { await setsApi.uploadImage(s.id, file); setStamp(Date.now()); setError(""); onChanged(`Picture of ${s.name} saved`); }
    catch (e) { setError((e as Error).message); } finally { setBusy(""); if (pick.current) pick.current.value = ""; }
  }
  async function removePhoto(s: OwnedSet) {
    setBusy(s.id);
    try { await setsApi.removeImage(s.id); setStamp(Date.now()); setError(""); } catch (e) { setError((e as Error).message); } finally { setBusy(""); }
  }

  return <section aria-label="My sets">
    {images && <label className="check set-fetch">
      <input type="checkbox" checked={images.remote_fetch} disabled={busy === "fetch"} onChange={e => void toggleFetch(e.target.checked)} />
      <span>Fetch set pictures from {images.source}<small className="muted"> · once per owned set, kept on this machine, never shared. Untick to stay offline; your own photos always work.</small></span>
    </label>}
    {error && <div className="banner error" role="alert">{error}</div>}
    {!sets && !error && <p className="muted"><span className="spinner" aria-hidden /> Loading…</p>}
    {sets && !sets.length && <div className="panel parts-empty-state">
      <h2>No sets yet</h2>
      <p className="muted">Add a set from the catalogue, LEGO or another brand, and it shows up here with its picture and part counts.</p>
      <div className="head-actions"><button type="button" className="primary" onClick={onAdd}>Add a set</button>
        <button type="button" onClick={onAddManual}>Add parts of a third-party set by hand</button></div>
    </div>}
    {sets && sets.length > 0 && <>
      <div className="parts-filters" role="search">
        <input type="search" aria-label="Search sets" placeholder="Search by name or number" value={query} onChange={e => setQuery(e.target.value)} />
        <select aria-label="Brand" value={brand} onChange={e => setBrand(e.target.value)}>
          <option value="">All brands</option>{brands.map(b => <option key={b}>{b}</option>)}</select>
        <select aria-label="State" value={state} onChange={e => setState(e.target.value)}>
          <option value="">Built and taken apart</option><option value="built">Built</option><option value="parts">Parts</option></select>
        <select aria-label="Sort by" value={sort} onChange={e => setSort(e.target.value as Sort)}>
          {SORTS.map(o => <option key={o.id} value={o.id}>Sort: {o.label}</option>)}</select>
      </div>
      {!shown.length && <p className="muted">No set matches these filters.</p>}
      <div className="card-grid parts-grid sets-grid">{shown.map(s => <article className="panel set-card" key={s.id}>
        <Link to={`/sets/${encodeURIComponent(s.id)}`} className="set-card-link" aria-label={`${s.name}, set ${s.set_num}`}>
          <img className="set-card-image" src={`${s.image}${s.image.includes("?") ? "&" : "?"}v=${stamp}`} alt="" loading="lazy" />
          <h2 className="ellipsis" title={s.name}>{s.name}</h2>
        </Link>
        <p className="muted small ellipsis">{s.set_num} · {s.year || "year unknown"}{s.copies > 1 ? ` · × ${s.copies}` : ""}</p>
        <p className="set-badges">
          <span className="badge">{s.brand}</span>
          <span className={`badge${s.state === "built" ? " ok" : ""}`}>{s.state === "built" ? "Built" : "Parts"}</span>
          {s.missing > 0 && <span className="badge warn">{s.missing.toLocaleString()} missing</span>}
        </p>
        <p className="inventory-pieces"><strong>{s.on_hand.toLocaleString()}</strong> of {s.pieces.toLocaleString()} pieces on hand</p>
        {s.state === "built" && s.reserved > 0 && s.missing === 0 && <p className="muted small">All {s.reserved.toLocaleString()} reserved by this build</p>}
        <div className="card-actions">
          <button type="button" disabled={busy === s.id} onClick={() => { target.current = s; pick.current?.click(); }}>Upload photo</button>
          <button type="button" disabled={busy === s.id} aria-label={`Remove the photo of ${s.name}`} onClick={() => void removePhoto(s)}>Remove photo</button>
        </div>
      </article>)}</div>
    </>}
    <input ref={pick} type="file" accept="image/png,image/jpeg,image/webp" hidden onChange={e => void upload(e.target.files?.[0])} />
  </section>;
}
