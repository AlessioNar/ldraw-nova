import { useState } from "react";
import { type Approval, type Chat, type ChatDetail, type LiveWorker } from "../api";
import type { Activity, RunningTool } from "../hooks/useChatStream";
import UsageBadge from "./UsageBadge";

const TOOL_WORDS: Record<string, string> = {
  find_parts: "Searching parts", my_parts: "Searching your parts", buildable_models: "Ranking buildable models",
  render_model: "Rendering views", run_toolkit: "Running build", run_shell: "Running a command", run_python: "Running Python",
  publish_model: "Publishing the model", save_model: "Saving the model", write_file: "Writing files", read_file: "Reading files",
  list_files: "Listing files", view_image: "Reviewing a render",
};

const clock = (s: number) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
const minutes = (s: number) => (s < 60 ? `${s} s` : `${Math.round(s / 60)} min`);

/** First line of a report_progress summary, stripped of markdown marks. */
const plain = (md: string) => (md.split("\n").find(l => l.trim()) ?? "").replace(/[*_`#>]/g, "").replace(/^\s*[-\d.]+\s+/, "").trim();

/** Median step count of the viewer's finished chats: the "~M" of "Step N/~M". Needs 3 chats to mean anything. */
export function estimateSteps(chats: Chat[]): number | null {
  const steps = chats.filter(c => !c.running && c.turn_steps).map(c => c.turn_steps as number).sort((a, b) => a - b);
  if (steps.length < 3) return null;
  const mid = steps.length >> 1;
  return Math.round(steps.length % 2 ? steps[mid] : (steps[mid - 1] + steps[mid]) / 2);
}

/** Sticky strip under the chat title: step, phase, elapsed time and cost against the budget while a build runs; a one-line summary after. */
export default function BuildHeader({ detail, chats, running, connected, activity, tools, workers, approvals, now, onStop }: {
  detail: ChatDetail; chats: Chat[]; running: boolean; connected: boolean; activity: Activity | null; tools: RunningTool[];
  workers: LiveWorker[]; approvals: Approval[]; now: number; onStop: () => void;
}) {
  const [open, setOpen] = useState(false);
  const usage = detail.usage;
  const doneSteps = usage?.turn_steps ?? 0;
  if (!running && !doneSteps) return null;

  const cost = usage?.cost_eur ?? 0;
  const priced = !!usage && (cost > 0 || !usage.unpriced_steps);
  const budget = detail.chat.options?.budget_eur;
  const ratio = budget ? cost / budget : 0;
  const level = ratio >= 1 ? "over" : ratio >= 0.8 ? "near" : "";
  const euro = priced ? `€${cost.toFixed(2)}` : "€–";
  const toolName = tools.length ? tools[tools.length - 1].name : "";
  const active = workers.filter(w => w.status === "running").length;

  const panel = open && usage && <div className="bh-panel"><UsageBadge usage={usage} />
    {workers.length > 0 && <ul>{workers.map(w => <li key={w.id}>{w.role}: {w.steps}/{w.max_steps} steps · €{w.eur.toFixed(2)}</li>)}</ul>}
  </div>;
  const costButton = <button type="button" className={`bh-cost ${level}`} aria-expanded={open} onClick={() => setOpen(o => !o)}
    title="Show where the cost goes">
    <strong>{euro}</strong>{budget ? <span> / €{budget.toFixed(2)}</span> : null}
    {budget ? <i className="bh-bar" aria-hidden><b style={{ width: `${Math.min(100, ratio * 100)}%` }} /></i> : null}
  </button>;

  if (!running) {
    const jump = () => [...document.querySelectorAll('[id^="model-"]')].pop()?.scrollIntoView({ block: "center", behavior: "smooth" });
    const hasModel = Object.keys(detail.models).length > 0;
    return (
      <div className="build-header done" role="status">
        <div className="bh-line">
          <span className="bh-state">Done</span>
          {usage?.duration_s != null && <span>{minutes(usage.duration_s)}</span>}
          {costButton}
          <span>{doneSteps} step{doneSteps === 1 ? "" : "s"}</span>
          {hasModel && <button type="button" className="bh-jump" onClick={jump}>Jump to result</button>}
        </div>
        {panel}
      </div>
    );
  }

  const step = doneSteps + 1;
  const estimate = estimateSteps(chats);
  const phase = !connected ? "Reconnecting to live updates (your build continues on the server)"
    : approvals.length ? "Waiting for your approval"
    : (activity?.progress && plain(activity.progress)) || TOOL_WORDS[toolName] || (toolName ? "Working" : "Thinking");
  const elapsed = activity ? Math.max(0, Math.floor(now / 1000 - activity.started_at)) : 0;
  return (
    <div className="build-header" role="status" aria-live="polite">
      <div className="bh-line top">
        <span className="spinner" aria-hidden />
        <span className="bh-step">Step {step}{estimate && step <= estimate ? `/~${estimate}` : ""}</span>
        <span className="bh-phase">{phase}</span>
        <time>{clock(elapsed)}</time>
      </div>
      <div className="bh-line">
        {costButton}
        {workers.length > 0 && <span className="bh-workers">{workers.length} worker{workers.length === 1 ? "" : "s"}{active ? ` (${active} running)` : ""}</span>}
        <button type="button" className="bh-stop" onClick={onStop}>Stop</button>
      </div>
      {panel}
    </div>
  );
}
