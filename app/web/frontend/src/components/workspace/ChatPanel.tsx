import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { useLocation } from "react-router-dom";
import { api, buildsApi, estimateLabel, type ChatDetail, type DescribeHint, type DocumentUpload, type Message, type TurnOptions } from "../../api";
import { useApp } from "../../context";
import { useChatStream } from "../../hooks/useChatStream";
import type { Proposal } from "../../workspace/types";
import type { Workspace } from "../../workspace/useWorkspace";
import { wsApi } from "../../workspace/wsApi";
import Composer from "../Composer";
import MessageList from "../MessageList";
import TurnStatus from "../TurnStatus";

function ProposalCard({ p, ws }: { p: Proposal; ws: Workspace }) {
  const previewing = ws.viewing?.kind === "proposal" && ws.viewing.id === p.id;
  const { added, removed, moved } = p.summary;
  const busy = ws.busy === "proposal";
  return (
    <div className={`ws-ai-card${previewing ? " active" : ""}`}>
      <div className="label">Review before it joins the draft</div>
      <div className="mono ws-diffline"><span className="ok-text">+ {added} part{added === 1 ? "" : "s"}</span> <span className="muted">· {removed} removed · {moved} moved</span></div>
      <div className="ws-ai-actions">
        <button className="primary" disabled={busy} onClick={() => ws.keepProposal(p)}>Keep in draft</button>
        <button disabled={busy} aria-pressed={previewing} onClick={() => (previewing ? ws.backToDraft() : ws.previewProposal(p))}>{previewing ? "Stop preview" : "Preview"}</button>
        <button disabled={busy} onClick={() => ws.dropProposal(p)}>Discard</button>
      </div>
      <span className="ws-meta mono">proposed · AI{p.cost_eur != null ? ` · ${p.cost_eur.toFixed(2)} EUR` : ""}</span>
    </div>
  );
}

const DEFAULT_OPTIONS = { mode: "agent", permissions: "ask" } as TurnOptions;
const seconds = (ms: number) => (ms < 950 ? `${Math.max(ms / 1000, 0.1).toFixed(1)} s` : `${Math.round(ms / 1000)} s`);
const show = (v: unknown) => (typeof v === "object" && v !== null ? JSON.stringify(v) : String(v));

/** The one chat of a build. Every message first tries the free fast path (applied to the draft at once, with Undo);
 *  a vague one gets a single question with quick replies; one only the AI can do shows its cost and waits for "Ask the AI".
 *  While the AI is engaged (the server decides, from the saved chat) messages go to it instead, unless the person flips one.
 *  The AI then works in the same conversation and its result arrives as a proposal card. */
export default function ChatPanel({ ws }: { ws: Workspace }) {
  const { id } = ws;
  const { llms, defaultLlmId, refreshChats } = useApp();
  const [chatId, setChatId] = useState<string | null>(ws.ws?.chat_id ?? null);
  const [detail, setDetail] = useState<ChatDetail | null>(null);
  const [llmId, setLlmId] = useState<string | null>(null);
  const [hint, setHint] = useState<DescribeHint | null>(null);
  const [typed, setTyped] = useState(false);
  const [acting, setActing] = useState(false);
  const [mode, setMode] = useState<"ai" | "quick">(ws.ws?.chat_mode ?? "quick");   // decided by the server, from the saved chat
  const [flip, setFlip] = useState(false);                                          // this one message goes the other way
  const scroller = useRef<HTMLDivElement>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  const options = useRef<TurnOptions>(DEFAULT_OPTIONS);
  const hintTimer = useRef<ReturnType<typeof setTimeout>>(undefined);
  const generator = ws.ws?.generator?.id;
  useEffect(() => setChatId((c) => c ?? ws.ws?.chat_id ?? null), [ws.ws?.chat_id]);
  useEffect(() => setMode(ws.ws?.chat_mode ?? "quick"), [ws.ws?.chat_mode]);
  useEffect(() => () => clearTimeout(hintTimer.current), []);

  const reload = useCallback(async () => {
    if (!chatId) return null;
    const d = await api.chat(chatId);
    setDetail(d);
    return d;
  }, [chatId]);
  const prompt = (useLocation().state as { prompt?: string } | null)?.prompt;   // handed over by /new
  const { running, setRunning, draft, persisting, tools, error, setError, approvals, workers, contextNotice, now, subscribe, reset } =
    useChatStream(chatId ?? "", reload, useCallback(() => { refreshChats(); ws.reloadAll().catch(() => {}); }, [refreshChats, ws.reloadAll]));  // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    reset(); setDetail(null); stick.current = true;
    if (!chatId) return;
    reload().then((d) => {
      if (!d) return;
      setLlmId(d.chat.llm_model_id);
      if (d.chat.options?.mode) options.current = d.chat.options;
      if (d.chat.running) subscribe(); else setRunning(false);
    }).catch((e: Error) => setError(e.message));
  }, [chatId, reload, subscribe, reset, setRunning, setError]);
  useEffect(() => { if (llms.length && (!llmId || !llms.some((m) => m.id === llmId))) setLlmId(defaultLlmId ?? llms[0].id); }, [llms, llmId, defaultLlmId]);
  useLayoutEffect(() => { if (stick.current && scroller.current) scroller.current.scrollTop = scroller.current.scrollHeight; }, [detail, draft, persisting, tools]);

  // The live hint under the composer: rules only, a millisecond, never a paid call.
  const onTextChange = useCallback((text: string) => {
    clearTimeout(hintTimer.current);
    setHint(null);
    const long = text.trim().length >= 3;
    setTyped(long);
    if (long) hintTimer.current = setTimeout(() => buildsApi.hint(text, generator, id).then(setHint).catch(() => {}), 350);
  }, [generator, id]);

  const messages = detail?.messages ?? [];
  const lastId = messages.filter((m) => !m._hidden && m.role !== "tool").at(-1)?.id;
  const lastUser = [...messages].reverse().find((m) => m.role === "user" && typeof m.content === "string")?.content as string | undefined;

  async function toAgent(target: string, text: string, opts: TurnOptions, images: string[], documents: DocumentUpload[]) {
    await api.send(target, text, llmId, opts, images, documents);
    if (target === chatId) { await reload(); subscribe(); }   // a fresh chat is picked up by the effect above
    refreshChats();
  }

  async function viaAgent(text: string, opts: TurnOptions, images: string[], documents: DocumentUpload[]) {
    if (!llmId) throw new Error("Add a model in Settings first: the AI needs one.");
    const target = chatId ?? (await buildsApi.chat(id, llmId, false)).id;
    if (!chatId) setChatId(target);
    setMode("ai");
    await toAgent(target, text, opts, images, documents);
  }

  async function send(text: string, opts: TurnOptions, images: string[], documents: DocumentUpload[]) {
    setError(null); stick.current = true; options.current = opts;
    const toAi = mode === "ai" ? !flip : flip;               // the person's choice for this message, else the server's mode
    setFlip(false);
    try {
      if (images.length || documents.length || toAi) return await viaAgent(text, opts, images, documents);   // attachments are for the AI
      if (ws.viewing) ws.backToDraft();
      const r = await ws.describe(text, lastUser, mode === "ai" && !toAi);
      if (r.route === "agent") return await viaAgent(text, opts, images, documents);    // the server says the AI is engaged
      if (r.chat_mode) setMode(r.chat_mode);
      if (r.chat_id && r.chat_id !== chatId) setChatId(r.chat_id); else await reload();
      refreshChats();
    } catch (e) { setError((e as Error).message); throw e; }
  }

  async function guarded(work: () => Promise<unknown>) {
    setActing(true); setError(null);
    try { await work(); } catch (e) { setError((e as Error).message); } finally { setActing(false); }
  }
  const askAi = () => guarded(async () => {
    if (!chatId || !llmId) throw new Error("Add a model in Settings first: the AI needs one.");
    stick.current = true; setMode("ai");
    await toAgent(chatId, "Yes, ask the AI to do this.", options.current, [], []);
  });
  const reply = (text: string) => (text === "Ask the AI" ? askAi() : guarded(() => send(text, options.current, [], []).catch(() => undefined)));
  const note = (text: string) => guarded(async () => { await wsApi.note(id, text); await reload(); });
  const undoStep = (summary: string) => guarded(async () => { ws.undo(); await wsApi.note(id, `Undone: ${summary}`); await reload(); });

  const fast = (m: Message): ReactNode => {
    const f = m._fast_path!;
    const live = m.id === lastId && !running && !acting;   // only the latest answer may still be undone
    if (f.route === "generator" || f.route === "edit") {
      const summary = f.history ? "Went back one step"
        : f.route === "generator" ? `Built ${f.title ?? f.generator} from the generator`
        : (f.changes ?? []).map((c) => `${c.label}: ${show(c.from)} → ${show(c.to)}`).join("; ") || "No visible change";
      const canUndo = live && !ws.viewing && (ws.draft?.undo_depth ?? 0) > 0 && !ws.busy;
      return (
        <div className="ws-fp" data-route={f.route}>
          <div className="ws-fp-line"><span>{summary}</span> <span className="ws-meta mono">free · {seconds(f.took_ms)}</span>
            {canUndo && <button className="ws-fp-undo" onClick={() => undoStep(summary)}>Undo</button>}</div>
          {!!f.notes?.length && <ul className="ws-fp-notes">{f.notes.map((n, i) => <li key={i}>{n}</li>)}</ul>}
        </div>
      );
    }
    if (f.route === "clarify") {
      return (
        <div className="ws-fp" data-route="clarify">
          <div className="ws-fp-line"><span>{f.question}</span></div>
          {!!f.notes?.length && <ul className="ws-fp-notes">{f.notes.map((n, i) => <li key={i}>{n}</li>)}</ul>}
        </div>
      );
    }
    const est = f.estimate;
    const minutes = est?.minutes_low != null ? `about ${est.minutes_low}–${est.minutes_high ?? est.minutes_low} min` : null;
    const what = [estimateLabel(est), minutes].filter(Boolean).join(", ");
    return (
      <div className="ws-ai-card ws-fp-ai past" data-route="needs_ai">
        <div className="ws-fp-title">This needs the AI{what ? `: ${what}` : ""}</div>
        {f.reason && <div className="muted small">{f.reason}. Nothing is spent until you ask.</div>}
      </div>
    );
  };

  // What waits for the person lives in one tray above the composer; the transcript keeps the question as plain text.
  const waiting = messages.find((m) => m.id === lastId && m._fast_path && (m._fast_path.route === "clarify" || m._fast_path.route === "needs_ai"));
  const tray = waiting && !running && !acting ? (() => {
    const f = waiting._fast_path!;
    if (f.route === "needs_ai") return (
      <div className="ws-tray" role="region" aria-label="Needs you">
        <div className="label">Needs you</div>
        <div className="ws-ai-actions">
          <button className="primary" onClick={askAi}>Ask the AI</button>
          <button onClick={() => note("Cancelled. Nothing was spent.")}>Cancel</button>
        </div>
      </div>
    );
    const choices = f.choices ?? [];
    if (!choices.length && !f.highlight?.length) return null;
    return (
      <div className="ws-tray" role="region" aria-label="Needs you">
        <div className="label">Needs you</div>
        <div className="ws-chips" role="group" aria-label="Quick replies">
          {!!f.highlight?.length && <button className="ws-chip-btn" onClick={() => ws.showParts(f.highlight!)}>Show them</button>}
          {choices.map((c) => <button key={c} className="ws-chip-btn" onClick={() => reply(c)}>{c}</button>)}
          {choices.some((c) => c !== "Ask the AI") && <button className="ws-chip-btn pick" onClick={() => reply(choices.find((c) => c !== "Ask the AI")!)}>Just pick for me</button>}
        </div>
      </div>
    );
  })() : null;

  const cost = detail?.usage?.cost_eur;
  const toAi = mode === "ai" ? !flip : flip;
  const toggle = <button type="button" className="ws-mode-toggle" onClick={() => setFlip((f) => !f)} aria-pressed={flip}>{toAi ? "Quick change instead" : "Ask the AI instead"}</button>;
  let line: ReactNode = "Simple changes are free and instant. Bigger ones show their cost first.";
  if (toAi) line = <><b className="cost" data-mode="ai">Talking to the AI · costs apply</b> {toggle}</>;
  else if (mode === "ai" || (ws.ws?.agent_chat && !typed)) line = <><b data-mode="quick">Quick changes · free</b> {toggle}</>;
  else if (typed && hint) {
    if (hint.route === "needs_ai") line = <><b className="cost">AI{estimateLabel(hint.estimate) ? `, ${estimateLabel(hint.estimate)}` : ""}</b>. It asks before it spends.</>;
    else if (hint.route === "clarify") line = "Free. I may ask one question first.";
    else line = <b>Free, about 1 s</b>;
    if (!toAi) line = <><span data-mode="quick">{line}</span> {toggle}</>;
  } else if (!toAi) line = <><b data-mode="quick">Quick changes · free</b> {toggle}</>;
  return (
    <section className="ws-panel ws-ai" role="tabpanel" aria-label="Chat">
      {ws.proposals.map((p) => <ProposalCard key={p.id} p={p} ws={ws} />)}
      <div className="ws-chat">
        {detail
          ? <MessageList detail={detail} running={running} draft={draft} persisting={persisting} tools={tools} workers={workers} now={now} error={error} scrollerRef={scroller} bottomRef={bottom} fast={fast}
              onScroll={() => { const el = scroller.current; if (el) stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80; }} />
          : <div className="ws-chat-empty muted small">
              {chatId ? "Loading the chat…" : <><p>Say what you want, for example <em>make the roof blue</em>.</p><p>Simple changes are free and happen at once, with an Undo. Bigger ones show their cost first, and the AI only starts when you say so.</p></>}
              {!chatId && error && <div className="banner error">{error}</div>}
              <div ref={bottom} />
            </div>}
      </div>
      <div className="ws-composer">
        {cost != null && cost > 0 && <p className="muted small ws-cost">Spent in this chat: about €{cost.toFixed(2)}</p>}
        {tray}
        <TurnStatus chatId={chatId ?? ""} approvals={approvals} contextNotice={contextNotice} onError={setError} />
        <Composer key={chatId ?? "new"} initialText={prompt} initialOptions={detail?.chat.options} allowNoModel placeholder={toAi ? "Tell the AI what to do" : "Describe a change, e.g. make the roof blue"}
          onTextChange={onTextChange} onOptions={(o) => { options.current = o; }} hint={<span className="ws-hint-line" role="status" aria-live="polite">{line}</span>}
          llmId={llmId} onLlmChange={setLlmId} onSend={send} onStop={() => chatId && api.cancel(chatId)} running={running} />
      </div>
    </section>
  );
}
