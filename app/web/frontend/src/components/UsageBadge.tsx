import { type Usage, type UsageTotals } from "../api";
import { tokenLabel } from "../modelChoices";

// Raw counters only. Providers disagree on whether `prompt` already includes the
// cached tokens, so a percentage here would be a guess; a non-zero "cached"
// figure is the signal that prompt caching is actually working.
export default function UsageBadge({ usage, steps }: { usage: Usage | UsageTotals; steps?: number }) {
  const parts = [`${tokenLabel(usage.prompt)} in`, `${tokenLabel(usage.completion)} out`];
  if (usage.cache_read) parts.push(`${tokenLabel(usage.cache_read)} cached`);
  if (usage.cache_write) parts.push(`${tokenLabel(usage.cache_write)} cache write`);
  if (steps) parts.push(`${steps} step${steps === 1 ? "" : "s"}`);
  return (
    <div className="usage" title="Tokens reported by the provider. 'cached' is a prompt-cache read.">
      {parts.join(" · ")}
    </div>
  );
}
