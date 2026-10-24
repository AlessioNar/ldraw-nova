// The workspace's state and actions in one place: build + draft + versions from the W1 endpoints, selection and edit
// counters from the W2 canvas messages, and every button's behaviour. Components only render and call these.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { buildsApi, type BuildDetail, type BuildVersion } from "../api";
import { useApp } from "../context";
import { useMediaQuery } from "../hooks/useMediaQuery";
import { wsApi } from "./wsApi";
import type { CanvasCommand, CanvasEvent, CanvasMode, CanvasPiece, DescribeResult, DraftInfo, PartsCheckData, Proposal, Tool, WorkspaceData, WsVersion } from "./types";

export type Tab = "chat" | "parts" | "piece" | "settings";
export type Viewing = { kind: "version"; version: number } | { kind: "proposal"; id: string } | null;
export type VersionRow = WsVersion & { detail?: BuildVersion };
export type Toast = { text: string; error?: boolean; action?: { label: string; run: () => void } };
export type Confirm = { title: string; body: string; confirmLabel: string; danger?: boolean; onConfirm: () => void };

export const PHONE = "(max-width: 759px)";

export function useWorkspace(id: string) {
  const { refreshBuilds } = useApp();
  const phone = useMediaQuery(PHONE);
  const [detail, setDetail] = useState<BuildDetail | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [ws, setWs] = useState<WorkspaceData | null>(null);
  const [draft, setDraftState] = useState<DraftInfo | null>(null);
  const [check, setCheck] = useState<PartsCheckData | null>(null);
  const [tab, setTabState] = useState<Tab>("chat");
  const [tool, setToolState] = useState<Tool>("select");
  const [mode, setModeState] = useState<CanvasMode>("edit");
  const [colour, setColour] = useState(4);                       // colour for new bricks and the paint tool
  const [selection, setSelection] = useState<CanvasPiece[]>([]);
  const [viewing, setViewing] = useState<Viewing>(null);
  const [editTick, setEditTick] = useState(0);                   // bumps on every canvas edit: live counts refetch
  const [busy, setBusy] = useState<string | null>(null);
  const [toast, setToast] = useState<Toast | null>(null);
  const [confirm, setConfirm] = useState<Confirm | null>(null);
  const [sheetOpen, setSheetOpen] = useState(false);
  const [canvasReady, setCanvasReady] = useState(false);
  const [steps, setSteps] = useState<{ total: number; current: number } | null>(null);

  const sendRef = useRef<(c: CanvasCommand) => void>(() => {});
  const tabRef = useRef<Tab>("chat");
  const prevTab = useRef<Tab>("chat");
  const draftTimer = useRef<ReturnType<typeof setTimeout>>(undefined);
  const saveRef = useRef<() => unknown>(() => {});
  const checkTimer = useRef<ReturnType<typeof setTimeout>>(undefined);
  const toastTimer = useRef<ReturnType<typeof setTimeout>>(undefined);

  const send = useCallback((c: CanvasCommand) => sendRef.current(c), []);
  const registerCanvas = useCallback((fn: (c: CanvasCommand) => void) => { sendRef.current = fn; }, []);

  const say = useCallback((text: string, opts: { error?: boolean; action?: Toast["action"] } = {}) => {
    clearTimeout(toastTimer.current);
    setToast({ text, ...opts });
    toastTimer.current = setTimeout(() => setToast(null), opts.error ? 9000 : 6500);
  }, []);

  const setTab = useCallback((t: Tab, open = true) => {
    if (t !== "piece" && tabRef.current !== t) prevTab.current = t;
    tabRef.current = t; setTabState(t);
    if (open) setSheetOpen(true);
  }, []);

  const setServerDraft = useCallback((d: DraftInfo | null | undefined) => {
    if (d === undefined) return;
    setDraftState(d);
  }, []);

  const refreshCheck = useCallback(() => { wsApi.partsCheck(id).then(setCheck).catch(() => setCheck(null)); }, [id]);

  /** Refetch the build, the workspace and the parts check (after any backend change). */
  const reloadAll = useCallback(async () => {
    const [d, w] = await Promise.all([buildsApi.get(id), wsApi.workspace(id)]);
    setDetail(d); setWs(w);
    if (w.draft) setServerDraft(w.draft);
    else setServerDraft((await wsApi.open(id)).draft);
    refreshCheck();
    return w;
  }, [id, refreshCheck, setServerDraft]);

  useEffect(() => {
    setDetail(null); setNotFound(false); setWs(null); setDraftState(null); setCheck(null); setViewing(null); setSelection([]);
    setToast(null); setConfirm(null); setCanvasReady(false); setSteps(null);
    setToolState("select"); tabRef.current = "chat"; setTabState("chat");
    let live = true;
    (async () => {
      try {
        const d = await buildsApi.get(id);
        if (!live) return;
        setDetail(d);
        const w = await wsApi.workspace(id);
        if (!live) return;
        setWs(w);
        const dr = w.draft ?? (await wsApi.open(id)).draft;
        if (!live) return;
        setServerDraft(dr);
        refreshCheck();
      } catch (e) {
        if (live && (e as { status?: number }).status === 404) setNotFound(true);
        else if (live) say((e as Error).message, { error: true });
      }
    })();
    return () => { live = false; };
  }, [id, refreshCheck, say, setServerDraft]);

  // Snapshots and parts lists of new versions are still being made: refetch until they are done.
  const pending = detail?.pending ?? false;
  useEffect(() => {
    if (!pending) return;
    const t = setTimeout(() => { buildsApi.get(id).then(setDetail).then(refreshBuilds).catch(() => {}); }, 1500);
    return () => clearTimeout(t);
  }, [pending, detail, id, refreshBuilds]);

  /** `changes` is net (undo stack based, computed by the backend), so the canvas's revision is not enough: ask for the draft. */
  const syncDraft = useCallback(() => {
    clearTimeout(draftTimer.current);
    draftTimer.current = setTimeout(() => { wsApi.workspace(id).then((w) => { setWs(w); if (w.draft) setDraftState(w.draft); }).catch(() => {}); }, 250);
  }, [id]);

  // ---- canvas -> workspace ----
  const onCanvasEvent = useCallback((e: CanvasEvent) => {
    if (e.type === "obd:ready") { setCanvasReady(true); setModeState(e.mode); }
    else if (e.type === "obd:mode") { setModeState(e.mode); if (e.mode !== "player") setSteps(null); }
    else if (e.type === "obd:tool") setToolState(e.tool);
    else if (e.type === "obd:steps") setSteps({ total: e.total, current: e.current });
    else if (e.type === "obd:shortcut") { if (e.action === "save") saveRef.current(); }
    else if (e.type === "obd:error") say(e.message, { error: true });
    else if (e.type === "obd:changed") {
      setDraftState((d) => d && { ...d, revision: e.revision, undo_depth: e.undo_depth, redo_depth: e.redo_depth });
      syncDraft();
      setEditTick((n) => n + 1);
      clearTimeout(checkTimer.current);
      checkTimer.current = setTimeout(refreshCheck, 800);
    } else if (e.type === "obd:selection") {
      setSelection(e.pieces);
      if (e.pieces.length) { if (tabRef.current !== "piece") prevTab.current = tabRef.current; tabRef.current = "piece"; setTabState("piece"); }
      else if (tabRef.current === "piece") { tabRef.current = prevTab.current; setTabState(prevTab.current); }
    }
  }, [refreshCheck, say, syncDraft]);

  // ---- derived ----
  const versions = useMemo<VersionRow[]>(
    () => (ws?.versions ?? []).map((w) => ({ ...w, detail: detail?.versions.find((v) => v.version === w.version) })).sort((a, b) => a.version - b.version),
    [ws, detail],
  );
  const changes = draft?.changes ?? 0;
  const latest = versions.at(-1)?.version ?? 0;
  const nextVersion = latest + 1;
  const empty = !!ws && versions.length === 0 && changes === 0 && !viewing;
  const baseVersion = draft?.base_version ?? latest;
  const readOnly = viewing !== null || mode !== "edit";
  const proposals: Proposal[] = ws?.proposals ?? [];

  // ---- actions ----
  async function act<T>(label: string, work: () => Promise<T>): Promise<T | undefined> {
    setBusy(label);
    try { return await work(); }
    catch (e) { say((e as Error).message, { error: true }); }
    finally { setBusy(null); }
  }
  const undo = () => { if (!viewing) send({ cmd: "undo" }); };
  const redo = () => { if (!viewing) send({ cmd: "redo" }); };
  // A read-only view (version or proposal) ignores reload, so leave it first (a no-op when the canvas shows the draft).
  const afterBackendChange = async () => { await reloadAll(); send({ cmd: "back-to-draft" }); send({ cmd: "reload" }); };

  const setTool = useCallback((t: Tool) => { setToolState(t); sendRef.current({ cmd: "set-tool", tool: t }); }, []);
  const setMode = useCallback((m: CanvasMode) => { setModeState(m); sendRef.current({ cmd: "set-mode", mode: m }); }, []);
  useEffect(() => { if (phone && mode === "edit") setMode("view"); }, [phone, mode, setMode]);

  const pickColour = (c: number) => { setColour(c); if (tool === "paint") send({ cmd: "set-colour", colour: c }); };
  const recolourSelection = (c: number) => { setColour(c); send({ cmd: "set-colour", colour: c }); };
  const addPart = (part: string, c: number) => { setColour(c); setToolState("add"); send({ cmd: "add-part", part, colour: c }); };

  const clearSelection = () => { setSelection([]); if (tabRef.current === "piece") { tabRef.current = prevTab.current; setTabState(prevTab.current); } };
  const backToDraft = () => { setViewing(null); send({ cmd: "back-to-draft" }); };
  const viewVersion = (version: number) => {
    const row = versions.find((v) => v.version === version);
    if (version === baseVersion && changes === 0) return backToDraft();
    const url = row?.detail?.model_url;
    if (!url) return say(`v${version} has no model file to show.`, { error: true });
    setViewing({ kind: "version", version }); clearSelection();
    send({ cmd: "view-version", url });
  };

  const save = () => act("save", async () => {
    if (viewing || (changes === 0 && latest > 0)) return;
    const r = await wsApi.save(id);
    await afterBackendChange(); refreshBuilds();
    say(r.message || `Saved as v${r.version}.`);
  });

  const discard = () => {
    if (changes === 0) return;
    setConfirm({
      title: "Discard the draft?", body: `Your ${changes} change${changes === 1 ? "" : "s"} will be thrown away and the build goes back to v${baseVersion}.`, confirmLabel: "Discard draft", danger: true,
      onConfirm: () => { act("discard", async () => { await wsApi.discard(id); setViewing(null); await afterBackendChange(); say("Draft discarded."); }); },
    });
  };

  const branch = (version: number) => {
    const run = (force: boolean) => act("branch", async () => {
      setServerDraft((await wsApi.open(id, { version, force })).draft);
      setViewing(null); await afterBackendChange(); say(`New draft from v${version}.`);
    });
    const ask = () => setConfirm({
      title: `Branch from v${version}?`, body: `The draft has ${changes} unsaved change${changes === 1 ? "" : "s"}. Branching replaces them with a fresh draft on v${version}.`, confirmLabel: "Replace the draft", danger: true,
      onConfirm: () => { run(true); },
    });
    if (changes > 0) return ask();
    act("branch", async () => {
      try { setServerDraft((await wsApi.open(id, { version })).draft); }
      catch (e) { if ((e as { status?: number }).status === 409) { ask(); return; } throw e; }
      setViewing(null); await afterBackendChange(); say(`New draft from v${version}.`);
    });
  };

  /** One chat message through the free fast path. The server saves the turn in the build's chat; the chat shows the result. */
  const describe = async (text: string, previous?: string, quick?: boolean): Promise<DescribeResult> => {
    const r = await wsApi.describe(id, text, previous, quick);
    if (r.route === "generator" || r.route === "edit") {
      if (r.draft) setServerDraft(r.draft);
      setViewing(null); await afterBackendChange();
    }
    return r;
  };

  const regenerate = (generator: string, params: Record<string, unknown>) => act("regenerate", async () => {
    const r = await wsApi.regenerate(id, generator, params);
    if (r.draft) setServerDraft(r.draft);
    setViewing(null); await afterBackendChange();
    say(r.replaced_manual_edits ? "Regenerated. Your manual edits were replaced." : "Regenerated into the draft.", { action: { label: "Undo", run: undo } });
  });

  const keepProposal = (p: Proposal) => act("proposal", async () => {
    const r = await wsApi.accept(id, p.id);
    if (r.draft) setServerDraft(r.draft);
    setViewing(null); await afterBackendChange();
    say(`Kept ${p.summary.added} added part${p.summary.added === 1 ? "" : "s"} in the draft.`, { action: { label: "Undo", run: undo } });
  });
  const dropProposal = (p: Proposal) => act("proposal", async () => {
    await wsApi.discardProposal(id, p.id);
    if (viewing?.kind === "proposal" && viewing.id === p.id) backToDraft();
    await reloadAll();
    say("Proposal discarded.");
  });
  const previewProposal = (p: Proposal) => { setViewing({ kind: "proposal", id: p.id }); clearSelection(); send({ cmd: "view-version", url: wsApi.proposalUrl(id, p.id) }); };

  // Ctrl+S: Save as vN (the canvas owns every other shortcut)
  saveRef.current = save;
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && !e.shiftKey && !e.altKey && e.key.toLowerCase() === "s") { e.preventDefault(); saveRef.current(); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return {
    id, phone, detail, notFound, ws, draft, check, tab, tool, mode, colour, selection, viewing, editTick, busy, toast, confirm, sheetOpen, canvasReady, steps,
    versions, changes, latest, nextVersion, empty, baseVersion, readOnly, proposals,
    setTab, setSheetOpen, setConfirm, setToast, setColour: pickColour, recolourSelection, setTool, setMode, addPart, send, registerCanvas, onCanvasEvent,
    undo, redo, backToDraft, viewVersion, save, discard, branch, describe, regenerate, keepProposal, dropProposal, previewProposal, say,
    rename: async (title: string) => { const d = await act("rename", () => buildsApi.rename(id, title)); if (d) { setDetail(d); refreshBuilds(); } },
    reloadAll, refreshCheck,
  };
}

export type Workspace = ReturnType<typeof useWorkspace>;
