import { useState } from "react";
import type { LiveWorker, Message, ToolCall } from "../api";
import WorkerCard, { workerView } from "./WorkerCard";
import { parseArgs, type Status } from "./ToolCard";

export type WorkerItem = { call: ToolCall; result?: Message; status: Status; worker?: LiveWorker };

const SHORT: Record<string, string> = { queued: "waiting", ok: "done", over_budget: "over budget", step_cap: "step cap" };

/** All delegate calls of one batch: a header with totals, one compact row per worker. */
export default function WorkerGroup({ items, chatId, eurPerUsd }: { items: WorkerItem[]; chatId: string; eurPerUsd?: number }) {
  const [open, setOpen] = useState(true);
  const views = items.map(i => workerView({ args: parseArgs(i.call.function.arguments), result: i.result, status: i.status, live: i.worker, eurPerUsd }));
  const tally = new Map<string, number>();
  for (const v of views) tally.set(v.state, (tally.get(v.state) ?? 0) + 1);
  const states = [...tally].map(([s, n]) => `${n} ${SHORT[s] ?? s}`).join(", ");
  const spent = views.reduce((n, v) => n + (v.cost ?? 0), 0);
  const cap = views.reduce((n, v) => n + v.maxEur, 0);
  const bad = views.some(v => v.info.tone === "bad");
  return (
    <div className={`worker-group${bad ? " has-failed" : ""}`}>
      <button type="button" className="worker-group-head" aria-expanded={open} onClick={() => setOpen(!open)}>
        <strong>Workers: {items.length}</strong>
        <span className="ellipsis">{states}</span>
        <span className="worker-group-cost">€{spent.toFixed(2)}{cap > 0 ? ` of €${cap.toFixed(2)}` : ""}</span>
        <span className="step-chevron" aria-hidden>›</span>
      </button>
      {open && <div className="worker-group-body">
        {items.map(i => <WorkerCard key={i.call.id} chatId={chatId} args={parseArgs(i.call.function.arguments)} result={i.result} status={i.status} live={i.worker} eurPerUsd={eurPerUsd} />)}
      </div>}
    </div>
  );
}
