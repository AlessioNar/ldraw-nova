import { useEffect, useMemo, useState } from "react";
import { api, type CostDay, type CostHistory } from "../api";

// Order and labels of the kinds the backend records (usage_log.KINDS).
const KINDS: { kind: string; label: string }[] = [
  { kind: "agent", label: "Chat agent" },
  { kind: "worker", label: "Workers" },
  { kind: "understand", label: "Understanding" },
  { kind: "other", label: "Other" },
];

const eur = (value: number) => `€${value.toFixed(2)}`;

type DayRow = { day: string; eur: number; calls: number; unpriced: number; byKind: Record<string, number> };

/** Rows per UTC day with one euro column per kind, newest day first. */
function pivot(days: CostDay[]): DayRow[] {
  const rows = new Map<string, DayRow>();
  for (const d of days) {
    const row = rows.get(d.day) ?? { day: d.day, eur: 0, calls: 0, unpriced: 0, byKind: {} };
    row.eur += d.cost_eur;
    row.calls += d.calls;
    row.unpriced += d.unpriced_calls;
    row.byKind[d.kind] = (row.byKind[d.kind] ?? 0) + d.cost_eur;
    rows.set(d.day, row);
  }
  return [...rows.values()].sort((a, b) => b.day.localeCompare(a.day));
}

/** The spend history: totals and a per-day table over GET /api/costs, optionally between two dates. */
export default function Costs() {
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [history, setHistory] = useState<CostHistory | null>(null);
  const [error, setError] = useState<string | null>(null);
  const rangeInvalid = Boolean(from && to && from > to);

  useEffect(() => {
    if (rangeInvalid) return;
    let alive = true;
    setError(null);
    api
      .costs(from || undefined, to || undefined)
      .then((r) => alive && setHistory(r))
      .catch((e) => alive && setError((e as Error).message));
    return () => {
      alive = false;
    };
  }, [from, to, rangeInvalid]);

  const rows = useMemo(() => (history ? pivot(history.days) : []), [history]);
  const kinds = KINDS.filter(({ kind }) => history?.days.some((d) => d.kind === kind));

  return (
    <div className="page costs">
      <header className="page-head">
        <div>
          <h1>Costs</h1>
          <p className="muted">What your model calls have cost, per day. Estimated from the models' list prices.</p>
        </div>
      </header>

      <div className="costs-range">
        <label>From <input type="date" value={from} max={to || undefined} onChange={(e) => setFrom(e.target.value)} /></label>
        <label>To <input type="date" value={to} min={from || undefined} onChange={(e) => setTo(e.target.value)} /></label>
        {(from || to) && <button type="button" onClick={() => { setFrom(""); setTo(""); }}>All time</button>}
      </div>
      {rangeInvalid && <div className="banner error" role="alert">The first date is after the last one.</div>}
      {error && <div className="banner error" role="alert">{error}</div>}

      {history && !rangeInvalid && (
        <>
          <section className="costs-totals" aria-label="Totals">
            <div className="panel"><small className="muted">Spend</small><strong className="mono">{eur(history.totals.cost_eur)}</strong></div>
            <div className="panel"><small className="muted">Model calls</small><strong className="mono">{history.totals.calls}</strong></div>
            <div className="panel"><small className="muted">Unpriced calls</small><strong className="mono">{history.totals.unpriced_calls}</strong></div>
          </section>
          <p className="muted small">
            Days are UTC. Converted at {history.eur_per_usd} EUR per USD.
            {history.totals.unpriced_calls > 0 && ` ${history.totals.unpriced_calls} call(s) have no price and are not counted.`}
          </p>

          {rows.length ? (
            <div className="table-wrap">
              <table className="costs-table">
                <thead>
                  <tr>
                    <th scope="col">Day</th>
                    {kinds.map(({ kind, label }) => <th scope="col" key={kind} className="num">{label}</th>)}
                    <th scope="col" className="num">Calls</th>
                    <th scope="col" className="num">Total</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => (
                    <tr key={row.day}>
                      <th scope="row"><time dateTime={row.day}>{row.day}</time></th>
                      {kinds.map(({ kind }) => <td key={kind} className="num mono">{row.byKind[kind] !== undefined ? eur(row.byKind[kind]) : "–"}</td>)}
                      <td className="num mono">{row.calls}</td>
                      <td className="num mono strong">{eur(row.eur)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="muted">No model calls in this period.</p>
          )}
        </>
      )}
    </div>
  );
}
