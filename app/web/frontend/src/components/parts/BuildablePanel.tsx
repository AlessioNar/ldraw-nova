import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { inventoryApi, referenceModelUrl, type BuildableModel, type BuildableReport, type BuildableStatus, type Inventory } from "../../api";
import { useApp } from "../../context";
import { colourHex } from "../../ldrawColours";
import { saveSteering } from "./PartsControl";

const POLL_MS = 1500;
const SHARES = [["", "Any amount"], ["0.05", "5% or less"], ["0.1", "10% or less"], ["0.25", "25% or less"], ["0.5", "50% or less"]] as const;
type Phase = { kind: "loading" } | { kind: "building"; status: BuildableStatus } | { kind: "ready"; report: BuildableReport } | { kind: "error"; message: string };

const pct = (x: number) => `${Math.floor(x * 100)}%`;

function Card({ model, substitute, onOpen, onDesign }: { model: BuildableModel; substitute: boolean; onOpen: () => void; onDesign: () => void }) {
  const title = model.description || model.id;
  return <article className="panel build-card">
    <header>
      <h2 title={title}>{title}</h2>
      <p className="muted small ellipsis">{model.set_id ? `Set ${model.set_id}` : model.file}{model.set_id && model.id !== model.set_id ? ` · ${model.id}` : ""}</p>
    </header>
    <div className="build-score">
      <div className="build-bar" role="img" aria-label={`${pct(model.completeness)} of the pieces are in your lists`}>
        <span style={{ width: pct(model.completeness) }} />
      </div>
      <strong>{pct(model.completeness)}</strong>
    </div>
    <p className="small">{model.matched.toLocaleString()} of {model.pieces.toLocaleString()} pieces
      {model.missing > 0 ? <> · <span className="warn-text">{model.missing.toLocaleString()} missing</span> in {model.missing_lines} line{model.missing_lines === 1 ? "" : "s"}</> : <> · <span className="ok-text">nothing missing</span></>}</p>
    {substitute && model.substituted_completeness != null && model.substituted_completeness > model.completeness && <p className="small muted">{pct(model.substituted_completeness)} if other colours of the same part may stand in</p>}
    {model.custom_pieces > 0 && <p className="small muted">{model.custom_pieces.toLocaleString()} pieces are custom parts no list can cover</p>}
    {model.top_missing.length > 0 && <ul className="build-missing" aria-label="Most missing">
      {model.top_missing.map(l => <li key={`${l.part}-${l.colour}`} title={`${l.part} · need ${l.needed}, have ${l.owned}`}>
        <span className="swatch" style={{ background: colourHex(l.colour) }} aria-hidden />
        <span className="ellipsis">{l.missing}× {l.description || l.part} <span className="muted">{l.colour_name}</span></span>
      </li>)}
    </ul>}
    <div className="card-actions">
      <button type="button" onClick={onOpen}>Open model</button>
      <button type="button" onClick={onDesign} title="Start a chat that takes this model as the idea and prefers your parts">Design something like this</button>
    </div>
  </article>;
}

/** Which bundled set models the chosen inventories can build, or nearly build, ranked by completeness. */
export default function BuildablePanel({ inventories }: { inventories: Inventory[] }) {
  const { openViewer } = useApp();
  const navigate = useNavigate();
  const [picked, setPicked] = useState<string[]>(() => inventories.map(i => i.id));
  const [minParts, setMinParts] = useState("20");
  const [share, setShare] = useState("");
  const [query, setQuery] = useState("");
  const [applied, setApplied] = useState("");
  const [substitute, setSubstitute] = useState(false);
  const [phase, setPhase] = useState<Phase>({ kind: "loading" });
  const [attempt, setAttempt] = useState(0);

  const ids = picked.filter(id => inventories.some(i => i.id === id));
  const key = ids.join(",");
  useEffect(() => { const t = setTimeout(() => setApplied(query.trim()), 300); return () => clearTimeout(t); }, [query]);

  useEffect(() => {
    if (!ids.length) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const load = async () => {
      try {
        const min = Math.max(0, Math.floor(Number(minParts) || 0));
        const r = await inventoryApi.buildable({ ids, limit: 30, min_parts: min, max_missing_share: share ? Number(share) : null, query: applied, substitute });
        if (!alive) return;
        if ("results" in r) { setPhase({ kind: "ready", report: r }); return; }
        setPhase({ kind: "building", status: r });
        timer = setTimeout(poll, POLL_MS);
      } catch (e) {
        if (alive) setPhase({ kind: "error", message: (e as Error).message });
      }
    };
    const poll = async () => {
      try {
        const s = await inventoryApi.buildableStatus();
        if (!alive) return;
        if (s.error) setPhase({ kind: "error", message: s.error });
        else if (s.state === "building") { setPhase({ kind: "building", status: s }); timer = setTimeout(poll, POLL_MS); }
        else void load();
      } catch (e) {
        if (alive) setPhase({ kind: "error", message: (e as Error).message });
      }
    };
    setPhase(p => p.kind === "ready" ? p : { kind: "loading" });
    void load();
    return () => { alive = false; clearTimeout(timer); };
    // ids is derived from key; the rest are the filters
  }, [key, minParts, share, applied, substitute, attempt]);

  const retry = useCallback(() => { setPhase({ kind: "loading" }); inventoryApi.buildableWarm().then(() => setAttempt(a => a + 1), e => setPhase({ kind: "error", message: (e as Error).message })); }, []);
  const toggle = (id: string) => setPicked(ids.includes(id) ? ids.filter(i => i !== id) : [...ids, id]);
  const design = (m: BuildableModel) => {
    saveSteering({ mode: "prefer", ids });
    navigate("/new", { state: { prompt: `Design something like this: ${m.description || m.id}` } });
  };

  const report = phase.kind === "ready" ? phase.report : null;
  return <section className="buildable" aria-label="What can I build?">
    <p className="muted">Bundled LDraw set models ranked by how many of their pieces your lists cover, matching part and colour. Open a close one for ideas, or ask the agent for something similar.</p>
    <div className="build-filters">
      <fieldset className="parts-lists"><legend className="picker-label">Parts lists</legend>
        {inventories.map(inv => <label className="check" key={inv.id}>
          <input type="checkbox" checked={ids.includes(inv.id)} disabled={ids.includes(inv.id) && ids.length === 1} onChange={() => toggle(inv.id)} />
          <span className="ellipsis">{inv.name}</span><span className="muted small">{inv.total_pieces.toLocaleString()} pcs</span>
        </label>)}
      </fieldset>
      <label className="field"><span>Smallest model (pieces)</span>
        <input type="number" min={0} inputMode="numeric" value={minParts} onChange={e => setMinParts(e.target.value)} /></label>
      <label className="field"><span>Missing at most</span>
        <select value={share} onChange={e => setShare(e.target.value)}>{SHARES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
      <label className="field build-query"><span>Search</span>
        <input type="search" placeholder="e.g. truck, castle, spaceship" value={query} onChange={e => setQuery(e.target.value)} /></label>
      <label className="check"><input type="checkbox" checked={substitute} onChange={e => setSubstitute(e.target.checked)} /><span>Also show the share if other colours may stand in</span></label>
    </div>

    {phase.kind === "error" && <div className="banner error" role="alert">{phase.message} <button type="button" onClick={retry}>Try again</button></div>}
    {phase.kind === "loading" && <p className="muted"><span className="spinner" aria-hidden /> Ranking models…</p>}
    {phase.kind === "building" && <div className="parts-progress" role="status">
      <p>Scanning the bundled models for the first time. This takes a few minutes and only happens once.</p>
      <progress max={phase.status.total || 1} value={phase.status.done} aria-label="Models scanned" />
      <span className="muted small">{phase.status.done.toLocaleString()} of {phase.status.total ? phase.status.total.toLocaleString() : "…"} models</span>
    </div>}

    {report && <>
      {report.unmapped_pieces > 0 && <div className="banner" role="status">
        {report.unmapped_pieces.toLocaleString()} pieces ({pct(report.unmapped_share)}) of the chosen lists have no LDraw part or colour and count as not owned, so the shares below can be too low.
      </div>}
      {report.warnings.filter(w => !w.startsWith("pieces that could not be mapped")).map((w, i) => <div className="banner" role="status" key={i}>{w}</div>)}
      <p className="muted small" aria-live="polite">
        {report.counts.ranked.toLocaleString()} of {report.counts.models.toLocaleString()} models match; showing the best {report.results.length}.
        {report.counts.subvariants > 0 && ` ${report.counts.subvariants} variants that fit inside their main set model are left out.`}
      </p>
      {!report.results.length && <p className="muted">No model matches these filters. Try a larger "Missing at most", fewer search words or a smaller minimum size.</p>}
      <div className="card-grid build-grid">{report.results.map(m => <Card key={m.file} model={m} substitute={substitute}
        onOpen={() => openViewer({ modelUrl: referenceModelUrl(m.file), title: m.description || m.id, parts: m.pieces })} onDesign={() => design(m)} />)}</div>
    </>}
  </section>;
}
