import { useState } from "react";
import type { ChatModel, Message, ToolCall } from "../api";
import ToolCard, { isFailed, LABELS, parseArgs, resultText, summary, type Status } from "./ToolCard";

export type StepItem = { call: ToolCall; result?: Message; status: Status; models: ChatModel[]; live?: { output?: string; started_at?: number } };

/** Consecutive identical calls (same tool, same summary): a retry loop shown as one row. */
type Run = { items: StepItem[]; last: StepItem; failed: boolean; status: Status };

function toRuns(items: StepItem[]): Run[] {
  const runs: Run[] = [];
  const key = (i: StepItem) => `${i.call.function.name}\u0000${summary(i.call.function.name, parseArgs(i.call.function.arguments))}`;
  for (const item of items) {
    const prev = runs[runs.length - 1];
    if (prev && key(prev.last) === key(item) && prev.last.status === "done") prev.items.push(item);
    else runs.push({ items: [item], last: item, failed: false, status: item.status });
    const run = runs[runs.length - 1];
    run.last = item; run.status = item.status; run.failed = isFailed(resultText(item.result));
  }
  return runs;
}

function Lightbox({ url, onClose }: { url: string; onClose: () => void }) {
  return (
    <div className="lightbox" role="dialog" aria-label="Render" onClick={onClose}>
      <img src={url} alt="render" onClick={e => e.stopPropagation()} />
      <button className="lightbox-close" aria-label="Close" onClick={onClose}>×</button>
    </div>
  );
}

/** Intermediate renders as a thumbnail strip; click opens the full image. */
export function RenderStrip({ urls }: { urls: string[] }) {
  const [open, setOpen] = useState<string | null>(null);
  if (!urls.length) return null;
  return (
    <>
      <div className="render-strip" aria-label="Renders">
        {urls.map((u, i) => <button key={u} type="button" className="render-thumb" title="Open render" onClick={() => setOpen(u)}><img src={u} alt={`Render ${i + 1}`} loading="lazy" /></button>)}
      </div>
      {open && <Lightbox url={open} onClose={() => setOpen(null)} />}
    </>
  );
}

/** A run of tool calls between two prose messages: one collapsed line, expanded while running or when a call failed. */
export default function StepGroup({ items, first, now, chatId }: { items: StepItem[]; first: number; now: number; chatId: string }) {
  const [userOpen, setUserOpen] = useState<boolean | null>(null);
  const runs = toRuns(items);
  const running = runs.some(r => r.status === "running");
  const failedRuns = runs.filter(r => r.failed);
  const interrupted = items.some(i => i.status === "interrupted");
  const auto = running || failedRuns.length > 0;
  const open = userOpen ?? auto;
  const failedCalls = failedRuns.reduce((n, r) => n + r.items.length, 0);
  const done = items.filter(i => i.status === "done").length - failedCalls;
  const counts = new Map<string, number>();
  for (const i of items) { const n = LABELS[i.call.function.name] ?? i.call.function.name; counts.set(n, (counts.get(n) ?? 0) + 1); }
  const labels = [...counts].map(([n, c]) => c > 1 ? `${n} ×${c}` : n).join(", ");
  const last = first + items.length - 1;
  const shown = userOpen === null && auto ? runs.filter(r => r.status !== "done" || r.failed) : runs;
  const ordered = failedRuns.length ? [...shown.filter(r => r.failed), ...shown.filter(r => !r.failed)] : shown;
  const images = [...new Set(items.flatMap(i => {
    const own = new Set(i.models.map(m => m.image_url));
    return (i.result?._image_urls ?? []).filter(u => !own.has(u));
  }))];
  const tally = [running && "running", done > 0 && `${done} ok`, failedCalls > 0 && `${failedCalls} failed`, interrupted && "stopped"].filter(Boolean).join(" · ");

  return (
    <>
      <div className={`step-group${failedRuns.length ? " has-failed" : ""}${running ? " is-running" : ""}`}>
        <button type="button" className="step-head" aria-expanded={open} onClick={() => setUserOpen(!open)}>
          <span className="step-range">{items.length > 1 ? `Steps ${first}-${last}` : `Step ${first}`}</span>
          <span className="step-labels ellipsis">{labels}</span>
          <span className={`step-tally${failedCalls ? " bad" : ""}`}>{tally}</span>
          {interrupted && <span className="step-chip">Stopped</span>}
          <span className="step-chevron" aria-hidden>›</span>
        </button>
        {open && (
          <div className="step-body">
            {ordered.map(r => (
              <ToolCard key={r.last.call.id} call={r.last.call} result={r.last.result} status={r.status} models={r.last.models}
                live={r.last.live} now={now} chatId={chatId}
                attempts={r.items.length} earlier={r.items.slice(0, -1).map(i => i.result).filter((m): m is Message => !!m)} />
            ))}
          </div>
        )}
      </div>
      <RenderStrip urls={images} />
    </>
  );
}
