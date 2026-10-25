import { useCallback, useEffect, useMemo, useState, type ChangeEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { inventoryApi, reservationsApi } from "../api";
import PartThumb from "../components/parts/PartThumb";
import { colourHex } from "../ldrawColours";
import { setDetailApi, type OwnedSetDetail, type SetRow } from "../setDetailApi";

type Group = "colour" | "category";
const WANTED_FORMATS = [
  { id: "bricklink-xml", title: "BrickLink wanted list (XML)" },
  { id: "rebrickable-csv", title: "Rebrickable CSV" },
  { id: "csv", title: "Plain CSV" },
];

/** One owned set at /sets/:id: a large picture, what it is, and every part it needs with how many are on hand or missing. */
export default function SetDetail() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const [data, setData] = useState<{ set: OwnedSetDetail; rows: SetRow[] } | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [query, setQuery] = useState("");
  const [group, setGroup] = useState<Group>("colour");
  const [onlyMissing, setOnlyMissing] = useState(false);
  const [format, setFormat] = useState(WANTED_FORMATS[0].id);
  const [imageKey, setImageKey] = useState(0);
  const [imageFailed, setImageFailed] = useState(false);

  const load = useCallback(() => setDetailApi.get(id).then(r => { setData(r); setError(""); }, e => setError((e as Error).message)), [id]);
  useEffect(() => { setData(null); void load(); }, [load]);

  async function act(work: () => Promise<string | void>) {
    setBusy(true); setNotice(""); setError("");
    try { const text = await work(); if (text) setNotice(text); await load(); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  const toggleBuilt = (s: OwnedSetDetail) => act(async () => { await reservationsApi.setSetState(s.id, s.state === "built" ? "parts" : "built"); });
  const upload = (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (file) void act(async () => { await setDetailApi.uploadImage(id, file); setImageKey(k => k + 1); setImageFailed(false); });
  };
  const removeImage = () => act(async () => {
    const r = await setDetailApi.removeImage(id);
    setImageKey(k => k + 1); setImageFailed(false);
    return r.removed ? "Your picture was removed." : "There is no picture of your own to remove.";
  });
  async function remove(s: OwnedSetDetail) {
    if (!confirm(`Remove "${s.name}" and its parts from your collection?\n\nIts history goes with it.`)) return;
    setBusy(true);
    try { await inventoryApi.remove(s.id); navigate("/sets"); }
    catch (e) { setError((e as Error).message); setBusy(false); }
  }
  const wanted = (s: OwnedSetDetail, rows: SetRow[]) => act(async () => {
    const omitted = await setDetailApi.downloadWanted(`${s.name} missing`, format, rows);
    return omitted > 0 ? `${omitted} line(s) could not be mapped to this format and were left out.` : "";
  });

  const rows = data?.rows ?? [];
  const shown = useMemo(() => {
    const tokens = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
    return rows.filter(r => (!onlyMissing || r.missing > 0)
      && tokens.every(t => `${r.part} ${r.description} ${r.colour_name}`.toLowerCase().includes(t)));
  }, [rows, query, onlyMissing]);
  const groups = useMemo(() => {
    const by = new Map<string, SetRow[]>();
    for (const r of shown) {
      const key = group === "colour" ? r.colour_name || `Colour ${r.colour}` : r.category || "Other";
      by.set(key, [...(by.get(key) ?? []), r]);
    }
    return [...by.entries()].sort((a, b) => a[0].localeCompare(b[0]));
  }, [shown, group]);

  if (!data) return <div className="set-detail">
    <Link to="/sets" className="muted small">← My Sets</Link>
    {error ? <div className="banner error" role="alert">{error}</div> : <p className="muted"><span className="spinner" aria-hidden /> Loading…</p>}
  </div>;

  const s = data.set;
  const built = s.state === "built";
  const missingLines = rows.filter(r => r.missing > 0).length;
  return <div className="set-detail">
    <Link to="/sets" className="muted small">← My Sets</Link>
    {error && <div className="banner error" role="alert">{error}</div>}
    {notice && <div className="banner" role="status">{notice}</div>}
    <header className="set-head">
      <div className="set-image">
        {imageFailed ? <span className="muted small">No picture</span>
          : <img src={`${s.image}?v=${imageKey}`} alt={`${s.name} (${s.set_num})`} onError={() => setImageFailed(true)} />}
      </div>
      <div className="set-meta">
        <h1>{s.name}</h1>
        <p className="muted">{s.brand} · #{s.number} · {s.year}{s.theme && ` · ${s.theme}`}</p>
        <p>
          <span className={`badge${built ? " ok" : ""}`}>{built ? "Built" : "Parts only"}</span>{" "}
          {s.copies > 1 ? `${s.copies} copies` : "1 copy"} · {s.pieces.toLocaleString()} pieces · {s.distinct_parts.toLocaleString()} distinct parts
        </p>
        <p>
          <strong>{s.on_hand.toLocaleString()}</strong> on hand
          {s.missing > 0 ? <> · <strong className="warn-text">{s.missing.toLocaleString()} missing</strong> in {missingLines} lines</> : " · nothing missing"}
          {built && <span className="muted small"> · {s.reserved.toLocaleString()} held by this set</span>}
        </p>
        {s.dropped_pieces > 0 && <p className="muted small">
          {s.dropped_pieces.toLocaleString()} piece(s) of this set have no LDraw part and are not listed here, so the counts above leave them out.
        </p>}
        {s.warnings.filter(w => !/no LDraw equivalent/.test(w)).map(w => <p key={w} className="muted small">{w}</p>)}
        <div className="head-actions">
          <button type="button" disabled={busy} onClick={() => void toggleBuilt(s)}
            title={built ? "Take it apart: its parts are free again" : "Hold this set's parts back from the rest of your collection"}>
            {built ? "Mark as parts only" : "Mark as built"}</button>
          <label className="button" aria-disabled={busy}>Replace picture
            <input type="file" accept="image/png,image/jpeg,image/webp" hidden disabled={busy} onChange={upload} />
          </label>
          <button type="button" disabled={busy} onClick={() => void removeImage()}>Remove my picture</button>
          <Link className="button" to={`/parts/${encodeURIComponent(s.id)}`}>Open in Collection</Link>
          <button type="button" className="danger" disabled={busy} onClick={() => void remove(s)}>Remove set</button>
        </div>
      </div>
    </header>

    <section aria-label="Parts of this set">
      <div className="set-controls">
        <input type="search" placeholder="Search part number, name or colour" value={query} onChange={e => setQuery(e.target.value)} aria-label="Search parts" />
        <div className="segmented" role="group" aria-label="Group by">
          <button type="button" aria-pressed={group === "colour"} onClick={() => setGroup("colour")}>By colour</button>
          <button type="button" aria-pressed={group === "category"} onClick={() => setGroup("category")}>By category</button>
        </div>
        <label className="check"><input type="checkbox" checked={onlyMissing} onChange={e => setOnlyMissing(e.target.checked)} /> Missing pieces only</label>
        <span className="set-export">
          <select value={format} onChange={e => setFormat(e.target.value)} aria-label="Wanted-list format">
            {WANTED_FORMATS.map(f => <option key={f.id} value={f.id}>{f.title}</option>)}
          </select>
          <button type="button" disabled={busy || missingLines === 0} onClick={() => void wanted(s, rows)}
            title={missingLines ? "Download the missing pieces as a wanted list" : "Nothing is missing"}>Export missing</button>
        </span>
      </div>
      {rows.length === 0 && <p className="muted">This set has no parts with an LDraw equivalent.</p>}
      {rows.length > 0 && shown.length === 0 && <p className="muted">No part matches.</p>}
      {groups.map(([name, list]) => <div key={name} className="set-group">
        <h2>{group === "colour" && list[0] && <span className="swatch" style={{ background: colourHex(list[0].colour) }} aria-hidden />} {name}
          <span className="muted small"> · {list.reduce((n, r) => n + r.needed, 0).toLocaleString()} pieces</span></h2>
        <div className="table-scroll"><table className="set-parts">
          <thead><tr><th></th><th>Part</th>{group === "category" && <th>Colour</th>}<th>Needed</th><th>On hand</th><th>Held by this set</th><th>Missing</th></tr></thead>
          <tbody>{list.map(r => <tr key={`${r.part}-${r.colour}`} className={r.missing > 0 ? "short" : undefined}>
            <td><PartThumb part={r.part} size={44} /></td>
            <td><strong>{r.part}</strong> <span className="muted small">{r.description}</span></td>
            {group === "category" && <td><span className="swatch" style={{ background: colourHex(r.colour) }} aria-hidden /> {r.colour_name}</td>}
            <td>{r.needed}</td><td>{r.on_hand}</td><td>{built ? r.needed : 0}</td>
            <td>{r.missing > 0 ? <strong className="warn-text">{r.missing}</strong> : "–"}</td>
          </tr>)}</tbody>
        </table></div>
      </div>)}
    </section>
  </div>;
}
