import { useEffect, useRef, useState, type DragEvent } from "react";
import { createPortal } from "react-dom";
import { inventoryApi, type Inventory, type ImportReport } from "../../api";
import ImportReportView from "./ImportReportView";

const MAX_BYTES = 25 * 1024 * 1024;

const readText = (file: File) => new Promise<string>((resolve, reject) => {
  const reader = new FileReader();
  reader.onload = () => resolve(String(reader.result));
  reader.onerror = () => reject(new Error("Cannot read this file"));
  reader.readAsText(file);
});

/** Pick or drop a Rebrickable CSV, import it, then show the import report. */
export default function UploadDialog({ onClose, onImported }: { onClose: () => void; onImported: (inventory: Inventory) => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState("");
  const [spares, setSpares] = useState(false);
  const [phase, setPhase] = useState<"form" | "reading" | "importing" | "done">("form");
  const [error, setError] = useState("");
  const [dragging, setDragging] = useState(false);
  const [result, setResult] = useState<{ inventory: Inventory; report: ImportReport } | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const busy = phase === "reading" || phase === "importing";

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && !busy && onClose();
    addEventListener("keydown", onKey);
    return () => removeEventListener("keydown", onKey);
  }, [onClose, busy]);

  function choose(chosen: File | undefined) {
    if (!chosen) return;
    if (chosen.size > MAX_BYTES) { setFile(null); setError("This file is larger than 25 MB. Export a smaller CSV."); return; }
    setError("");
    setFile(chosen);
    setName(n => n.trim() ? n : chosen.name.replace(/\.[^.]+$/, ""));
  }
  function drop(e: DragEvent) {
    e.preventDefault();
    setDragging(false);
    if (!busy) choose(e.dataTransfer.files[0]);
  }
  async function submit() {
    if (!file || !name.trim()) return;
    setError("");
    try {
      setPhase("reading");
      const csv = await readText(file);
      setPhase("importing");
      const r = await inventoryApi.create(name.trim(), csv, spares);
      setResult(r);
      setPhase("done");
      onImported(r.inventory);
    } catch (e) {
      setError((e as Error).message);
      setPhase("form");
    }
  }

  return createPortal(
    <div className="modal-backdrop" onClick={() => !busy && onClose()}>
      <div className="modal parts-dialog" role="dialog" aria-modal="true" aria-label="Upload a parts list" onClick={e => e.stopPropagation()}>
        <header className="modal-head">
          <strong className="ellipsis">{phase === "done" ? "Import report" : "Upload a parts list"}</strong>
          <button className="icon-button" aria-label="Close" disabled={busy} onClick={onClose}>✕</button>
        </header>
        <div className="parts-dialog-body">
          {phase === "done" && result ? <>
            <p><strong>{result.inventory.name}</strong> now has {result.inventory.total_pieces.toLocaleString()} pieces in {result.inventory.distinct_parts.toLocaleString()} distinct parts.</p>
            <ImportReportView report={result.report} />
            <div className="form-actions"><button type="button" className="primary" autoFocus onClick={onClose}>Done</button></div>
          </> : <form className="parts-form" onSubmit={e => { e.preventDefault(); void submit(); }}>
            <button type="button" className={`dropzone ${dragging ? "dragging" : ""}`} disabled={busy} autoFocus
              onClick={() => input.current?.click()}
              onDragOver={e => { e.preventDefault(); setDragging(true); }} onDragLeave={() => setDragging(false)} onDrop={drop}>
              {file ? <><strong>{file.name}</strong><span className="muted small">{(file.size / 1024).toLocaleString(undefined, { maximumFractionDigits: 0 })} KB · click to choose another</span></>
                : <><strong>Drop a CSV here or click to choose one</strong><span className="muted small">Rebrickable format: Part, Color, Quantity. Up to 25 MB.</span></>}
            </button>
            <input ref={input} hidden type="file" aria-label="Parts CSV file" accept=".csv,text/csv,text/plain" onChange={e => { choose(e.target.files?.[0]); e.target.value = ""; }} />
            <label className="field"><span>Name</span>
              <input value={name} maxLength={80} disabled={busy} placeholder="Technic bin" onChange={e => setName(e.target.value)} /></label>
            <label className="check"><input type="checkbox" checked={spares} disabled={busy} onChange={e => setSpares(e.target.checked)} /> Count spare parts</label>
            {error && <div className="banner error" role="alert">{error}</div>}
            {busy && <div className="parts-progress" role="status"><progress aria-label="Import progress" />
              <span className="muted small">{phase === "reading" ? "Reading the file…" : "Importing and matching parts…"}</span></div>}
            <div className="form-actions">
              <button type="button" disabled={busy} onClick={onClose}>Cancel</button>
              <button type="submit" className="primary" disabled={busy || !file || !name.trim()}>Import</button>
            </div>
          </form>}
        </div>
      </div>
    </div>,
    document.body,
  );
}
