import { useState } from "react";
import { api, type LiveWorker, type Message, type ToolCall, type WorkerTranscript } from "../api";

/** Every worker status in docs/plans/swarm-subagents/README.md section 2, plus the two the UI adds itself. */
const STATUS: Record<string, { label: string; tone: "ok" | "warn" | "bad" | "run" }> = {
  running: { label: "running", tone: "run" },
  queued: { label: "waiting to start", tone: "run" },
  ok: { label: "done", tone: "ok" },
  partial: { label: "partial: some outputs missing or invalid", tone: "warn" },
  failed: { label: "failed", tone: "bad" },
  over_budget: { label: "stopped: over budget", tone: "bad" },
  step_cap: { label: "stopped: step cap reached", tone: "warn" },
  stuck: { label: "stopped: stuck", tone: "bad" },
  cancelled: { label: "cancelled", tone: "warn" },
  interrupted: { label: "interrupted", tone: "warn" },
};

const ROLE_TITLE: Record<string, string> = { builder: "Builder", researcher: "Researcher", reviewer: "Reviewer" };
const oneLine = (task: string) => task.replace(/\s+/g, " ").trim();

/** Lines of the delegate result the user should see as a notice (failure, budget stop, stray writes). */
function notices(output: string, status: string): string[] {
  const lines = output.split("\n");
  const pick = (prefix: string) => lines.filter(l => l.startsWith(prefix));
  const found = [...pick("Error:"), ...pick("Stopped because"), ...pick("Missing outputs:"), ...pick("Stray writes")];
  if (!found.length && ["failed", "stuck", "partial"].includes(status)) {
    const why = lines.find(l => /^(Stuck|Validation|Repair)/.test(l));
    if (why) found.push(why);
  }
  return found;
}

/** What the worker did, from its side file: calls and text, read only. */
function Transcript({ data }: { data: WorkerTranscript }) {
  const text = (c: unknown) => (typeof c === "string" ? c : "");
  const brief = (call: ToolCall) => `${call.function.name}(${call.function.arguments.slice(0, 160)})`;
  return (
    <div className="worker-transcript">
      {data.messages.map((m, i) => (
        <div key={i} className={`worker-line ${m.role}`}>
          <span className="worker-role">{m.role === "tool" ? `result${m.name ? ` ${m.name}` : ""}` : m.role === "user" ? "task" : "worker"}</span>
          {text(m.content) && <pre>{text(m.content).slice(0, 2000)}</pre>}
          {(m.tool_calls ?? []).map(c => <code key={c.id} className="worker-call">{brief(c)}</code>)}
        </div>
      ))}
      {!!data.files.length && (
        <div className="worker-files">
          <div className="tool-section">Output files</div>
          {data.files.map(f => <a key={f.url} href={f.url} download>{f.name} <span className="muted small">({f.size} bytes)</span></a>)}
        </div>
      )}
    </div>
  );
}

/** One card per delegated worker: role, task, model, live status, steps and euros against the caps. */
export default function WorkerCard({ chatId, args, result, status, live, eurPerUsd }: {
  chatId: string; args: Record<string, unknown>; result?: Message; status: string;
  live?: LiveWorker; eurPerUsd?: number;
}) {
  const [transcript, setTranscript] = useState<WorkerTranscript | null>(null);
  const [loadError, setLoadError] = useState("");
  const saved = result?._worker;
  const output = typeof result?.content === "string" ? result.content : "";
  const workerId = saved?.id ?? live?.id;
  // Final figures come from the saved record (what the chat total sums); live ones from the stream.
  const final = saved ? (saved.status || "ok") : null;
  const state = final ?? live?.status ?? (output.startsWith("Error:") ? "failed" : status);
  const info = STATUS[state] ?? { label: state, tone: "warn" as const };
  const role = live?.role ?? (output.match(/role (\w+)/)?.[1]) ?? String(args.role ?? "worker");
  const task = live?.task ?? String(args.task ?? "");
  const model = live?.model ?? saved?.model ?? "";
  const steps = saved?.steps ?? live?.steps;
  const maxSteps = live?.max_steps ?? Number(output.match(/max (\d+) steps/)?.[1] ?? args.max_steps ?? 0);
  const maxEur = live?.max_eur ?? Number(output.match(/max EUR ([\d.]+)/)?.[1] ?? args.max_eur ?? 0);
  const cost = result?._usage?.cost_usd != null && eurPerUsd ? result._usage.cost_usd * eurPerUsd : live?.eur;
  const notes = result ? notices(output, state) : [];
  const pct = Math.min(100, maxSteps && steps ? (steps / maxSteps) * 100 : 0);

  const load = (open: boolean) => {
    if (!open || !workerId || transcript) return;
    api.worker(chatId, workerId).then(setTranscript).catch(e => setLoadError(e.message));
  };

  return (
    <div className="tool worker-card" data-worker-status={state}>
      <details onToggle={e => load((e.target as HTMLDetailsElement).open)}>
        <summary>
          <span className={`tool-status ${info.tone === "bad" ? "failed" : info.tone === "ok" ? "done" : info.tone === "warn" ? "warn" : "running"}`} aria-hidden />
          <span className="tool-name">{ROLE_TITLE[role] ?? role} worker</span>
          <span className="tool-summary ellipsis">{oneLine(task)}</span>
        </summary>
        <div className="tool-body">
          <div className="tool-section">Task</div>
          <pre>{task}</pre>
          {output && <><div className="tool-section">Result</div><pre className={info.tone === "bad" ? "failed" : ""}>{output}</pre></>}
          {transcript && <Transcript data={transcript} />}
          {loadError && <p className="muted small">The worker transcript could not be loaded: {loadError}</p>}
          {workerId && !transcript && !loadError && <p className="muted small">Loading the worker transcript…</p>}
          {!workerId && <p className="muted small">The transcript is available once the worker has started.</p>}
        </div>
      </details>
      <div className="worker-meter" aria-label="Worker progress">
        <span className={`worker-chip ${info.tone}`}>{info.label}</span>
        {model && <span className="muted small ellipsis" title="Worker model">{model}</span>}
        <span className="small" title="Model calls so far against the step cap">{steps ?? 0}{maxSteps ? ` / ${maxSteps}` : ""} steps</span>
        {cost != null && <span className="small" title="Worker spending against its own budget">€{cost.toFixed(2)}{maxEur ? ` of €${maxEur.toFixed(2)}` : ""}</span>}
        {state === "running" && maxSteps > 0 && <span className="worker-bar" aria-hidden><i style={{ width: `${pct}%` }} /></span>}
      </div>
      {notes.map((n, i) => <p key={i} className={`worker-notice ${n.startsWith("Stray") || info.tone === "ok" ? "warn" : info.tone}`} role="status">{n}</p>)}
    </div>
  );
}
