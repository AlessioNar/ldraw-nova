import type { LlmEntry, ProviderInfo } from "./api";

// Display names come from the backend registry (providers.py) via
// /api/llm-providers; until that loads, a provider shows as its prefix.
let labels: Record<string, string> = {};
export function setProviderCatalogue(catalogue: ProviderInfo[]) {
  labels = Object.fromEntries(catalogue.map(p => [p.provider, p.label]));
}

export const agentProvider = (model: string) => model.split("/")[0];
// Entries saved before browser login was removed (browser auth or a chatgpt/ model)
// stay listed but cannot run. The backend flags them; the fallback covers older responses.
export const isUnsupported = (model: LlmEntry) => model.unsupported ?? model.auth_mode === "browser";
export const UNSUPPORTED_HINT = "Browser login was removed. Edit this agent and use an API key instead.";
export const providerLabel = (provider: string) => labels[provider] ?? provider;

const RECENTS_KEY = "ldraw-nova.recent-models";
export function recentModels(): string[] {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(RECENTS_KEY) ?? "[]");
    return Array.isArray(value) ? value.filter((id): id is string => typeof id === "string").slice(0, 5) : [];
  } catch { return []; }
}

export function rememberModel(id: string) {
  const ids = [id, ...recentModels().filter(previous => previous !== id)].slice(0, 5);
  try { localStorage.setItem(RECENTS_KEY, JSON.stringify(ids)); } catch { /* private browsing */ }
  return ids;
}

export function providerName(model: LlmEntry) {
  return providerLabel(agentProvider(String(model.litellm_params.model)));
}

export function tokenLabel(tokens: number) {
  if (tokens >= 1_000_000) return `${Number((tokens / 1_000_000).toFixed(2))}M`;
  if (tokens >= 1000) return `${Number((tokens / 1000).toFixed(2))}K`;
  return String(tokens);
}
