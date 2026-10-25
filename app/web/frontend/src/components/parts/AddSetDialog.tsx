import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { collectionApi, setsApi, type CatalogSet, type Inventory } from "../../api";
import Dialog from "./Dialog";
import type { OnChanged } from "./changes";

const BRANDS = ["CaDA", "Mould King", "Xingbao", "Sluban", "Cobi", "Lepin"];

function SetOptions({ copies, setCopies, state, setState }:
  { copies: string; setCopies: (v: string) => void; state: "parts" | "built"; setState: (v: "parts" | "built") => void }) {
  return <div className="set-options">
    <label className="field"><span>Copies</span><input type="number" min={1} max={99} value={copies} onChange={e => setCopies(e.target.value)} /></label>
    <fieldset className="set-state"><legend className="small">State</legend>
      <label className="check"><input type="radio" name="set-state" checked={state === "parts"} onChange={() => setState("parts")} /> Taken apart, the parts are loose</label>
      <label className="check"><input type="radio" name="set-state" checked={state === "built"} onChange={() => setState("built")} /> Built, still counted as owned</label>
    </fieldset>
  </div>;
}

/** Add a whole set by number or name: its parts list from the offline catalogue becomes a source of its own.
 * The "By hand" tab is the fallback for a set no catalogue knows: it starts empty and its parts are imported over it. */
export default function AddSetDialog({ onClose, onChanged, onImportParts }: { onClose: () => void; onChanged: OnChanged; onImportParts?: (id: string) => void }) {
  const [mode, setMode] = useState<"catalogue" | "manual">("catalogue");
  const [brand, setBrand] = useState("");
  const [name, setName] = useState("");
  const [code, setCode] = useState("");
  const [year, setYear] = useState("");
  const [photo, setPhoto] = useState<File | null>(null);
  const [photoError, setPhotoError] = useState("");
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

  async function addManual() {
    setBusy(true); setError(""); setPhotoError("");
    try {
      const r = await collectionApi.addManualSet({ brand: brand.trim(), name: name.trim(), code: code.trim(), year: year.trim() ? Number(year) : null,
        copies: Math.max(1, Math.floor(Number(copies) || 1)), state });
      if (photo) await setsApi.uploadImage(r.inventory.id, photo).catch(e => setPhotoError(`The set was added, but its photo was not saved: ${(e as Error).message}`));
      setDone(r.inventory);
      onChanged(`Added ${brand.trim()} set ${code.trim()}`);
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }

  if (done) return <Dialog title="Add a set" onClose={onClose}>
    {done.set?.manual
      ? <p><strong>{done.name}</strong> is added without parts. Import its parts list (Rebrickable, BrickLink, BrickStore or any CSV) to count its pieces in your collection.</p>
      : <p><strong>{done.name}</strong> is now a source with {done.total_pieces.toLocaleString()} pieces in {done.distinct_parts.toLocaleString()} distinct parts.</p>}
    {photoError && <div className="banner warn" role="status">{photoError}</div>}
    {!!done.set?.dropped_pieces && <div className="banner" role="status">{done.set.dropped_pieces.toLocaleString()} pieces of this set have no LDraw part and are not listed.</div>}
    <div className="form-actions">
      {done.set?.manual && onImportParts
        ? <button type="button" className="primary" autoFocus onClick={() => { onClose(); onImportParts(done.id); }}>Import its parts</button>
        : <Link className="button" to={`/parts/${done.id}`} onClick={onClose}>View its parts</Link>}
      <button type="button" onClick={() => { setDone(null); setPicked(null); setPhotoError(""); }}>Add another set</button>
      <button type="button" className={done.set?.manual && onImportParts ? "" : "primary"} onClick={onClose}>Done</button>
    </div>
  </Dialog>;

  const tabs = <div className="segmented" role="group" aria-label="How to add the set">
    <button type="button" aria-pressed={mode === "catalogue"} onClick={() => { setMode("catalogue"); setError(""); }}>Find it</button>
    <button type="button" aria-pressed={mode === "manual"} onClick={() => { setMode("manual"); setError(""); }}>By hand</button>
  </div>;

  if (mode === "manual") return <Dialog title="Add a set" wide busy={busy} onClose={onClose}>
    {tabs}
    <p className="muted small">For a set the search does not find. It is added with no parts; you import its parts list next.</p>
    <div className="set-options">
      <label className="field"><span>Brand</span><input autoFocus list="set-brands" value={brand} maxLength={40} placeholder="CaDA" onChange={e => setBrand(e.target.value)} />
        <datalist id="set-brands">{BRANDS.map(b => <option key={b} value={b} />)}</datalist></label>
      <label className="field"><span>Set number or product code</span><input value={code} maxLength={40} placeholder="C51026W" onChange={e => setCode(e.target.value)} /></label>
    </div>
    <div className="set-options">
      <label className="field"><span>Name</span><input value={name} maxLength={80} placeholder="Red Baron Plane" onChange={e => setName(e.target.value)} /></label>
      <label className="field"><span>Year (optional)</span><input type="number" min={1900} max={2100} value={year} onChange={e => setYear(e.target.value)} /></label>
    </div>
    <SetOptions copies={copies} setCopies={setCopies} state={state} setState={setState} />
    <label className="field"><span>Photo (optional, PNG, JPEG or WebP, up to 2 MB)</span>
      <input type="file" accept="image/png,image/jpeg,image/webp" onChange={e => setPhoto(e.target.files?.[0] ?? null)} /></label>
    {error && <div className="banner error" role="alert">{error}</div>}
    <div className="form-actions">
      <button type="button" disabled={busy} onClick={onClose}>Cancel</button>
      <button type="button" className="primary" disabled={busy || !brand.trim() || !name.trim() || !code.trim()} onClick={() => void addManual()}>{busy ? "Adding…" : "Add set"}</button>
    </div>
  </Dialog>;

  return <Dialog title="Add a set" wide busy={busy} onClose={onClose}>
    {tabs}
    <label className="field"><span>Set number or name</span>
      <input autoFocus type="search" value={query} placeholder="8448, or tipper truck" aria-label="Search sets" onChange={e => { setQuery(e.target.value); setPicked(null); }} /></label>
    {error && <div className="banner error" role="alert">{error}</div>}
    {!results && !error && <p className="muted"><span className="spinner" aria-hidden /> Loading the catalogue…</p>}
    {results && !results.length && <p className="muted">No set matches “{query}”. <button type="button" className="link-button" onClick={() => setMode("manual")}>Add it by hand</button></p>}
    {results && results.length > 0 && <ul className="set-results" aria-label="Sets">
      {results.map(s => <li key={s.set_num}>
        <button type="button" aria-pressed={picked?.set_num === s.set_num} className="set-result" onClick={() => setPicked(s)}>
          <code>{s.number}</code><span className="set-name ellipsis">{s.name}</span>
          <span className="muted small">{[s.brand !== "LEGO" && s.brand, s.year || "", s.theme.split(" > ")[0]].filter(Boolean).join(" · ")}</span><span className="mono small">{s.num_parts.toLocaleString()} pcs</span>
        </button></li>)}
    </ul>}
    {picked && <div className="set-picked panel">
      <strong>{picked.number} {picked.name}</strong>
      <p className="muted small">{[picked.brand, picked.year || "", picked.theme].filter(Boolean).join(" · ")} · {picked.num_parts.toLocaleString()} pieces
        {picked.dropped_pieces > 0 && `, ${picked.dropped_pieces.toLocaleString()} without an LDraw part (not listed)`}</p>
      <SetOptions copies={copies} setCopies={setCopies} state={state} setState={setState} />
    </div>}
    <div className="form-actions">
      <button type="button" disabled={busy} onClick={onClose}>Cancel</button>
      <button type="button" className="primary" disabled={busy || !picked} onClick={() => void add()}>{busy ? "Adding…" : picked ? `Add ${picked.set_num}` : "Add"}</button>
    </div>
  </Dialog>;
}
