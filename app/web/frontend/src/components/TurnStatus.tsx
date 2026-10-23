import { api, type Approval, type LiveWorker, type WorkerContract } from "../api";
import type { Activity } from "../hooks/useChatStream";

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

/** What the running turn is up to, above the composer: progress line, context notice and pending tool approvals. */
export default function TurnStatus({ chatId, running, connected, approvals, workers, activity, now, contextNotice, onError }: {
  chatId: string; running: boolean; connected: boolean; approvals: Approval[]; workers: LiveWorker[]; activity: Activity | null; now: number;
  contextNotice: string; onError: (message: string) => void;
}) {
  const elapsed = activity ? Math.max(0, Math.floor(now / 1000 - activity.started_at)) : 0;
  const active = workers.filter(w => w.status === "running").length;
  const answer = (approval: string, approved: boolean) => api.approve(chatId, approval, approved).catch(e => onError(e.message));
  return (
    <>
      {running && <div className="build-activity" role="status" aria-live="polite">
        <span className="spinner" aria-hidden />
        <span>{!connected ? "Reconnecting to live updates… Your build continues on the server." :
          approvals.length ? "Waiting for your approval" : active ? `${active} worker${active === 1 ? "" : "s"} running` : activity?.phase || "Agent is working…"}</span>
        <time>{Math.floor(elapsed / 60)}:{String(elapsed % 60).padStart(2, "0")}</time>
      </div>}
      {contextNotice && <p className="muted small">{contextNotice}</p>}
      {approvals.map(a => <div className="panel approval" key={a.id}>
        <strong>Allow {a.name}?</strong>{a.contract ? <ContractView c={a.contract} /> : <pre>{a.arguments}</pre>}
        <button type="button" onClick={() => answer(a.id, false)}>Deny</button>{" "}
        <button type="button" className="primary" onClick={() => answer(a.id, true)}>Allow once</button>
      </div>)}
    </>
  );
}
