import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { Link, useLocation, useParams } from "react-router-dom";
import { api, isPending, type ChatDetail, type TurnOptions, type DocumentUpload } from "../api";
import Composer from "../components/Composer";
import MessageList from "../components/MessageList";
import TurnStatus from "../components/TurnStatus";
import BuildHeader from "../components/BuildHeader";
import { useApp } from "../context";
import { useChatStream } from "../hooks/useChatStream";

export default function ChatPage() {
  const { id = "" } = useParams();
  const { hash } = useLocation();
  const anchored = useRef("");
  const { chats, llms, defaultLlmId, refreshChats } = useApp();
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

  const { running, setRunning, draft, persisting, tools, error, setError, approvals, workers, contextNotice, activity, connected, now, subscribe, reset } =
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

  const latest = Object.values(detail.models).filter(m => m.version != null).sort((a, b) => b.version! - a.version!)[0];
  return (
    <div className="chat-page">
      <header className="chat-head">
        <h2 className="ellipsis">{detail.chat.title}</h2>
        <Link className="chat-edit-link" to={`/chat/${id}/edit`} title="Place and move parts in 3D (desktop)">Edit in 3D</Link>
      </header>
      <BuildHeader detail={detail} chats={chats} running={running} connected={connected} activity={activity} tools={tools}
        workers={workers} approvals={approvals} now={now} onStop={() => api.cancel(id)} />
      <MessageList detail={detail} running={running} draft={draft} persisting={persisting} tools={tools} workers={workers} now={now} error={error}
        scrollerRef={scrollerRef} bottomRef={bottomRef} onScroll={onScroll} />
      <div className="composer-wrap">
        <TurnStatus chatId={id} approvals={approvals} contextNotice={contextNotice} onError={setError} />
        <Composer
          key={id}
          initialOptions={detail.chat.options}
          continuingFrom={latest ? `v${latest.version}${latest.title ? `: ${latest.title}` : ""}` : undefined}
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
