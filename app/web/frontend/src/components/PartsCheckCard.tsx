import type { PartsCheck } from "../api";
import { colourHex } from "../ldrawColours";

/** After a model is published: does it fit the user's inventories, and what is short. */
export default function PartsCheckCard({ check }: { check: PartsCheck }) {
  const short = check.short ?? [];
  const missing = short.reduce((n, s) => n + s.missing, 0);
  return <section className={`parts-check ${check.complete ? "complete" : "short"}`} aria-label="Parts check">
    <header>
      <strong>Parts check</strong>
      <span className={`badge ${check.complete ? "ok" : "warn"}`}>{check.complete ? "You have everything" : `${missing.toLocaleString()} short`}</span>
      <span className="muted small">{check.mode === "only" ? "Only my parts" : "Prefer my parts"}</span>
    </header>
    <p className="small">{check.summary || `You own ${check.owned_total.toLocaleString()} of the ${check.needed_total.toLocaleString()} parts this model needs.`}</p>
    {!!check.sources?.length && <p className="small parts-sources">From: {check.sources.map(s => `${s.name} (${s.pieces.toLocaleString()})`).join(" · ")}</p>}
    {short.length > 0 && <details open={short.length <= 8}>
      <summary>{short.length} part{short.length === 1 ? "" : "s"} to find</summary>
      <div className="table-wrap"><table className="parts-table">
        <thead><tr><th scope="col">Part</th><th scope="col">Colour</th><th scope="col" className="num">Need</th><th scope="col" className="num">Own</th><th scope="col" className="num">Missing</th></tr></thead>
        <tbody>{short.map(s => <tr key={`${s.part}-${s.colour}`}>
          <td><code>{s.part}</code></td>
          <td><span className="swatch" style={{ background: colourHex(s.colour) }} aria-hidden /> {s.colour_name}</td>
          <td className="num">{s.needed}</td><td className="num">{s.owned}</td><td className="num warn-text">{s.missing}</td>
        </tr>)}</tbody>
      </table></div>
    </details>}
  </section>;
}
