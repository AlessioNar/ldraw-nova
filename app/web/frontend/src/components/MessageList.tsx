import { useEffect, useRef, type ReactNode, type Ref, type UIEventHandler } from "react";
import type { ChatDetail, ChatModel, LiveWorker, Message } from "../api";
import { placeChatModels } from "../chatModels";
import type { RunningTool } from "../hooks/useChatStream";
import DocumentIcon from "./DocumentIcon";
import Markdown from "./Markdown";
import ModelCard from "./ModelCard";
import PartsCheckCard from "./PartsCheckCard";
import StepGroup, { type StepItem } from "./StepGroup";
import ToolCard, { type Status } from "./ToolCard";
import WorkerGroup from "./WorkerGroup";

type Pending = StepItem;

type ContentBlock = { type: string; text?: string; image_url?: { url: string } };
const text = (m: Message) => typeof m.content === "string" ? m.content : Array.isArray(m.content) ? (m.content as ContentBlock[]).filter(b => b.type === "text").map(b => b.text).join("\n") : "";

/** The transcript: saved messages with their tool calls and model cards, then the in-flight reply. */
export default function MessageList({ detail, running, draft, persisting, tools, workers, now, error, scrollerRef, bottomRef, onScroll }: {
  detail: ChatDetail; running: boolean; draft: string; persisting: string; tools: RunningTool[]; workers: LiveWorker[]; now: number; error: string | null;
  scrollerRef: Ref<HTMLDivElement>; bottomRef: Ref<HTMLDivElement>; onScroll: UIEventHandler<HTMLDivElement>;
}) {
  const messages = detail.messages;
  const results = new Map(messages.filter((m) => m.role === "tool").map((m) => [m.tool_call_id, m]));
  const runningIds = new Set(tools.map((t) => t.id));
  const modelsFor = (m?: Message): ChatModel[] => (m?._models ?? []).map((id) => detail.models[id]).filter(Boolean);
  // A live worker belongs to the first unanswered delegate call with the same task; a saved result carries its own id.
  const claimed = new Set(messages.filter(m => m._worker).map(m => m._worker!.id));
  const workerFor = new Map<string, LiveWorker>();
  for (const m of messages) for (const call of m.tool_calls ?? []) {
    if (call.function.name !== "delegate") continue;
    const saved = results.get(call.id)?._worker;
    if (saved) { const w = workers.find(x => x.id === saved.id); if (w) workerFor.set(call.id, w); continue; }
    let task = "";
    try { task = String(JSON.parse(call.function.arguments || "{}").task ?? ""); } catch { /* shown as a bare card */ }
    const w = workers.find(x => !claimed.has(x.id) && task.trim().startsWith(x.task.trim()));
    if (w) { claimed.add(w.id); workerFor.set(call.id, w); }
  }
  const idle = running && !draft && !persisting && tools.length === 0;
  const cards = placeChatModels(messages, detail.models);
  const publishedModels = Object.values(detail.models);
  const modelCard = (m: ChatModel) => <div id={`model-${m.id}`} className="chat-model" key={m.id}><ModelCard model={m} /></div>;

  // Consecutive tool calls between two prose messages are collected and rendered as step groups (and one workers group per delegate batch).
  const nodes: ReactNode[] = [];
  let pending: Pending[] = [];
  let stepNo = 1;
  const flush = () => {
    if (!pending.length) return;
    const blocks: ReactNode[] = [];
    const kindOf = (p: Pending) => p.call.function.name === "delegate" ? "worker" : p.call.function.name === "report_progress" ? "progress" : "step";
    for (let i = 0; i < pending.length;) {
      const kind = kindOf(pending[i]);
      let j = i;
      while (j < pending.length && kindOf(pending[j]) === kind && (kind !== "progress" || j === i)) j++;
      const run = pending.slice(i, j);
      const key = run[0].call.id;
      if (kind === "worker") {
        blocks.push(<WorkerGroup key={key} chatId={detail.chat.id} eurPerUsd={detail.usage?.eur_per_usd}
          items={run.map(p => ({ call: p.call, result: p.result, status: p.status, worker: workerFor.get(p.call.id) }))} />);
      } else if (kind === "progress") {
        blocks.push(<ToolCard key={key} call={run[0].call} result={run[0].result} status={run[0].status} models={[]} />);
      } else {
        blocks.push(<StepGroup key={key} items={run} first={stepNo} now={now} chatId={detail.chat.id} />);
        stepNo += run.length;
      }
      i = j;
    }
    nodes.push(<div key={`steps-${pending[0].call.id}`} className="msg assistant transcript-steps">{blocks}</div>);
    pending = [];
  };
  for (const m of messages) {
    if (m._hidden || m.role === "tool" || m.role === "system") continue;
    if (m._ui_only) {
      flush();
      nodes.push(<div key={m.id} className={`banner ${m._error ? "error" : ""}`}>{text(m)}</div>);
      continue;
    }
    if (m.role === "user") {
      flush();
      nodes.push(
        <div key={m.id} className="msg user">
          <div className="bubble">{text(m)}
            {Array.isArray(m.content) && <div className="attachments">{(m.content as ContentBlock[]).filter(b => b.type === "image_url").map((b, i) => <img key={i} src={b.image_url?.url} alt={`Attached image ${i + 1}`} />)}</div>}
            {!!m._documents?.length && <div className="document-attachments">{m._documents.map((d, i) =>
              <a className="document-chip" key={i} href={d.url ?? undefined} download={d.name}><DocumentIcon /><span>{d.name}</span></a>
            )}</div>}
          </div>
        </div>
      );
      continue;
    }
    if (text(m)) {
      flush();
      nodes.push(<div key={m.id} className="msg assistant"><Markdown models={publishedModels}>{text(m)}</Markdown></div>);
    }
    for (const call of m.tool_calls ?? []) {
      const result = results.get(call.id);
      const status: Status = result ? "done" : runningIds.has(call.id) ? "running" : running ? "queued" : "interrupted";
      pending.push({ call, result, status, models: modelsFor(result), live: tools.find(t => t.id === call.id) });
    }
    const placed = cards.byMessage.get(m.id) ?? [];
    if (placed.length || m.parts_check) {
      flush();
      nodes.push(<div key={`cards-${m.id}`} className="msg assistant">{placed.map(modelCard)}{m.parts_check && <PartsCheckCard check={m.parts_check} />}</div>);
    }
  }
  flush();

  // A finished chat opens on its result, unless the URL already points at a model (ChatPage handles that).
  const scrolledTo = useRef("");
  useEffect(() => {
    if (scrolledTo.current === detail.chat.id) return;
    scrolledTo.current = detail.chat.id;
    if (running || window.location.hash.startsWith("#model-")) return;
    const all = [...cards.byMessage.values()].flat();
    const target = all[all.length - 1] ?? cards.unplaced[cards.unplaced.length - 1];
    if (target) document.getElementById(`model-${target.id}`)?.scrollIntoView({ block: "center" });
  });

  return (
    <div className="messages" ref={scrollerRef} onScroll={onScroll}>
      {nodes}
      {cards.unplaced.map(m => (
        <div className="msg assistant" key={m.id}>{modelCard(m)}</div>
      ))}
      {(persisting || draft) && (
        <div className="msg assistant">
          <Markdown models={publishedModels}>{persisting || draft}</Markdown>
        </div>
      )}
      {idle && (
        <div className="msg assistant">
          <span className="thinking" aria-label="Thinking">
            <i />
            <i />
            <i />
          </span>
        </div>
      )}
      {error && !messages.some((m) => m._error && text(m) === error) && <div className="banner error">{error}</div>}
      <div ref={bottomRef} />
    </div>
  );
}
