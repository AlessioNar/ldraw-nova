import { api, type Approval } from "../api";
import type { Activity } from "../hooks/useChatStream";

/** What the running turn is up to, above the composer: progress line, context notice and pending tool approvals. */
export default function TurnStatus({ chatId, running, connected, approvals, activity, now, contextNotice, onError }: {
  chatId: string; running: boolean; connected: boolean; approvals: Approval[]; activity: Activity | null; now: number;
  contextNotice: string; onError: (message: string) => void;
}) {
  const elapsed = activity ? Math.max(0, Math.floor(now / 1000 - activity.started_at)) : 0;
  const answer = (approval: string, approved: boolean) => api.approve(chatId, approval, approved).catch(e => onError(e.message));
  return (
    <>
      {running && <div className="build-activity" role="status" aria-live="polite">
        <span className="spinner" aria-hidden />
        <span>{!connected ? "Reconnecting to live updates… Your build continues on the server." :
          approvals.length ? "Waiting for your approval" : activity?.phase || "Agent is working…"}</span>
        <time>{Math.floor(elapsed / 60)}:{String(elapsed % 60).padStart(2, "0")}</time>
      </div>}
      {contextNotice && <p className="muted small">{contextNotice}</p>}
      {approvals.map(a => <div className="panel approval" key={a.id}>
        <strong>Allow {a.name}?</strong><pre>{a.arguments}</pre>
        <button type="button" onClick={() => answer(a.id, false)}>Deny</button>{" "}
        <button type="button" className="primary" onClick={() => answer(a.id, true)}>Allow once</button>
      </div>)}
    </>
  );
}
