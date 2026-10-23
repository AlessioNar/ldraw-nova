import { type Usage, type UsageTotals } from "../api";
import { tokenLabel } from "../modelChoices";

// The chat's cost breakdown, shown in the build header's cost panel. Raw counters only:
// providers disagree on whether `prompt` already includes the cached tokens, so a
// percentage would be a guess; a non-zero "cached" figure is the signal that prompt
// caching is actually working. A single step's usage (no totals) is not shown in the transcript.
export default function UsageBadge({ usage }: { usage: Usage | UsageTotals }) {
  if (!("cost_eur" in usage)) return null;
  const workerEur = usage.worker_cost_eur ?? 0;
  const parts = [`${tokenLabel(usage.prompt)} in`, `${tokenLabel(usage.completion)} out`];
  if (usage.cache_read) parts.push(`${tokenLabel(usage.cache_read)} cached`);
  if (usage.cache_write) parts.push(`${tokenLabel(usage.cache_write)} cache write`);
  return (
    <div className="usage" title="Tokens reported by the provider. 'cached' is a prompt-cache read.">
      {workerEur > 0 && <p>Planner €{(usage.cost_eur - workerEur).toFixed(2)} · workers €{workerEur.toFixed(2)}</p>}
      <p>{parts.join(" · ")}</p>
      <p>Estimated from the models' list prices at {usage.eur_per_usd} EUR per USD
        {usage.unpriced_steps ? `; ${usage.unpriced_steps} step(s) have no price and are not counted` : ""}.</p>
    </div>
  );
}
