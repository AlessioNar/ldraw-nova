import { useState } from "react";
import type { ImportReport } from "../../api";

const TOP = 10;

function Unmapped<T>({ title, hint, items, label }: { title: string; hint: string; items: T[]; label: (item: T) => string }) {
  const [all, setAll] = useState(false);
  if (!items.length) return null;
  const shown = all ? items : items.slice(0, TOP);
  return <section className="report-unmapped" aria-label={title}>
    <h4>{title} <span className="badge warn">{items.length}</span></h4>
    <p className="muted small">{hint}</p>
    <ul>{shown.map((item, i) => <li key={i}>{label(item)}</li>)}</ul>
    {items.length > TOP && <button type="button" className="link-button" aria-expanded={all} onClick={() => setAll(!all)}>
      {all ? "Show fewer" : `Show all ${items.length}`}
    </button>}
  </section>;
}

/** What an import understood, and what the agent will not see. */
export default function ImportReportView({ report }: { report: ImportReport }) {
  const clean = !report.unmapped_parts.length && !report.unmapped_colours.length && !report.warnings.length;
  return <div className="import-report">
    <dl className="report-facts">
      <div><dt>Format</dt><dd>{report.dialect}</dd></div>
      <div><dt>Rows</dt><dd>{report.total_rows.toLocaleString()}</dd></div>
      <div><dt>Pieces</dt><dd>{report.total_pieces.toLocaleString()}</dd></div>
      <div><dt>Spares</dt><dd>{report.spare_pieces.toLocaleString()}</dd></div>
    </dl>
    {clean && <p className="ok-text">Every part and colour was matched.</p>}
    {report.warnings.length > 0 && <div className="banner" role="status">{report.warnings.map((w, i) => <div key={i}>{w}</div>)}</div>}
    <Unmapped title="Parts not recognised" items={report.unmapped_parts}
      hint="These Rebrickable parts have no LDraw equivalent, so the agent will not see them."
      label={(p) => `${p.rb_part} · colour ${p.colour} · ×${p.qty}`} />
    <Unmapped title="Colours not recognised" items={report.unmapped_colours}
      hint="These colours could not be mapped to LDraw, so the agent will not see these pieces."
      label={(c) => `${c.part} · Rebrickable colour ${c.rb_colour} · ×${c.qty}`} />
  </div>;
}
