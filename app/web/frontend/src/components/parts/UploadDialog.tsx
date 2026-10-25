import { useEffect, useRef, useState, type DragEvent } from "react";
import { collectionApi, type ColourSystem, type ColumnMapping, type ImportDiff, type ImportPreview, type Inventory, type NeedsMapping } from "../../api";
import { colourHex } from "../../ldrawColours";
import Dialog from "./Dialog";
import ImportReportView from "./ImportReportView";
import PileometerSummary from "./PileometerSummary";
import type { OnChanged } from "./changes";

const MAX_BYTES = 25 * 1024 * 1024;
const FORMATS: Record<string, string> = {
  "rebrickable-csv": "Rebrickable CSV", "brickit-pileometer": "Brickit / Pileometer CSV", "bricklink-xml": "BrickLink XML (inventory or wanted list)",
  "brickstore-bsx": "BrickStore BSX", "bricklink-csv": "BrickLink CSV (Item No, Color, Qty)", "generic-csv": "CSV with the columns you chose",
};
const SYSTEMS: Record<ColourSystem, string> = {
  name: "Colour names (BrickLink or Rebrickable spelling)", bricklink: "BrickLink colour ids", rebrickable: "Rebrickable colour ids", ldraw: "LDraw colour codes",
};
type Needed = NeedsMapping["needs_mapping"];

const readText = (file: File) => new Promise<string>((resolve, reject) => {
  const reader = new FileReader();
  reader.onload = () => resolve(String(reader.result));
  reader.onerror = () => reject(new Error("Cannot read this file"));
  reader.readAsText(file);
});

function DiffLines({ title, lines, show }: { title: string; lines: ImportDiff["lines"]["added"]; show: (l: ImportDiff["lines"]["added"][number]) => string }) {
  const [all, setAll] = useState(false);
  if (!lines.length) return null;
  const shown = all ? lines : lines.slice(0, 6);
  return <div className="diff-block"><h5>{title} <span className="badge">{lines.length}</span></h5>
    <ul>{shown.map(l => <li key={`${l.part}-${l.colour}`}><span className="swatch" style={{ background: colourHex(l.colour) }} aria-hidden />
      <code>{l.part}</code> <span className="ellipsis">{l.description || l.colour_name}</span> <span className="muted small">{l.colour_name}</span>
      <span className="mono diff-qty">{show(l)}</span></li>)}</ul>
    {lines.length > 6 && <button type="button" className="link-button" aria-expanded={all} onClick={() => setAll(!all)}>{all ? "Show fewer" : `Show all ${lines.length}`}</button>}
  </div>;
}

/** What replacing a list by a new file would change, line by line. */
export function DiffView({ diff }: { diff: ImportDiff }) {
  const same = !diff.added && !diff.removed && !diff.changed;
  return <section className="import-diff" aria-label="Changes against the current list">
    <p><strong>{diff.target.name}</strong>: {diff.pieces_before.toLocaleString()} pieces now, {diff.pieces_after.toLocaleString()} after.{" "}
      {same ? <span className="ok-text">Nothing changes.</span> : <>
        <span className="ok-text">+{diff.added} new line{diff.added === 1 ? "" : "s"}</span>, <span className="danger-text">−{diff.removed} gone</span>, <span className="warn-text">~{diff.changed} changed</span>, {diff.unchanged} unchanged.</>}</p>
    {(diff.review.before_pieces > 0 || diff.review.after_pieces > 0) && <p className="muted small">Colour-family pieces waiting for a colour: {diff.review.before_pieces} before, {diff.review.after_pieces} after (your earlier per-piece choices are replaced by the file).</p>}
    <DiffLines title="New lines" lines={diff.lines.added} show={l => `+${l.after}`} />
    <DiffLines title="Lines that are gone" lines={diff.lines.removed} show={l => `−${l.before}`} />
    <DiffLines title="Changed quantities" lines={diff.lines.changed} show={l => `${l.before} → ${l.after}`} />
  </section>;
}

/** The columns of a CSV nobody recognises: pick the part, colour and quantity columns and how colours are written. */
function MappingForm({ needed, busy, onRead, onCancel }: { needed: Needed; busy: boolean; onRead: (m: ColumnMapping) => void; onCancel: () => void }) {
  const start = needed.saved ?? needed.guess;
  const [part, setPart] = useState(start.part ?? "");
  const [colour, setColour] = useState(start.colour ?? "");
  const [qty, setQty] = useState(start.qty ?? "");
  const [system, setSystem] = useState<ColourSystem>(start.system || "name");
  const pick = (label: string, value: string, set: (v: string) => void) => <label className="field"><span>{label}</span>
    <select value={value} disabled={busy} onChange={e => set(e.target.value)}>
      <option value="">Choose a column…</option>
      {needed.headers.map((h, i) => <option key={i} value={h}>{h}</option>)}</select></label>;
  const ready = part && colour && qty && new Set([part, colour, qty]).size === 3;
  return <form className="parts-form" onSubmit={e => { e.preventDefault(); if (ready) onRead({ part, colour, qty, system }); }}>
    <p>No importer recognises this file. Tell me which column is which; the choice is remembered for files with these headers.</p>
    {needed.sample.length > 0 && <div className="mapping-sample" role="region" aria-label="First rows of the file" tabIndex={0}>
      <table><thead><tr>{needed.headers.map((h, i) => <th key={i}>{h}</th>)}</tr></thead>
        <tbody>{needed.sample.map((row, r) => <tr key={r}>{needed.headers.map((_, c) => <td key={c}>{row[c] ?? ""}</td>)}</tr>)}</tbody></table></div>}
    {pick("Part number column", part, setPart)}
    {pick("Colour column", colour, setColour)}
    {pick("Quantity column", qty, setQty)}
    <label className="field"><span>Colours are written as</span>
      <select value={system} disabled={busy} onChange={e => setSystem(e.target.value as ColourSystem)}>
        {(Object.keys(SYSTEMS) as ColourSystem[]).map(k => <option key={k} value={k}>{SYSTEMS[k]}</option>)}</select></label>
    <div className="form-actions">
      <button type="button" disabled={busy} onClick={onCancel}>Back</button>
      <button type="submit" className="primary" disabled={busy || !ready}>Read with these columns</button>
    </div>
  </form>;
}

/** Pick or drop a parts file. It is read and shown first (format, counts, what is not understood, and the diff when it
 *  replaces a list); nothing is stored until Import. */
export default function UploadDialog({ onClose, onChanged, sources, replaceId = "" }:
  { onClose: () => void; onChanged: OnChanged; sources: Inventory[]; replaceId?: string }) {
  const replaceable = sources.filter(s => (s.kind ?? "import") === "import" && !s.virtual);
  const [file, setFile] = useState<File | null>(null);
  const [csv, setCsv] = useState("");
  const [name, setName] = useState("");
  const [spares, setSpares] = useState(false);
  const [phase, setPhase] = useState<"form" | "reading" | "mapping" | "preview" | "committing" | "done">("form");
  const [needed, setNeeded] = useState<Needed | null>(null);
  const [error, setError] = useState("");
  const [dragging, setDragging] = useState(false);
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const [target, setTarget] = useState<string>(replaceId);          // "" = a new source, else the id to replace
  const [diff, setDiff] = useState<ImportDiff | null>(null);
  const [diffBusy, setDiffBusy] = useState(false);
  const [result, setResult] = useState<{ inventory: Inventory; summary: string } | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const busy = phase === "reading" || phase === "committing";

  function choose(chosen: File | undefined) {
    if (!chosen) return;
    if (chosen.size > MAX_BYTES) { setFile(null); setError("This file is larger than 25 MB. Export a smaller CSV."); return; }
    setError("");
    setFile(chosen);
    setName(n => n.trim() ? n : (replaceable.find(s => s.id === replaceId)?.name ?? chosen.name.replace(/\.[^.]+$/, "")));
  }
  function drop(e: DragEvent) {
    e.preventDefault();
    setDragging(false);
    if (!busy) choose(e.dataTransfer.files[0]);
  }
  async function read(families?: Record<string, number>, how?: { format?: string; mapping?: ColumnMapping }) {
    if (!file || !name.trim()) return;
    setError("");
    try {
      setPhase("reading");
      const text = csv || await readText(file);
      setCsv(text);
      const p = await collectionApi.previewImport(name.trim(), text, spares, file.name, families, how);
      if ("needs_mapping" in p) { setNeeded(p.needs_mapping); setPreview(null); setPhase("mapping"); return; }
      setPreview(p);
      if (!replaceId) setTarget(t => t || (p.same_name && replaceable.some(s => s.id === p.same_name) ? p.same_name : ""));
      setPhase("preview");
    } catch (e) {
      setError((e as Error).message);
      setPhase(preview ? "preview" : needed && how?.mapping ? "mapping" : "form");
    }
  }
  useEffect(() => {
    if (phase !== "preview" || !preview || !target) { setDiff(null); return; }
    let alive = true;
    setDiffBusy(true);
    collectionApi.importDiff(preview.preview_id, target).then(d => alive && (setDiff(d), setError("")), e => alive && setError((e as Error).message))
      .finally(() => alive && setDiffBusy(false));
    return () => { alive = false; };
  }, [phase, preview, target]);

  async function commit() {
    if (!preview) return;
    setPhase("committing"); setError("");
    try {
      const r = await collectionApi.commitImport(preview.preview_id, target ? { replace: target } : { name: name.trim() });
      const text = target
        ? `Replaced “${r.inventory.name}” with ${file?.name ?? "the new file"}`
        : `Imported “${r.inventory.name}”: ${r.inventory.total_pieces.toLocaleString()} pieces`;
      setResult({ inventory: r.inventory, summary: text });
      setPhase("done");
      onChanged(text, target ? { source: target } : undefined);
    } catch (e) { setError((e as Error).message); setPhase("preview"); }
  }

  const title = phase === "mapping" ? "Choose the columns" : phase === "preview" || phase === "committing" ? "Review the import" : phase === "done" ? "Imported" : replaceId ? "Replace with a new file" : "Import a file";
  const label = preview ? (FORMATS[preview.format] ?? preview.format) : "";
  const p = preview?.report.pileometer;
  return <Dialog title={title} label="Import a parts file" busy={busy} wide={phase === "preview" || phase === "committing"} onClose={onClose}>
    {phase === "done" && result && <>
      <p><strong>{result.summary}</strong>. {result.inventory.review_items ? `${(result.inventory.review_pieces ?? 0).toLocaleString()} colour-family pieces wait in Review and are not counted until you give them a colour.` : ""}</p>
      <div className="form-actions"><button type="button" className="primary" autoFocus onClick={onClose}>Done</button></div>
    </>}
    {(phase === "form" || phase === "reading") && <form className="parts-form" onSubmit={e => { e.preventDefault(); void read(); }}>
      <button type="button" className={`dropzone ${dragging ? "dragging" : ""}`} disabled={busy} autoFocus
        onClick={() => input.current?.click()}
        onDragOver={e => { e.preventDefault(); setDragging(true); }} onDragLeave={() => setDragging(false)} onDrop={drop}>
        {file ? <><strong>{file.name}</strong><span className="muted small">{(file.size / 1024).toLocaleString(undefined, { maximumFractionDigits: 0 })} KB · click to choose another</span></>
          : <><strong>Drop a file here or click to choose one</strong><span className="muted small">Rebrickable, BrickLink, BrickStore, Brickit Pileometer or any CSV. Up to 25 MB.</span></>}
      </button>
      <input ref={input} hidden type="file" aria-label="Parts file" accept=".csv,.xml,.bsx,text/csv,text/plain,text/xml,application/xml" onChange={e => { choose(e.target.files?.[0]); setCsv(""); e.target.value = ""; }} />
      <label className="field"><span>Name</span>
        <input value={name} maxLength={80} disabled={busy} placeholder="Technic bin" onChange={e => setName(e.target.value)} /></label>
      <label className="check"><input type="checkbox" checked={spares} disabled={busy} onChange={e => setSpares(e.target.checked)} /> Count spare parts</label>
      {error && <div className="banner error" role="alert">{error}</div>}
      {busy && <div className="parts-progress" role="status"><progress aria-label="Reading the file" /><span className="muted small">Reading the file and matching parts…</span></div>}
      <div className="form-actions">
        <button type="button" disabled={busy} onClick={onClose}>Cancel</button>
        <button type="submit" className="primary" disabled={busy || !file || !name.trim()}>Read the file</button>
      </div>
    </form>}
    {phase === "mapping" && needed && <MappingForm needed={needed} busy={false} onRead={m => void read(undefined, { mapping: m })}
      onCancel={() => { setPhase("form"); setNeeded(null); }} />}
    {error && phase === "mapping" && <div className="banner error" role="alert">{error}</div>}
    {(phase === "preview" || phase === "committing") && preview && <>
      <p className="import-detected">Detected <strong>{label}</strong>{preview.mapping_remembered && <> (columns remembered from last time)</>} · {file?.name} · {preview.totals.lines.toLocaleString()} lines, <strong>{preview.totals.pieces.toLocaleString()}</strong> exact pieces in {preview.totals.parts.toLocaleString()} parts and {preview.totals.colours} colours
        {preview.totals.review_pieces > 0 && <>, <span className="warn-text">{preview.totals.review_pieces.toLocaleString()} colour-family pieces to review</span></>}</p>
      <label className="field import-format"><span>Read it as</span>
        <select value={FORMATS[preview.format] ? preview.format : ""} disabled={busy} aria-label="Read the file as"
          onChange={e => void read(undefined, { format: e.target.value })}>
          {!FORMATS[preview.format] && <option value="">{preview.format || "Detected format"}</option>}
          {Object.entries(FORMATS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
      <fieldset className="import-target"><legend className="label">Where it goes</legend>
        <label className="check"><input type="radio" name="target" checked={!target} disabled={busy} onChange={() => setTarget("")} /> A new source called
          <input className="inline-input" aria-label="Source name" value={name} maxLength={80} disabled={busy || !!target} onChange={e => setName(e.target.value)} /></label>
        <label className="check"><input type="radio" name="target" checked={!!target} disabled={busy || !replaceable.length} onChange={() => setTarget(replaceable.find(s => s.id === preview.same_name)?.id ?? replaceable[0]?.id ?? "")} /> Replace an existing list (it keeps its id, so chats that use it keep working)
          <select className="inline-input" aria-label="List to replace" value={target} disabled={busy || !target} onChange={e => setTarget(e.target.value)}>
            {replaceable.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}</select></label>
      </fieldset>
      {target && (diff ? <DiffView diff={diff} /> : diffBusy ? <p className="muted"><span className="spinner" aria-hidden /> Comparing…</p> : null)}
      {p ? <PileometerSummary report={preview.report} onApply={async choice => { await read(choice); }} /> : null}
      <ImportReportView report={preview.report} hideFamilies />
      {error && <div className="banner error" role="alert">{error}</div>}
      <div className="form-actions">
        <button type="button" disabled={busy} onClick={() => { setPhase("form"); setPreview(null); setDiff(null); }}>Back</button>
        <button type="button" disabled={busy} onClick={onClose}>Cancel</button>
        <button type="button" className="primary" autoFocus disabled={busy || (!target && !name.trim()) || (!!target && !diff)} onClick={() => void commit()}>
          {phase === "committing" ? "Importing…" : target ? "Replace the list" : "Import"}</button>
      </div>
    </>}
  </Dialog>;
}
