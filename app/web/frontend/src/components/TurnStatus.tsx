import { api, type Approval, type WorkerContract } from "../api";

/** The whole contract of a delegation, shown once for approval: workers never ask again. */
function ContractView({ c }: { c: WorkerContract }) {
  if (c.error) return <p className="worker-notice bad">This delegation would be refused: {c.error}</p>;
  const rows: [string, string][] = [
    ["Role", c.role ?? ""], ["Task", c.task ?? ""], ["Worker model", c.model ?? ""], ["Tools", (c.tools ?? []).join(", ") || "none"],
    ["May read", (c.inputs ?? []).join(", ") || "toolkit docs only"], ["Must write", (c.outputs ?? []).join(", ") || "nothing (reply only)"],
    ["Write folder", c.write_folder ?? ""], ["Step cap", String(c.max_steps ?? "")], ["Budget", c.max_eur != null ? `up to €${c.max_eur.toFixed(2)}` : ""],
  ];
  return (
    <dl className="contract">
      {rows.filter(([, v]) => v).map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}
    </dl>
  );
}

/** Context notice and pending tool approvals above the composer (progress, time and cost live in BuildHeader). */
export default function TurnStatus({ chatId, approvals, contextNotice, onError }: {
  chatId: string; approvals: Approval[]; contextNotice: string; onError: (message: string) => void;
}) {
  const answer = (approval: string, approved: boolean) => api.approve(chatId, approval, approved).catch(e => onError(e.message));
  return (
    <>
      {contextNotice && <p className="muted small">{contextNotice}</p>}
      {approvals.map(a => <div className="panel approval" key={a.id}>
        <strong>Allow {a.name}?</strong>{a.contract ? <ContractView c={a.contract} /> : <pre>{a.arguments}</pre>}
        <button type="button" onClick={() => answer(a.id, false)}>Deny</button>{" "}
        <button type="button" className="primary" onClick={() => answer(a.id, true)}>Allow once</button>
      </div>)}
    </>
  );
}
