import { useState } from "react";
import type { ImportReport } from "../../api";

const KEEP = "";

/** Brickit Pileometer import: what was counted, and which exact LDraw colour each colour family gets (or none yet). */
export default function PileometerSummary({ report, onApply }: { report: ImportReport; onApply?: (choice: Record<string, number>) => Promise<void> }) {
  const p = report.pileometer!;
  const [choice, setChoice] = useState<Record<string, string>>(() => Object.fromEntries(p.families.map(f => [f.family, f.colour == null ? KEEP : String(f.colour)])));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const changed = p.families.some(f => choice[f.family] !== (f.colour == null ? KEEP : String(f.colour)));
  const review = p.families.filter(f => f.colour == null).length;
  const apply = async () => {
    if (!onApply) return;
    setBusy(true); setError("");
    try {
      await onApply(Object.fromEntries(Object.entries(choice).filter(([, v]) => v !== KEEP).map(([k, v]) => [k, Number(v)])));
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };
  return <>
    <p>{p.pieces.toLocaleString()} pieces from {p.scans} scans · {p.assigned_pieces.toLocaleString()} assigned, {review} colour {review === 1 ? "family" : "families"} to review · {report.unmapped_parts.length} parts not matched</p>
    <section className="report-unmapped" aria-label="Colour families">
      <h4>Colour families</h4>
      <p className="muted small">Pileometer only says &ldquo;Reds&rdquo; or &ldquo;Grays&rdquo;. Pieces without a colour are kept for review and not counted as owned. Pick a colour to assign a whole family.</p>
      <ul style={{ maxHeight: "none", overflow: "visible" }}>{p.families.map(f => <li key={f.family}>
        <label>{f.family} <span className="muted small">({f.pieces.toLocaleString()} {f.pieces === 1 ? "piece" : "pieces"})</span>{" "}
          <select aria-label={`Colour for ${f.family}`} value={choice[f.family]} disabled={busy || !onApply || !f.candidates.length}
            onChange={e => setChoice({ ...choice, [f.family]: e.target.value })}>
            <option value={KEEP}>Not assigned</option>
            {f.candidates.map(o => <option key={o.colour} value={String(o.colour)}>{o.name || `Colour ${o.colour}`}</option>)}
          </select></label>
      </li>)}</ul>
      {error && <div className="banner error" role="alert">{error}</div>}
      {onApply && <button type="button" disabled={busy || !changed} onClick={() => void apply()}>{busy ? "Applying…" : "Assign colours"}</button>}
    </section>
  </>;
}
