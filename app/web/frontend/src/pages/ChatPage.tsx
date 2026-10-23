import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { useLocation, useParams } from "react-router-dom";
import { api, isPending, type ChatDetail, type TurnOptions, type DocumentUpload } from "../api";
import Composer from "../components/Composer";
import MessageList from "../components/MessageList";
import TurnStatus from "../components/TurnStatus";
import UsageBadge from "../components/UsageBadge";
import { useApp } from "../context";
import { useChatStream } from "../hooks/useChatStream";

export default function ChatPage() {
  const { id = "" } = useParams();
  const { hash } = useLocation();
  const anchored = useRef("");
  const { llms, defaultLlmId, refreshChats } = useApp();
  const [detail, setDetail] = useState<ChatDetail | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [llmId, setLlmId] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const scrollerRef = useRef<HTMLDivElement>(null);
  const stickToBottom = useRef(true);

  const reload = useCallback(async () => {
    const d = await api.chat(id);
    setDetail(d);
    return d;
  }, [id]);

  const { running, setRunning, draft, persisting, tools, error, setError, approvals, contextNotice, activity, connected, now, subscribe, reset } =
    useChatStream(id, reload, refreshChats);

  useEffect(() => {
    setDetail(null);
    setNotFound(false);
    reset();
    stickToBottom.current = true;
    reload()
      .then((d) => {
        setLlmId(d.chat.llm_model_id);
        if (d.chat.running) subscribe();
        else setRunning(false);
      })
      .catch(() => setNotFound(true));
  }, [id, reload, subscribe, reset, setRunning]);

  // Snapshots/BOMs of this chat's models still being made (e.g. a deleted .png): refetch until done.
  const snapshotsPending = !running && !!detail && Object.values(detail.models).some(isPending);
  useEffect(() => {
    if (!snapshotsPending) return;
    const timer = setTimeout(() => reload().then(() => refreshChats()), 1500);
    return () => clearTimeout(timer);
  }, [snapshotsPending, detail, reload, refreshChats]);

  // Fall back to the default model when the chat's model was deleted.
  useEffect(() => {
    if (llms.length && (!llmId || !llms.some((m) => m.id === llmId))) setLlmId(defaultLlmId ?? llms[0].id);
  }, [llms, llmId, defaultLlmId]);

  useLayoutEffect(() => {
    if (stickToBottom.current) bottomRef.current?.scrollIntoView({ block: "end" });
  }, [detail, draft, persisting, tools]);

  useEffect(() => {
    if (!hash.startsWith("#model-") || anchored.current === id + hash) return;
    const card = document.getElementById(hash.slice(1));
    if (card) {
      stickToBottom.current = false;
      card.scrollIntoView({ block: "center" });
      anchored.current = id + hash;
    }
  }, [id, hash, detail]);

  function onScroll() {
    const el = scrollerRef.current;
    if (el) stickToBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
  }

  async function send(value: string, options: TurnOptions, images: string[], documents: DocumentUpload[]) {
    setError(null);
    stickToBottom.current = true;
    try {
      await api.send(id, value, llmId, options, images, documents);
    } catch (e) {
      setError((e as Error).message);
      throw e;
    }
    await reload();
    subscribe();
    refreshChats();
  }

  if (notFound) return <div className="page"><p className="muted">This chat doesn't exist (any more).</p></div>;
  if (!detail) return <div className="page"><p className="muted">Loading…</p></div>;

  return (
    <div className="chat-page">
      <header className="chat-head">
        <h2 className="ellipsis">{detail.chat.title}</h2>
        {!!detail.usage?.steps && <UsageBadge usage={detail.usage} steps={detail.usage.steps} budget={detail.chat.options?.budget_eur} />}
      </header>
      <MessageList detail={detail} running={running} draft={draft} persisting={persisting} tools={tools} now={now} error={error}
        scrollerRef={scrollerRef} bottomRef={bottomRef} onScroll={onScroll} />
      <div className="composer-wrap">
        <TurnStatus chatId={id} running={running} connected={connected} approvals={approvals} activity={activity} now={now}
          contextNotice={contextNotice} onError={setError} />
        <Composer
          key={id}
          initialOptions={detail.chat.options}
          llmId={llmId}
          onLlmChange={setLlmId}
          onSend={send}
          onStop={() => api.cancel(id)}
          running={running}
        />
      </div>
    </div>
  );
}
