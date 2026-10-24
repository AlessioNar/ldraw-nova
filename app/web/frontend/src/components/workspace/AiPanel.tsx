import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, buildsApi, type ChatDetail, type DocumentUpload, type TurnOptions } from "../../api";
import { useApp } from "../../context";
import { useChatStream } from "../../hooks/useChatStream";
import type { Proposal } from "../../workspace/types";
import type { Workspace } from "../../workspace/useWorkspace";
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

/** The chat attached to this build, inline: messages, composer, and the proposals the AI has made. */
export default function AiPanel({ ws }: { ws: Workspace }) {
  const { id } = ws;
  const { llms, defaultLlmId, refreshChats } = useApp();
  const [chatId, setChatId] = useState<string | null>(ws.ws?.chat_id ?? null);
  const [detail, setDetail] = useState<ChatDetail | null>(null);
  const [llmId, setLlmId] = useState<string | null>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  useEffect(() => setChatId((c) => c ?? ws.ws?.chat_id ?? null), [ws.ws?.chat_id]);

  const reload = useCallback(async () => {
    if (!chatId) return null;
    const d = await api.chat(chatId);
    setDetail(d);
    return d;
  }, [chatId]);
  const { running, setRunning, draft, persisting, tools, error, setError, approvals, workers, contextNotice, now, subscribe, reset } =
    useChatStream(chatId ?? "", reload, useCallback(() => { refreshChats(); ws.reloadAll().catch(() => {}); }, [refreshChats, ws.reloadAll]));  // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    reset(); setDetail(null); stick.current = true;
    if (!chatId) return;
    reload().then((d) => { if (d) { setLlmId(d.chat.llm_model_id); if (d.chat.running) subscribe(); else setRunning(false); } }).catch((e: Error) => setError(e.message));
  }, [chatId, reload, subscribe, reset, setRunning, setError]);
  useEffect(() => { if (llms.length && (!llmId || !llms.some((m) => m.id === llmId))) setLlmId(defaultLlmId ?? llms[0].id); }, [llms, llmId, defaultLlmId]);
  useLayoutEffect(() => { if (stick.current && scroller.current) scroller.current.scrollTop = scroller.current.scrollHeight; }, [detail, draft, persisting, tools]);

  async function send(text: string, options: TurnOptions, images: string[], documents: DocumentUpload[]) {
    setError(null); stick.current = true;
    try {
      let target = chatId;
      if (!target) { const c = await buildsApi.chat(id, llmId, false); target = c.id; setChatId(c.id); }
      await api.send(target, text, llmId, options, images, documents);
      if (target === chatId) { await reload(); subscribe(); }   // a fresh chat is picked up by the effect above
      refreshChats();
    } catch (e) { setError((e as Error).message); throw e; }
  }

  const cost = detail?.usage?.cost_eur;
  const mine = ws.proposals;
  return (
    <section className="ws-panel ws-ai" role="tabpanel" aria-label="AI">
      <div className="ws-chat-head">
        <span className="label">Chat for this build</span>
        {chatId ? <Link className="small" to={`/chat/${chatId}`}>Open full chat</Link> : <span className="muted small">starts with your first message</span>}
      </div>
      {mine.map((p) => <ProposalCard key={p.id} p={p} ws={ws} />)}
      <div className="ws-chat">
        {detail
          ? <MessageList detail={detail} running={running} draft={draft} persisting={persisting} tools={tools} workers={workers} now={now} error={error} scrollerRef={scroller} bottomRef={bottom}
              onScroll={() => { const el = scroller.current; if (el) stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80; }} />
          : <div className="ws-chat-empty muted small">
              {chatId ? "Loading the chat…" : <><p>Ask the AI for something the generator and the Describe bar cannot do, like a garden fence or a custom detail.</p><p>What it builds arrives as a proposal you review before it joins the draft.</p></>}
              {!chatId && error && <div className="banner error">{error}</div>}
              <div ref={bottom} />
            </div>}
      </div>
      <div className="ws-composer">
        {cost != null && cost > 0 && <p className="muted small ws-cost">Spent in this chat: about €{cost.toFixed(2)}</p>}
        <TurnStatus chatId={chatId ?? ""} approvals={approvals} contextNotice={contextNotice} onError={setError} />
        <Composer key={`${chatId ?? "new"}-${ws.aiSeed?.n ?? 0}`} initialText={ws.aiSeed?.text ?? ""} initialOptions={detail?.chat.options}
          llmId={llmId} onLlmChange={setLlmId} onSend={send} onStop={() => chatId && api.cancel(chatId)} running={running} />
        <p className="muted small ws-note">Simple edits (colours, sizes, a chimney) never need this: use the Describe bar below, it is free.</p>
      </div>
    </section>
  );
}
