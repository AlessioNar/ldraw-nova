import { useEffect, useRef, useState, type FormEvent } from "react";
import { api, type EnvironmentState, type EnvironmentVariable } from "../api";
import { useApp } from "../context";

type Row = { key: string; id?: string; name: string; value: string | null; has_value: boolean; fixed: boolean };
const toRows = (variables: EnvironmentVariable[]): Row[] => variables.map(v => ({ ...v, key: v.id }));

function ReplacementWarning({ row }: { row: Row }) {
  const [warning, setWarning] = useState("");
  useEffect(() => {
    let active = true;
    setWarning("");
    const timer = setTimeout(() => {
      api.checkEnvironment(row.name.trim(), row.id).then(result => {
        if (active) setWarning(result.preconfigured
          ? "This name is already preconfigured. The value you save here takes precedence and replaces that value for new requests."
          : result.saved ? "This variable already has a saved row. Edit that row instead of adding it again." : "");
      }).catch(() => {});
    }, 250);
    return () => { active = false; clearTimeout(timer); };
  }, [row.name, row.id]);
  return warning ? <span className="environment-warning" tabIndex={0} role="img" aria-label={warning} title={warning}>
    <svg viewBox="0 0 20 20" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><path d="M10 2 19 18H1Z" strokeLinejoin="round" /><path d="M10 7v5m0 2v1" strokeLinecap="round" /></svg>
    <span className="environment-tooltip" aria-hidden="true">{warning}</span>
  </span> : null;
}

const FIXED_LABELS: Record<string, string> = { JEV_OPENROUTER_API_KEY: "Jev search key (OpenRouter, optional)" };
const FIXED_HINTS: Record<string, string> = {
  TYPESAFE_API_KEY: "Optional. Enables Jev semantic search for parts and models. Without it, searches use keyword (BM25) ranking, which works but ranks less well.",
  JEV_OPENROUTER_API_KEY: "Optional. Jev search through OpenRouter with a key of its own: create a separate key on OpenRouter with a low spend cap, because agent code can read it. Used when TYPESAFE_API_KEY is empty; if both are empty, Jev falls back to OPENROUTER_API_KEY.",
};

export default function EnvironmentSettings() {
  const { refreshLlms } = useApp();
  const [rows, setRows] = useState<Row[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [info, setInfo] = useState<Pick<EnvironmentState, "jev_source" | "sandbox_isolation" | "database">>({});
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const nextKey = useRef(0);

  useEffect(() => {
    let active = true;
    api.environment().then(result => {
      if (active) { setRows(toRows(result.variables)); setInfo({ jev_source: result.jev_source, sandbox_isolation: result.sandbox_isolation, database: result.database }); setLoaded(true); }
    }).catch(e => { if (active) setError(e.message); });
    return () => { active = false; };
  }, []);

  function change(key: string, patch: Partial<Row>) {
    setRows(current => current.map(row => row.key === key ? { ...row, ...patch } : row));
    setDirty(true); setMessage(""); setError("");
  }

  async function save(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError(""); setMessage("");
    try {
      const result = await api.saveEnvironment(rows.map(({ id, name, value }) => ({ id, name: name.trim(), value })));
      setRows(toRows(result.variables)); setDirty(false);
      api.environment().then(state => setInfo({ jev_source: state.jev_source, sandbox_isolation: state.sandbox_isolation, database: state.database })).catch(() => {});
      refreshLlms();
      setMessage("Environment variables saved. New requests use these values.");
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }

  return <section className="panel environment-settings" aria-labelledby="environment-heading">
    <div className="environment-heading">
      <h2 id="environment-heading">Environment variables</h2>
      <button type="button" disabled={!loaded || busy} onClick={() => {
        const key = `new-${nextKey.current++}`;
        setRows(current => [...current, { key, name: "", value: "", has_value: false, fixed: false }]);
        setDirty(true); setMessage(""); setError("");
      }}>Add</button>
    </div>
    {info.sandbox_isolation?.state === "off" && <p className="warn-text" role="alert">Sandbox isolation: off. {info.sandbox_isolation.detail} Code the agent runs can reach this app's own API. Start the container with cap_add NET_ADMIN (see docker-compose.yml).</p>}
    {info.sandbox_isolation?.state === "on" && <p className="ok-text small">Sandbox isolation: on. Agent code cannot reach this app's API.</p>}
    {info.database?.state === "ok" && <p className="ok-text small">Database: ok. {info.database.detail}</p>}
    {info.database?.state === "unavailable" && <p className="warn-text" role="alert">Database: unavailable ({info.database.detail}). Builds still work; the request log and cost history are paused.</p>}
    <p className="muted small">Click Add, enter a variable name and value, then Save. If the variable is missing or empty, this supplies its value. If it is already preconfigured, the value saved here takes precedence.</p>
    <p className="muted small">Values are hidden for everyone. Leave a saved value untouched to keep it, or type a new value to replace it. An empty value saved here means “no value”.</p>
    <form onSubmit={save}>
      <div className="environment-rows">
        {rows.map((row, index) => row.fixed ? <div className="required-environment" key={row.key}>
          <label className="required-environment-row"><span>{FIXED_LABELS[row.name] ?? row.name}:</span>
            <span className="environment-value"><input aria-label={`${row.name} value`} aria-describedby={`${row.name}-hint`} type="password" autoComplete="new-password" maxLength={65536} disabled={busy}
              placeholder={row.has_value ? "Value saved · type to replace" : "Enter value"} value={row.value ?? ""}
              onChange={e => change(row.key, { value: e.target.value })} />
              <ReplacementWarning row={row} /></span>
          </label>
          <small id={`${row.name}-hint`} className="muted typesafe-hint"><em>{FIXED_HINTS[row.name]}</em></small>
          {row.name === "JEV_OPENROUTER_API_KEY" && info.jev_source === "openrouter" && <p className="warn-text small" role="status">
            No separate Jev key is set, so Jev search uses your main OpenRouter key (OPENROUTER_API_KEY). Code the agent runs can read and send that key. Create a separate, spend-capped key above to avoid this.</p>}
        </div> : <div className="environment-row" key={row.key}>
          <label>
            <span>Name</span>
            <input aria-label={`Variable name ${index + 1}`} required pattern="[A-Za-z_][A-Za-z0-9_]*" maxLength={255}
              autoComplete="off" spellCheck={false} placeholder="OPENROUTER_API_KEY" disabled={busy}
              value={row.name} onChange={e => change(row.key, { name: e.target.value })} />
          </label>
          <label>
            <span>Value</span>
            <span className="environment-value"><input aria-label={`Variable value ${index + 1}`} type="password" autoComplete="new-password" spellCheck={false} maxLength={65536}
              placeholder={row.has_value ? "Value saved · type to replace" : "Enter value"} disabled={busy}
              value={row.value ?? ""}
              onChange={e => change(row.key, { value: e.target.value })} />
              <ReplacementWarning row={row} /></span>
          </label>
          <button type="button" className="danger-text" aria-label={`Remove variable ${index + 1}`} disabled={busy} onClick={() => {
            setRows(current => current.filter(r => r.key !== row.key)); setDirty(true); setMessage(""); setError("");
          }}>Remove</button>
        </div>)}
      </div>
      {!loaded && !error && <p className="muted small">Loading environment variables…</p>}
      {rows.length > 1 && <p className="muted small">Remove a variable and save to use its preconfigured value again, if there is one.</p>}
      {error && <p className="warn-text" role="alert">{error}</p>}
      {message && <p className="ok-text small" role="status">{message}</p>}
      <div className="form-actions">
        {dirty && <span className="muted small">Unsaved changes</span>}
        <button type="submit" className="primary" disabled={!loaded || !dirty || busy}>{busy ? "Saving…" : "Save environment variables"}</button>
      </div>
    </form>
  </section>;
}
