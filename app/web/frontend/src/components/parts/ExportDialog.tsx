import { useEffect, useState } from "react";
import { collectionApi, exportUrl, type ExportFormat, type ExportSummary } from "../../api";
import Dialog from "./Dialog";

const FORMATS: { id: ExportFormat; title: string; hint: string }[] = [
  { id: "bricklink-xml", title: "BrickLink wanted list (XML)", hint: "Upload it as a wanted list on BrickLink" },
  { id: "rebrickable-csv", title: "Rebrickable CSV", hint: "Part, Color, Quantity: import it into Rebrickable" },
  { id: "csv", title: "Plain CSV", hint: "Names and quantities for a spreadsheet" },
];

/** Export the whole collection (scope "collection") or one source ("source:<id>"). The file says what it holds and what it
 *  could not map before it is downloaded; such lines are left out, never guessed. */
export default function ExportDialog({ scope, name, onClose }: { scope: string; name: string; onClose: () => void }) {
  const [format, setFormat] = useState<ExportFormat>("bricklink-xml");
  const [summary, setSummary] = useState<ExportSummary | null>(null);
  const [error, setError] = useState("");
  const [all, setAll] = useState(false);
  useEffect(() => {
    let alive = true;
    setSummary(null); setError(""); setAll(false);
    collectionApi.exportPreview(format, scope).then(s => alive && setSummary(s), e => alive && setError((e as Error).message));
    return () => { alive = false; };
  }, [format, scope]);
  const omitted = summary?.omitted ?? [];
  const shown = all ? omitted : omitted.slice(0, 8);
  return <Dialog title={`Export ${name}`} label="Export parts" onClose={onClose}>
    <div className="parts-form">
      <fieldset className="import-target"><legend className="label">Format</legend>
        {FORMATS.map(f => <label className="check" key={f.id}><input type="radio" name="export-format" checked={format === f.id} onChange={() => setFormat(f.id)} />
          <span><strong>{f.title}</strong> <span className="muted small">{f.hint}</span></span></label>)}
      </fieldset>
      {error && <div className="banner error" role="alert">{error}</div>}
      {!summary && !error && <p className="muted"><span className="spinner" aria-hidden /> Preparing…</p>}
      {summary && <>
        <p><strong>{summary.lines.toLocaleString()}</strong> line{summary.lines === 1 ? "" : "s"}, {summary.pieces.toLocaleString()} pieces in <code>{summary.filename}</code>.</p>
        {summary.notes.map((n, i) => <p key={i} className="muted small">{n}</p>)}
        {summary.omitted_lines > 0 && <section className="report-unmapped" aria-label="Lines left out">
          <h4>Left out <span className="badge warn">{summary.omitted_lines}</span></h4>
          <p className="muted small">{summary.omitted_pieces.toLocaleString()} pieces have no matching colour in this format, so they are not in the file.</p>
          <ul>{shown.map((o, i) => <li key={i}><code>{o.part}</code> {o.colour_name} · ×{o.qty} <span className="muted small">{o.reason}</span></li>)}</ul>
          {omitted.length > 8 && <button type="button" className="link-button" aria-expanded={all} onClick={() => setAll(!all)}>{all ? "Show fewer" : `Show ${omitted.length} listed`}</button>}
        </section>}
        {summary.lines === 0 && <p className="muted">Nothing to export yet.</p>}
      </>}
      <div className="form-actions">
        <button type="button" onClick={onClose}>Close</button>
        <button type="button" className="primary" disabled={!summary || summary.lines === 0}
          onClick={() => { window.location.assign(exportUrl(format, scope)); window.setTimeout(onClose, 300); }}>Download</button>
      </div>
    </div>
  </Dialog>;
}
