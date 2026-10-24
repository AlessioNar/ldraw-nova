import { useEffect, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { api } from "../api";
import { useApp } from "../context";
import { agentProvider, providerLabel } from "../modelChoices";

/** Shown when the default model uses a provider key that the provider is known to have rejected. */
export default function KeyBanner() {
  const { llms, defaultLlmId } = useApp();
  const [rejected, setRejected] = useState<string[]>([]);
  const { pathname } = useLocation();
  useEffect(() => { api.providerKeys().then(r => setRejected(r.rejected ?? [])).catch(() => {}); }, [llms, pathname]);
  const model = llms.find(m => m.id === defaultLlmId);
  const key = String(model?.litellm_params.api_key ?? "");
  const provider = model ? agentProvider(String(model.litellm_params.model)) : "";
  if (!model || !rejected.includes(provider) || (key && !key.startsWith("os.environ/"))) return null;
  return <div className="banner error key-banner" role="alert">
    {provider === "anthropic" ? "Anthropic" : providerLabel(provider)} rejected your API key, so {model.model_name} cannot answer. <Link to="/settings">Open Settings</Link> and paste a new key.
  </div>;
}
