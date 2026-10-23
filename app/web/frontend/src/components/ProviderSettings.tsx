import { useCallback, useEffect, useState, type FormEvent } from "react";
import { api, type EnvironmentUpdate, type LlmEntry, type ModelProfile } from "../api";
import { useApp } from "../context";
import { agentProvider, isUnsupported, providerLabel } from "../modelChoices";
import { priceLabel } from "./ModelPicker";

export const PROVIDERS = [
  { id: "anthropic", name: "Anthropic", env: "ANTHROPIC_API_KEY", keysUrl: "https://platform.claude.com/settings/keys" },
  { id: "openai", name: "OpenAI", env: "OPENAI_API_KEY", keysUrl: "https://platform.openai.com/api-keys" },
  { id: "mistral", name: "Mistral", env: "MISTRAL_API_KEY", keysUrl: "https://console.mistral.ai/api-keys" },
  { id: "openrouter", name: "OpenRouter", env: "OPENROUTER_API_KEY", keysUrl: "https://openrouter.ai/settings/keys" },
];

const reference = (env: string) => `os.environ/${env}`;
const keyOf = (m: LlmEntry) => String(m.litellm_params.api_key ?? "");
/** An entry that carries its own literal key instead of a reference to an environment variable. */
const hasOwnKey = (m: LlmEntry) => !!keyOf(m) && !keyOf(m).startsWith("os.environ/");

/** Store the key once as the provider's environment variable (empty string removes it). */
async function storeKey(env: string, value: string) {
  const { variables } = await api.environment();
  const rows: EnvironmentUpdate[] = variables.filter(v => !(v.name === env && value === "")).map(v => ({ id: v.id, name: v.name, value: v.name === env ? value : null }));
  if (value && !variables.some(v => v.name === env)) rows.push({ name: env, value });
  await api.saveEnvironment(rows);
}

function ProviderCard({ provider, hasKey, saved, onChanged }: { provider: typeof PROVIDERS[number]; hasKey: boolean; saved: boolean; onChanged: () => void }) {
  const { refreshLlms } = useApp();
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null);
  const run = async (job: () => Promise<void>) => {
    setBusy(true); setResult(null);
    try { await job(); } catch (e) { setResult({ ok: false, text: (e as Error).message }); }
    finally { setBusy(false); }
  };
  const save = (event: FormEvent) => { event.preventDefault(); if (!value.trim()) return; run(async () => {
    await storeKey(provider.env, value.trim()); setValue(""); onChanged(); refreshLlms();
    setResult({ ok: true, text: "Key saved. Click Test to check it." });
  }); };
  const test = () => run(async () => {
    const r = await api.testProvider(provider.id);
    setResult({ ok: r.ok, text: (r.ok ? r.message : r.error) ?? "" });
  });
  const remove = () => run(async () => { await storeKey(provider.env, ""); onChanged(); refreshLlms(); });
  return <form className="panel provider-card" onSubmit={save} aria-label={`${provider.name} key`}>
    <header className="provider-card-head"><strong>{provider.name}</strong>
      <span className={`badge ${hasKey ? "ok" : ""}`}>{hasKey ? "Key set" : "No key"}</span></header>
    <label className="provider-key"><span className="muted small">API key · <a href={provider.keysUrl} target="_blank" rel="noreferrer">get one</a></span>
      <input type="password" autoComplete="new-password" spellCheck={false} maxLength={65536} disabled={busy}
        aria-label={`${provider.name} API key`} placeholder={hasKey ? "Key saved · type to replace" : "Paste your API key"}
        value={value} onChange={e => setValue(e.target.value)} /></label>
    <div className="provider-card-actions">
      <button type="submit" className="primary" disabled={busy || !value.trim()}>Save key</button>
      <button type="button" disabled={busy || !hasKey} onClick={test}>{busy ? "Working…" : "Test"}</button>
      {saved && <button type="button" className="danger-text" disabled={busy} onClick={remove}>Remove key</button>}
    </div>
    {result && <p className={`small ${result.ok ? "ok-text" : "warn-text"}`} role="status">{result.text}</p>}
    <p className="muted small">Test uses the provider's free models list, so it costs nothing. Stored as {provider.env}.</p>
  </form>;
}

function ModelRow({ name, profile, entry, env, busy, onAdd, onRemove, onUseProviderKey }: {
  name: string; profile: ModelProfile; entry?: LlmEntry; env: string; busy: boolean;
  onAdd: () => void; onRemove: () => void; onUseProviderKey: () => void;
}) {
  return <li className="panel model-row">
    <div className="llm-info">
      <div className="llm-title"><strong>{name}</strong>
        {entry && hasOwnKey(entry) && <span className="badge warn" title="This entry stores its own key and keeps working as is.">own key</span>}
      </div>
      <small className="muted">{priceLabel({ profile })}</small>
    </div>
    <div className="llm-actions">
      {entry && hasOwnKey(entry) && <button type="button" disabled={busy} title={`Replace the stored key with ${reference(env)}`} onClick={onUseProviderKey}>Use the provider key</button>}
      {entry ? <button type="button" disabled={busy} aria-pressed onClick={onRemove}>Remove</button>
        : <button type="button" className="primary" disabled={busy} onClick={onAdd}>Add</button>}
    </div>
  </li>;
}

export default function ProviderSettings({ catalog, onError }: { catalog: ModelProfile[]; onError: (message: string) => void }) {
  const { llms, refreshLlms } = useApp();
  const [keys, setKeys] = useState<Record<string, boolean>>({});
  const [savedNames, setSavedNames] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const load = useCallback(() => {
    api.providerKeys().then(r => setKeys(r.keys)).catch(e => onError(e.message));
    api.environment().then(r => setSavedNames(r.variables.filter(v => v.has_value).map(v => v.name))).catch(() => {});
  }, [onError]);
  useEffect(load, [load]);
  const act = async (job: () => Promise<unknown>) => {
    setBusy(true); onError("");
    try { await job(); refreshLlms(); } catch (e) { onError((e as Error).message); } finally { setBusy(false); }
  };
  const supported = llms.filter(m => !isUnsupported(m));
  return <>
    <section aria-labelledby="providers-heading">
      <header className="page-head settings-models-head"><div><h2 id="providers-heading">Providers</h2>
        <p className="muted small">Paste one API key per provider. It is saved once and never shown again.</p></div></header>
      <div className="provider-cards">{PROVIDERS.map(p =>
        <ProviderCard key={p.id} provider={p} hasKey={!!keys[p.id]} saved={savedNames.includes(p.env)} onChanged={load} />)}</div>
    </section>
    <section aria-labelledby="models-heading">
      <header className="page-head settings-models-head"><div><h2 id="models-heading">Models</h2>
        <p className="muted small">Add the models you want to pick from in a chat. They all use the provider key above.</p></div></header>
      {PROVIDERS.map(p => {
        const mine = supported.filter(m => agentProvider(String(m.litellm_params.model)) === p.id);
        const presets = catalog.filter(m => agentProvider(m.model) === p.id);
        const extra = mine.filter(m => !presets.some(c => c.model === m.litellm_params.model));
        const add = (c: ModelProfile) => act(() => api.createLlm({ model_name: c.name, litellm_params: { model: c.model, api_key: reference(p.env) } }));
        const useKey = (m: LlmEntry) => act(() => api.updateLlm(m.id, { ...m, litellm_params: { ...m.litellm_params, api_key: reference(p.env) } }));
        const remove = (m: LlmEntry) => act(() => api.deleteLlm(m.id));
        return <section className="agent-provider models-provider" key={p.id} aria-label={`${p.name} models`}>
          <header className="agent-provider-head"><strong>{p.name}</strong><span className="badge">{mine.length} added</span></header>
          <div className="agent-provider-body">
            {!keys[p.id] && !mine.length ? <p className="muted small">Add your {p.name} key above to choose models.</p> :
              <ul className="model-rows">
                {presets.map(c => { const entry = mine.find(m => m.litellm_params.model === c.model);
                  return <ModelRow key={c.model} name={c.name} profile={c} entry={entry} env={p.env} busy={busy}
                    onAdd={() => add(c)} onRemove={() => entry && remove(entry)} onUseProviderKey={() => entry && useKey(entry)} />; })}
                {extra.map(m => <ModelRow key={m.id} name={m.model_name} profile={m.profile} entry={m} env={p.env} busy={busy}
                  onAdd={() => {}} onRemove={() => remove(m)} onUseProviderKey={() => useKey(m)} />)}
              </ul>}
            {!keys[p.id] && mine.length > 0 && <p className="muted small">No {providerLabel(p.id)} key is set, so these models cannot run yet.</p>}
          </div>
        </section>;
      })}
    </section>
  </>;
}
