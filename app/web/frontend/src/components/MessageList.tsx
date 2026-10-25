import { Link } from "react-router-dom";
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
/** An error message whose "Open Settings" becomes a link to the Settings page. */
function ErrorText({ text }: { text: string }) {
  const [before, after] = text.split("Open Settings", 2);
  return after === undefined ? <>{text}</> : <>{before}<Link to="/settings">Open Settings</Link>{after}</>;
}

/** Who answered, in the workspace chat only (`fast` is its marker): the free path or the AI. */
function Badge({ kind }: { kind: "you" | "free" | "ai" }) {
  const label = { you: "You", free: "Free · instant", ai: "AI · costs apply" }[kind];
  return <span className="msg-badge" data-kind={kind}>{label}</span>;
}

export default function MessageList({ detail, running, draft, persisting, tools, workers, now, error, scrollerRef, bottomRef, onScroll, fast }: {
  detail: ChatDetail; running: boolean; draft: string; persisting: string; tools: RunningTool[]; workers: LiveWorker[]; now: number; error: string | null;
  scrollerRef: Ref<HTMLDivElement>; bottomRef: Ref<HTMLDivElement>; onScroll: UIEventHandler<HTMLDivElement>;
  fast?: (m: Message) => ReactNode;   // how the workspace chat draws a reply of the free fast path
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
  // "I used: ..." goes under the last prose answer of a turn whose message carried files; a failed message is marked and its error says which.
  const usedLine = new Map<number, string>();
  const failed = new Set<number>();
  let turnFiles: string[] = [], turnAnswer: number | undefined, turnUser: Message | undefined;
  const endTurn = () => { if (turnAnswer !== undefined && turnFiles.length) usedLine.set(turnAnswer, turnFiles.join(", ")); };
  for (const m of messages) {
    if (m._hidden || m.role === "tool" || m.role === "system") continue;
    if (m.role === "user" && !m._editor_publish) {
      endTurn();
      const images = Array.isArray(m.content) ? (m.content as ContentBlock[]).filter(b => b.type === "image_url").length : 0;
      turnFiles = [...(m._documents ?? []).map(d => d.name), ...Array.from({ length: images }, (_, i) => images > 1 ? `image ${i + 1}` : "image")];
      turnAnswer = undefined; turnUser = m;
    } else if (m._error && turnUser) failed.add(turnUser.id);
    else if (m.role === "assistant" && !m._ui_only && !m._fast_path && text(m)) turnAnswer = m.id;
  }
  endTurn();
  const lastUser = [...messages].reverse().find(m => m.role === "user" && !m._hidden && !m._editor_publish);
  if (error && lastUser) failed.add(lastUser.id);
  const excerpt = (m?: Message) => { const t = m ? text(m).trim() : ""; return t.length > 60 ? `${t.slice(0, 57)}…` : t; };
  const failedBanner = (key: string | number, body: ReactNode, user?: Message) => (
    <div key={key} className="banner error msg-failed">{fast && user && <span className="msg-failed-for">Failed: “{excerpt(user)}”</span>}{body}</div>
  );
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
      if (m._error) {
        const owner = [...messages.slice(0, messages.indexOf(m))].reverse().find(x => x.role === "user" && !x._hidden && !x._editor_publish);
        nodes.push(failedBanner(m.id, <ErrorText text={text(m)} />, owner));
      } else nodes.push(<div key={m.id} className="banner">{text(m)}</div>);
      continue;
    }
    if (m.role === "user") {
      flush();
      nodes.push(
        <div key={m.id} className="msg user" data-failed={failed.has(m.id) || undefined}>
          <div className="bubble">{fast && !m._editor_publish && <Badge kind="you" />}{m._editor_publish ? m._label ?? text(m) : text(m)}
            {Array.isArray(m.content) && <div className="attachments">{(m.content as ContentBlock[]).filter(b => b.type === "image_url").map((b, i) => <img key={i} src={b.image_url?.url} alt={`Attached image ${i + 1}`} />)}</div>}
            {!!m._documents?.length && <div className="document-attachments">{m._documents.map((d, i) =>
              <a className="document-chip" key={i} href={d.url ?? undefined} download={d.name}><DocumentIcon /><span>{d.name}</span></a>
            )}</div>}
          </div>
        </div>
      );
      const published = cards.byMessage.get(m.id) ?? [];
      if (m._editor_publish && (published.length || m.parts_check)) {
        nodes.push(<div key={`cards-${m.id}`} className="msg assistant">{published.map(modelCard)}{m.parts_check && <PartsCheckCard check={m.parts_check} />}</div>);
      }
      continue;
    }
    if (m._fast_path && fast) {
      flush();
      nodes.push(<div key={m.id} className="msg assistant fast-path">{m._fast_path.route !== "needs_ai" && <Badge kind="free" />}{fast(m)}</div>);
      continue;
    }
    if (text(m)) {
      flush();
      nodes.push(<div key={m.id} className="msg assistant">{fast && <Badge kind="ai" />}<Markdown models={publishedModels}>{text(m)}</Markdown>{fast && usedLine.has(m.id) && <p className="msg-used muted small">I used: {usedLine.get(m.id)}</p>}</div>);
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
      {error && !messages.some((m) => m._error && text(m) === error) && failedBanner("live-error", <ErrorText text={error} />, lastUser)}
      <div ref={bottomRef} />
    </div>
  );
}
