import { type Usage, type UsageTotals } from "../api";
import { tokenLabel } from "../modelChoices";

// Raw counters only. Providers disagree on whether `prompt` already includes the
// cached tokens, so a percentage here would be a guess; a non-zero "cached"
// figure is the signal that prompt caching is actually working.
export default function UsageBadge({ usage, steps, budget }: { usage: Usage | UsageTotals; steps?: number; budget?: number | null }) {
  const totals = "cost_eur" in usage ? usage : null;
  const parts = [`${tokenLabel(usage.prompt)} in`, `${tokenLabel(usage.completion)} out`];
  if (usage.cache_read) parts.push(`${tokenLabel(usage.cache_read)} cached`);
  if (usage.cache_write) parts.push(`${tokenLabel(usage.cache_write)} cache write`);
  if (steps) parts.push(`${steps} step${steps === 1 ? "" : "s"}`);
  const priced = totals && (totals.cost_eur > 0 || !totals.unpriced_steps);
  const near = !!budget && !!totals && totals.cost_eur >= budget * 0.8;
  return (
    <div className="usage" title="Tokens reported by the provider. 'cached' is a prompt-cache read.">
      {priced && totals && <strong className={near ? "usage-cost near-budget" : "usage-cost"}
        title={`Estimated from the model's list prices at ${totals.eur_per_usd} EUR per USD${totals.unpriced_steps ? `; ${totals.unpriced_steps} step(s) have no price and are not counted` : ""}.`}>
        ≈ €{totals.cost_eur.toFixed(2)}{budget ? ` of €${budget.toFixed(2)}` : ""}</strong>}
      {priced && " · "}{parts.join(" · ")}
    </div>
  );
}
