import { useEffect, useState } from "react";
import { reservationsApi, type Shortage } from "../../api";
import Dialog from "./Dialog";

/** What a model needs that the collection leaves free, with the export buttons. `initial` skips the fetch when the caller already has the report. */
export default function ShortageDialog({ model, title, initial, onClose }: { model: string; title: string; initial?: Shortage; onClose: () => void }) {
  const [mode, setMode] = useState<"exact" | "substitute">("exact");
  const [report, setReport] = useState<Shortage | null>(initial ?? null);
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (mode === "exact" && initial) { setReport(initial); return; }
    let alive = true;
    reservationsApi.shortage(model, mode).then(r => alive && setReport(r), e => alive && setError((e as Error).message));
    return () => { alive = false; };
  }, [model, mode, initial]);

  async function copy() {
    try { await navigator.clipboard.writeText(await reservationsApi.exportText(model, mode)); setCopied(true); setTimeout(() => setCopied(false), 2500); }
    catch (e) { setError((e as Error).message); }
  }

  return <Dialog title={title} wide onClose={onClose}>
    {error && <div className="banner error" role="alert">{error}</div>}
    {!report && !error && <p className="muted"><span className="spinner" aria-hidden /> Counting…</p>}
    {report && <>
      <p>{report.missing === 0
        ? "Your free parts cover everything this model needs."
        : <>You are short of <strong>{report.missing.toLocaleString()}</strong> of {report.needed.toLocaleString()} pieces{report.missing_with_substitutes != null && <> ({report.missing_with_substitutes.toLocaleString()} if other colours of the same part may stand in)</>}.</>}</p>
      <div className="segmented" role="group" aria-label="Colours">
        <button type="button" aria-pressed={mode === "exact"} onClick={() => setMode("exact")}>Exact colours</button>
        <button type="button" aria-pressed={mode === "substitute"} onClick={() => setMode("substitute")}>Allow other colours</button>
      </div>
      {report.lines.length > 0 && <div className="table-scroll"><table className="shortage-table">
        <thead><tr><th>Part</th><th>Colour</th><th>Needed</th><th>Free</th><th>Missing</th><th>Other colours I own</th></tr></thead>
        <tbody>{report.lines.map(l => <tr key={`${l.part}-${l.colour}`}>
          <td><strong>{l.part}</strong> <span className="muted small">{l.description}</span></td>
          <td>{l.colour_name}</td><td>{l.needed}</td>
          <td title={l.reserved ? `${l.owned} owned, ${l.reserved} held by built models and sets` : undefined}>{l.available}</td>
          <td><strong>{l.missing}</strong>{l.maybe > 0 && <span className="muted small" title="Unsorted colour-family pieces that might be this colour"> +{l.maybe} maybe</span>}</td>
          <td className="small">{l.substitutes.length ? l.substitutes.map(s => `${s.available} ${s.colour_name}`).join(", ") : "–"}</td>
        </tr>)}</tbody></table></div>}
      <div className="head-actions">
        <button type="button" disabled={!report.lines.length} onClick={() => void copy()}>{copied ? "Copied" : "Copy as text"}</button>
        <a className={`button${report.lines.length ? "" : " disabled"}`} href={reservationsApi.exportUrl(model, "csv", mode)} aria-disabled={!report.lines.length}>Export CSV</a>
      </div>
    </>}
  </Dialog>;
}
