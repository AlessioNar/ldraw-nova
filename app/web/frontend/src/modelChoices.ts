import type { LlmEntry, ProviderInfo } from "./api";

// Display names come from the backend registry (providers.py) via
// /api/llm-providers; until that loads, a provider shows as its prefix.
let labels: Record<string, string> = {};
export function setProviderCatalogue(catalogue: ProviderInfo[]) {
  labels = Object.fromEntries(catalogue.map(p => [p.provider, p.label]));
}

export const agentProvider = (model: string) => model.startsWith("chatgpt/") ? "openai" : model.split("/")[0];
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
  const provider = String(model.litellm_params.model).split("/")[0];
  if (provider === "openai" && model.auth_mode === "browser") return providerLabel("chatgpt");
  return providerLabel(provider);
}

export function tokenLabel(tokens: number) {
  if (tokens >= 1_000_000) return `${Number((tokens / 1_000_000).toFixed(2))}M`;
  if (tokens >= 1000) return `${Number((tokens / 1000).toFixed(2))}K`;
  return String(tokens);
}
