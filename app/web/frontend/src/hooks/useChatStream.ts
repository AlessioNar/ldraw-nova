import { useCallback, useEffect, useRef, useState } from "react";
import type { Approval, LiveWorker } from "../api";

export type RunningTool = { id: string; name: string; arguments: string; output?: string; started_at?: number };
export type Activity = { started_at: number; last_event_at: number; phase: string };

/** Live state of a chat's running turn, fed by its server-sent event stream.
 * The DB stays the source of truth: every "saved" event triggers `reload`; the stream only carries in-flight text/tools. */
export function useChatStream(id: string, reload: () => Promise<unknown>, refreshChats: () => void) {
  const [running, setRunning] = useState(false);
  const [draft, setDraft] = useState("");
  const [persisting, setPersisting] = useState("");
  const [tools, setTools] = useState<RunningTool[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [workers, setWorkers] = useState<LiveWorker[]>([]);
  const [contextNotice, setContextNotice] = useState("");
  const [activity, setActivity] = useState<Activity | null>(null);
  const [connected, setConnected] = useState(true);
  const [now, setNow] = useState(Date.now());
  const draftRef = useRef("");
  const sourceRef = useRef<EventSource | null>(null);

  const updateDraft = (value: string) => {
    draftRef.current = value;
    setDraft(value);
  };

  const subscribe = useCallback(() => {
    sourceRef.current?.close();
    const es = new EventSource(`/api/chats/${id}/stream`);
    sourceRef.current = es;
    setRunning(true);
    const data = (e: Event) => JSON.parse((e as MessageEvent).data);
    es.onopen = () => setConnected(true);
    es.onerror = () => setConnected(false);
    es.addEventListener("reconnect", () => { es.close(); subscribe(); });

    es.addEventListener("snapshot", (e) => {
      const d = data(e);
      if (!d.running) {
        es.close();
        setRunning(false);
        reload();
        return;
      }
      updateDraft(d.draft);
      setTools(d.tools);
      setApprovals(d.approvals ?? []);
      setWorkers(d.workers ?? []);
      setActivity(d.activity ?? null);
    });
    es.addEventListener("activity", e => setActivity(data(e)));
    es.addEventListener("progress", e => setActivity(a => ({
      started_at: a?.started_at ?? Date.now() / 1000,
      last_event_at: Date.now() / 1000, phase: data(e).summary,
    })));
    es.addEventListener("tool_output", e => {
      const d = data(e);
      setTools(all => all.map(t => t.id === d.id ? { ...t, output: ((t.output ?? "") + d.delta).slice(-12000) } : t));
    });
    es.addEventListener("worker_start", e => setWorkers(all => [...all.filter(w => w.id !== data(e).id), { status: "running", steps: 0, eur: 0, ...data(e) }]));
    es.addEventListener("worker_step", e => setWorkers(all => all.map(w => w.id === data(e).id ? { ...w, steps: data(e).steps, eur: data(e).eur } : w)));
    es.addEventListener("worker_end", e => setWorkers(all => all.map(w => w.id === data(e).id ? { ...w, status: data(e).status, steps: data(e).steps ?? w.steps, eur: data(e).eur } : w)));
    es.addEventListener("text", (e) => updateDraft(draftRef.current + data(e).delta));
    es.addEventListener("tool_start", (e) => {
      const t = data(e) as RunningTool;
      setTools((all) => [...all.filter((x) => x.id !== t.id), t]);
    });
    es.addEventListener("tool_end", (e) => setTools((all) => all.filter((x) => x.id !== data(e).id)));
    es.addEventListener("saved", () => {
      // Keep showing the finished text until the refetch brings the saved message.
      setPersisting(draftRef.current);
      updateDraft("");
      reload().then(() => setPersisting(""));
    });
    es.addEventListener("model", () => { reload(); refreshChats(); });
    es.addEventListener("turn_error", (e) => setError(data(e).message));
    es.addEventListener("approval", e => setApprovals(all => [...all.filter(a => a.id !== data(e).id), data(e)]));
    es.addEventListener("approval_resolved", e => setApprovals(all => all.filter(a => a.id !== data(e).id)));
    es.addEventListener("context", () => setContextNotice("Earlier conversation or completed tool steps were omitted to fit the context budget. Saved history and build files are unchanged."));
    es.addEventListener("done", () => {
      es.close();
      setRunning(false);
      setTools([]);
      setApprovals([]);
      setWorkers([]);
      updateDraft("");
      reload();
      refreshChats();
    });
  }, [id, reload, refreshChats]);

  /** Forget the previous chat's turn state (the caller reloads and resubscribes). */
  const reset = useCallback(() => {
    setError(null);
    setTools([]);
    setApprovals([]);
    setWorkers([]);
    setContextNotice("");
    setActivity(null);
    setConnected(true);
    draftRef.current = "";
    setDraft("");
    setPersisting("");
  }, []);

  useEffect(() => () => sourceRef.current?.close(), [id]);

  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [running]);

  return { running, setRunning, draft, persisting, tools, error, setError, approvals, workers, contextNotice, activity, connected, now, subscribe, reset };
}
