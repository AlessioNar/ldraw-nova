import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { Link } from "react-router-dom";
import { useApp } from "../context";
import { inventoryApi, type TurnOptions, type DocumentUpload, type Inventory } from "../api";
import { loadSteering, saveSteering, type PartsSteering } from "./parts/PartsControl";
import ModelPicker from "./ModelPicker";
import ChatSettings, { budgetLabel, loadChatDefaults, saveChatDefaults } from "./ChatSettings";
import { rememberModel } from "../modelChoices";
import DocumentIcon from "./DocumentIcon";

const DOCUMENT_TYPES = ".pdf,.txt,.md,.csv,.json,.yaml,.yml,.xml,.html,.rtf,.docx,.xlsx,.pptx,.mpd,.ldr,.dat";

export default function Composer({ llmId, onLlmChange, onSend, onStop, running, autoFocus, initialText = "", initialOptions, budgetReached }: {
  llmId: string | null;
  onLlmChange: (id: string) => void;
  onSend: (text: string, options: TurnOptions, images: string[], documents: DocumentUpload[]) => Promise<void> | void;
  onStop?: () => void;
  running: boolean;
  autoFocus?: boolean;
  initialText?: string;
  budgetReached?: boolean; // the last request stopped at its budget: the chip turns red
  initialOptions?: TurnOptions;
}) {
  const { llms, defaultWorkerId } = useApp();
  const [text, setText] = useState(initialText);
  const [busy, setBusy] = useState(false);
  const ref = useRef<HTMLTextAreaElement>(null);
  const upload = useRef<HTMLInputElement>(null);
  const documentUpload = useRef<HTMLInputElement>(null);
  const settingsButton = useRef<HTMLButtonElement>(null);
  const budgetButton = useRef<HTMLButtonElement>(null);
  const attachButton = useRef<HTMLButtonElement>(null);
  const attachMenu = useRef<HTMLDivElement>(null);
  const [panel, setPanel] = useState<"settings" | "budget" | null>(null);
  const [attachOpen, setAttachOpen] = useState(false);
  // An existing chat keeps its own saved options; a new chat starts from the last-used ones.
  const [selection, setSelection] = useState<{ modelId: string | null; options: TurnOptions }>(() => ({ modelId: llmId,
    options: initialOptions?.mode ? { ...initialOptions, mode: initialOptions.mode !== "agent" ? "plan" : "agent", permissions: initialOptions.permissions ?? "ask", budget_eur: initialOptions.budget_eur ?? null }
      : loadChatDefaults() }));
  const [images, setImages] = useState<{ name: string; url: string; size: number }[]>([]);
  const [documents, setDocuments] = useState<(DocumentUpload & { size: number })[]>([]);
  const [error, setError] = useState("");
  const [inventories, setInventories] = useState<Inventory[]>([]);
  const [steering, setSteering] = useState<PartsSteering>(() => initialOptions?.parts_mode
    ? { mode: initialOptions.parts_mode, ids: initialOptions.inventory_ids ?? [] } : loadSteering());
  const model = llms.find(m => m.id === llmId);
  const profile = model?.profile;
  const disabled = running || busy;
  // Resolve synchronously so a quick model switch + Send cannot submit the
  // previous provider's effort/context while waiting for an effect to run.
  const sameModel = selection.modelId === llmId;
  const saved = selection.options;
  const options: TurnOptions = { ...saved,
    effort: sameModel && saved.effort && profile?.efforts.includes(saved.effort) ? saved.effort : profile?.default_effort ?? null,
    worker_model_id: saved.worker_model_id !== undefined ? saved.worker_model_id : defaultWorkerId,
    context_tokens: sameModel && saved.context_tokens && profile?.context_budgets.includes(saved.context_tokens) ? saved.context_tokens : profile?.context_window ?? null,
  };
  const setOptions = (next: TurnOptions) => { setSelection({ modelId: llmId, options: next }); saveChatDefaults(next); };
  function chooseModel(id: string) {
    const next = llms.find(m => m.id === id)?.profile;
    setSelection({ modelId: id, options: { ...options, effort: next?.default_effort ?? null, context_tokens: next?.context_window ?? null } });
    onLlmChange(id);
  }
  useEffect(() => setText(initialText), [initialText]);
  // The chat's saved budget changed (a "Continue with more budget" button raised it): show it in the chip.
  const savedBudget = initialOptions?.budget_eur;
  useEffect(() => {
    if (savedBudget !== undefined) setSelection(cur => cur.options.budget_eur === savedBudget ? cur : { ...cur, options: { ...cur.options, budget_eur: savedBudget } });
  }, [savedBudget]);
  useEffect(() => {
    if (!attachOpen) return;
    const onDown = (e: PointerEvent) => { if (!attachMenu.current?.contains(e.target as Node) && !attachButton.current?.contains(e.target as Node)) setAttachOpen(false); };
    const onKey = (e: globalThis.KeyboardEvent) => { if (e.key === "Escape") { setAttachOpen(false); attachButton.current?.focus(); } };
    document.addEventListener("pointerdown", onDown); document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("pointerdown", onDown); document.removeEventListener("keydown", onKey); };
  }, [attachOpen]);
  function closePanel() {
    const opener = panel === "budget" ? budgetButton.current : settingsButton.current;
    setPanel(null);
    opener?.focus();
  }
  useEffect(() => { inventoryApi.list().then(r => setInventories(r.inventories), () => {}); }, []);
  // Only ids that still exist count; a mode other than off needs at least one, so fall back to the newest list.
  const known = inventories.filter(i => steering.ids.includes(i.id)).map(i => i.id);
  const effective: PartsSteering = { mode: steering.mode, ids: steering.mode !== "off" && !known.length && inventories.length ? [inventories[0].id] : known };
  const partsOptions: Partial<TurnOptions> = !inventories.length ? { parts_mode: undefined, inventory_ids: undefined }
    : effective.mode === "off" ? { parts_mode: "off", inventory_ids: undefined }
    : { parts_mode: effective.mode, inventory_ids: effective.ids };
  function changeSteering(next: PartsSteering) { setSteering(next); saveSteering(next); }
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 240) + "px";
  }, [text]);

  async function submit(e?: FormEvent) {
    e?.preventDefault();
    const value = text.trim();
    if (!value || disabled || !model) return;
    setBusy(true);
    try {
      setError("");
      await onSend(value, { ...options, ...partsOptions }, images.map(i => i.url), documents.map(({ name, data }) => ({ name, data })));
      rememberModel(model.id);
      setText(""); setImages([]); setDocuments([]);
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); submit(); }
  }
  async function attach(files: File[], kind: "image" | "document" = "image") {
    if (!files.length) return;
    setError("");
    if (files.length + (kind === "image" ? images.length : documents.length) > 4 || files.some(f => f.size > 5 * 1024 * 1024)) {
      setError(`Attach up to 4 ${kind === "image" ? "images" : "documents"}, at most 5 MB each.`); return;
    }
    if ([...images, ...documents, ...files].reduce((total, f) => total + f.size, 0) > 12 * 1024 * 1024) {
      setError("Images and documents must total at most 12 MB."); return;
    }
    if (kind === "image" && files.some(f => !["image/png", "image/jpeg", "image/webp"].includes(f.type))) {
      setError("Choose PNG, JPEG or WebP images."); return;
    }
    if (kind === "document" && files.some(f => !DOCUMENT_TYPES.split(",").includes("." + f.name.split(".").pop()?.toLowerCase()))) {
      setError("Choose a PDF, text, Office or LDraw document. Use the image button for images."); return;
    }
    setBusy(true);
    try {
      const added = await Promise.all(files.map(file => new Promise<{ name: string; url: string; size: number }>((resolve, reject) => {
        const reader = new FileReader(); reader.onload = () => resolve({ name: file.name, url: String(reader.result), size: file.size });
        reader.onerror = () => reject(new Error("Cannot read attachment")); reader.readAsDataURL(file);
      })));
      if (kind === "image") setImages(current => [...current, ...added]);
      else setDocuments(current => [...current, ...added.map(({ name, url, size }) => ({ name, data: url, size }))]);
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  // A dot on the settings button when something other than the defaults is active.
  const nonDefault = options.mode !== "agent" || (options.mode === "agent" && options.permissions !== "ask")
    || (!!inventories.length && effective.mode !== "off") || (options.worker_model_id ?? null) !== (defaultWorkerId ?? null)
    || (!!profile?.efforts.length && options.effort !== profile.default_effort)
    || (!!profile?.context_budgets.length && options.context_tokens !== profile.context_window);

  return <form className="composer" onSubmit={submit}>
    {!llms.length && <div className="composer-notice">No model configured yet. <Link to="/settings">Add a model in Settings</Link> to start building.</div>}
    <div className="composer-input">
      <div className="attach-wrap">
        <button type="button" className="attach-button" ref={attachButton} aria-label="Attach" aria-haspopup="menu" aria-expanded={attachOpen} title="Attach images or documents (up to 4 each)"
          disabled={disabled || !model} onClick={() => setAttachOpen(!attachOpen)}>
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true">
            <path d="m8 13 7-7a3 3 0 0 1 4 4L9 20a5 5 0 0 1-7-7L13 2a2 2 0 0 1 3 3L5 16" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </button>
        {attachOpen && <div className="attach-menu" role="menu" ref={attachMenu}>
          <button type="button" role="menuitem" onClick={() => { setAttachOpen(false); upload.current?.click(); }}>Attach image</button>
          <button type="button" role="menuitem" onClick={() => { setAttachOpen(false); documentUpload.current?.click(); }}>Attach document</button>
        </div>}
      </div>
      <input ref={documentUpload} hidden type="file" aria-label="Document files" accept={DOCUMENT_TYPES} multiple disabled={disabled} onChange={e => {
        const files = Array.from(e.target.files ?? []); e.target.value = ""; void attach(files, "document");
      }} />
      <input ref={upload} hidden type="file" aria-label="Image files" accept="image/png,image/jpeg,image/webp" multiple disabled={disabled} onChange={e => {
        const files = Array.from(e.target.files ?? []); e.target.value = ""; void attach(files);
      }} />
      <textarea ref={ref} rows={2} value={text} autoFocus={autoFocus} aria-label="Message" placeholder="Describe a model to build…"
        onChange={e => setText(e.target.value)} onKeyDown={onKeyDown} />
    </div>
    {!!images.length && <div className="attachments">{images.map((img, index) => <div key={index}>
      <img src={img.url} alt={img.name} /><button type="button" aria-label={`Remove ${img.name}`} disabled={disabled} onClick={() => setImages(images.filter((_, i) => i !== index))}>✕</button>
    </div>)}</div>}
    {!!documents.length && <div className="document-attachments">{documents.map((doc, index) => <div className="document-chip" key={index}>
      <DocumentIcon /><span>{doc.name}</span><button type="button" className="icon-button" aria-label={`Remove ${doc.name}`} disabled={disabled} onClick={() => setDocuments(documents.filter((_, i) => i !== index))}>✕</button>
    </div>)}</div>}
    {error && <div className="banner error" role="alert">{error}</div>}
    <div className="composer-toolbar">
      <div className="turn-options">
        <ModelPicker value={llmId} onChange={chooseModel} disabled={disabled || !llms.length} />
        <button type="button" ref={budgetButton} className={`compact-control chat-settings-trigger budget-chip${budgetReached ? " over" : ""}`} aria-haspopup="dialog" aria-expanded={panel === "budget"}
          title="Spend limit for each request: planner and workers stop when a message has cost about this much. Click to change." onClick={() => setPanel(panel === "budget" ? null : "budget")}>
          {budgetLabel(options.budget_eur)}{options.budget_eur ? <small> per request</small> : null}
        </button>
        <button type="button" ref={settingsButton} className="compact-control chat-settings-trigger settings-button" aria-haspopup="dialog" aria-expanded={panel === "settings"}
          aria-label={nonDefault ? "Chat settings (changed from defaults)" : "Chat settings"} title="Mode, permissions, parts, workers, budget and more"
          onClick={() => setPanel(panel === "settings" ? null : "settings")}>
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true">
            <path d="M4 7h10M18 7h2M4 17h2M10 17h10" /><circle cx="16" cy="7" r="2" /><circle cx="8" cy="17" r="2" />
          </svg>
          {nonDefault && <span className="settings-dot" aria-hidden="true" />}
        </button>
      </div>
      {running ? <button type="button" className="danger send-button" onClick={onStop}>Stop</button> :
        <button type="submit" className="primary send-button" disabled={!text.trim() || busy || !model}>Send <span aria-hidden>↑</span></button>}
    </div>
    {panel && <ChatSettings options={options} onChange={setOptions} profile={profile} defaultWorkerId={defaultWorkerId ?? null} inventories={inventories}
      steering={effective} onSteering={changeSteering} disabled={disabled} focus={panel} onClose={closePanel} />}
    <div className="composer-hint muted">Enter to send · Shift+Enter for a new line</div>
  </form>;
}
