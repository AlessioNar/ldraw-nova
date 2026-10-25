import { useEffect, useLayoutEffect, useRef, useState } from "react";
import type { Inventory, ModelProfile, TurnOptions } from "../api";
import PartsControl, { partsSummary, type PartsSteering } from "./parts/PartsControl";
import WorkerModelPicker from "./WorkerModelPicker";
import { tokenLabel } from "../modelChoices";

export const DEFAULT_BUDGET_EUR = 2;
const BUDGETS = [1, 2, 5, 10, 20];
/** The next preset above `b` (or double it past the last one): what "Raise budget" offers. */
export const raisedBudget = (b: number) => BUDGETS.find(n => n > b) ?? b * 2;
const KEY = "ldraw-nova.chatSettings";
const EFFORT_NAMES: Record<string, string> = { none: "No reasoning", low: "Low", medium: "Medium", high: "High", xhigh: "XHigh", max: "Max" };
const PERMISSION_HINTS: Record<TurnOptions["permissions"], string> = {
  ask: "The agent asks before running tools in the container.",
  full: "The agent runs tools without asking. Needed for workers.",
  read_only: "The agent can look but not change files.",
};

/** Last-used settings, remembered per viewer for every new chat. Storage may be unavailable or corrupt: fall back to defaults. */
export function loadChatDefaults(): TurnOptions {
  const out: TurnOptions = { mode: "agent", permissions: "ask", budget_eur: DEFAULT_BUDGET_EUR };
  try {
    const v = JSON.parse(localStorage.getItem(KEY) ?? "null");
    if (!v || typeof v !== "object") return out;
    if (v.mode === "plan") out.mode = "plan";
    if (v.permissions === "full" || v.permissions === "read_only") out.permissions = v.permissions;
    if (v.budget_eur === null) out.budget_eur = null;
    else if (typeof v.budget_eur === "number" && v.budget_eur > 0) out.budget_eur = v.budget_eur;
    if (v.worker_model_id === null || typeof v.worker_model_id === "string") out.worker_model_id = v.worker_model_id;
    if (v.confirm_with_documents === true) out.confirm_with_documents = true;
    if (typeof v.effort === "string") out.effort = v.effort;
    if (typeof v.context_tokens === "number") out.context_tokens = v.context_tokens;
  } catch { /* storage unavailable or corrupt */ }
  return out;
}
export function saveChatDefaults(o: TurnOptions) {
  const { mode, permissions, budget_eur, worker_model_id, effort, context_tokens, confirm_with_documents } = o;
  try { localStorage.setItem(KEY, JSON.stringify({ mode, permissions, budget_eur: budget_eur ?? null, worker_model_id, effort, context_tokens, confirm_with_documents })); } catch { /* storage unavailable */ }
}

export function budgetLabel(b: number | null | undefined) {
  return b ? `Budget €${+b.toFixed(2)}` : "No budget";
}

/** The per-chat settings that used to crowd the composer toolbar. A popover on desktop, a bottom sheet on a phone (CSS). */
export default function ChatSettings({ options, onChange, profile, defaultWorkerId, inventories, steering, onSteering, onInventoriesChanged, disabled, focus, onClose }: {
  options: TurnOptions; onChange: (next: TurnOptions) => void; profile?: ModelProfile; defaultWorkerId: string | null;
  inventories: Inventory[]; steering: PartsSteering; onSteering: (next: PartsSteering) => void; onInventoriesChanged?: () => void;
  disabled: boolean; focus: "settings" | "budget"; onClose: () => void;
}) {
  const root = useRef<HTMLDivElement>(null);
  const budget = useRef<HTMLSelectElement>(null);
  const [custom, setCustom] = useState(() => !!options.budget_eur && !BUDGETS.includes(options.budget_eur));
  const budgetValue = custom ? "custom" : options.budget_eur ? String(options.budget_eur) : "";

  // On desktop the popover opens upwards and must fit the room above the composer (the home page composer sits mid-screen).
  useLayoutEffect(() => {
    const el = root.current;
    if (!el) return;
    const fit = () => { el.style.maxHeight = window.innerWidth > 820 ? `${Math.max(240, Math.min(640, (el.parentElement?.getBoundingClientRect().top ?? 600) - 16))}px` : ""; };
    fit(); window.addEventListener("resize", fit);
    return () => window.removeEventListener("resize", fit);
  }, []);
  useEffect(() => {
    if (focus === "budget") budget.current?.focus();
    else root.current?.querySelector<HTMLElement>("button, select, input")?.focus();
  }, [focus]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      // A worker-model picker that is open handles its own Escape.
      if (e.key === "Escape" && !document.querySelector(".model-picker-panel")) { e.preventDefault(); onClose(); }
    };
    const onDown = (e: PointerEvent) => {
      const t = e.target as HTMLElement;
      if (root.current?.contains(t) || t.closest(".model-picker-panel, .chat-settings-trigger")) return;
      onClose();
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", onDown);
    return () => { document.removeEventListener("keydown", onKey); document.removeEventListener("pointerdown", onDown); };
  }, [onClose]);

  const hasAdvanced = !!profile?.efforts.length || !!profile?.context_budgets.length;
  return <>
    <div className="chat-settings-scrim" aria-hidden="true" />
    <div className="chat-settings" role="dialog" aria-label="Chat settings" ref={root}>
      <div className="chat-settings-head"><strong>This chat</strong>
        <button type="button" className="icon-button chat-settings-close" aria-label="Close settings" onClick={onClose}>✕</button></div>
      <div className="setting-row"><label id="set-mode">Mode</label>
        <div className="segmented" role="radiogroup" aria-labelledby="set-mode">
          {(["agent", "plan"] as const).map(m => <button type="button" key={m} role="radio" aria-checked={options.mode === m} aria-pressed={options.mode === m}
            disabled={disabled} title={m === "agent" ? "Build with the agent" : "Work out a design in Plan"} onClick={() => onChange({ ...options, mode: m })}>{m === "agent" ? "Agent" : "Plan"}</button>)}
        </div></div>
      {options.mode === "agent" && <div className="setting-row"><label htmlFor="set-permissions">Permissions</label>
        <div><select id="set-permissions" aria-label="Permissions" disabled={disabled} value={options.permissions}
          onChange={e => onChange({ ...options, permissions: e.target.value as TurnOptions["permissions"] })}>
          <option value="ask">Ask first</option><option value="full">Full access</option><option value="read_only">Read only</option>
        </select><p className="muted small">{PERMISSION_HINTS[options.permissions]}</p>
          {options.permissions === "full" && <label className="small"><input type="checkbox" disabled={disabled} checked={options.confirm_with_documents === true}
            onChange={e => onChange({ ...options, confirm_with_documents: e.target.checked })} /> Still ask before running code when documents are attached</label>}</div></div>}
      <div className="setting-row"><label>Parts<small className="muted">{partsSummary(steering, inventories)}</small></label>
        <PartsControl inventories={inventories} value={steering} onChange={onSteering} disabled={disabled} onChanged={onInventoriesChanged} /></div>
      <div className="setting-row"><label>Workers</label>
        <WorkerModelPicker value={options.worker_model_id !== undefined ? options.worker_model_id : defaultWorkerId} disabled={disabled}
          onChange={id => onChange({ ...options, worker_model_id: id })} /></div>
      <div className="setting-row"><label htmlFor="set-budget">Budget<small className="muted">per request</small></label>
        <div><select id="set-budget" ref={budget} disabled={disabled} value={budgetValue}
          title="Each request (message) stops when it has cost about this much, planner and workers together. Estimated from list prices."
          onChange={e => {
            const v = e.target.value;
            setCustom(v === "custom");
            if (v === "custom") onChange({ ...options, budget_eur: options.budget_eur || DEFAULT_BUDGET_EUR });
            else onChange({ ...options, budget_eur: v ? Number(v) : null });
          }}>
          <option value="">No budget</option>
          {BUDGETS.map(n => <option value={n} key={n}>€{n}</option>)}
          <option value="custom">Custom…</option>
        </select>
        {custom && <input type="number" min={0.1} step={0.5} aria-label="Custom budget in euros" disabled={disabled} value={options.budget_eur ?? ""}
          onChange={e => onChange({ ...options, budget_eur: e.target.value && Number(e.target.value) > 0 ? Number(e.target.value) : null })} />}
        </div></div>
      {hasAdvanced && <details className="setting-advanced"><summary>Advanced</summary>
        {!!profile?.efforts.length && <div className="setting-row"><label htmlFor="set-effort">Reasoning effort</label>
          <select id="set-effort" disabled={disabled} value={options.effort ?? profile.default_effort ?? ""} onChange={e => onChange({ ...options, effort: e.target.value })}>
            {profile.efforts.map(e => <option value={e} key={e}>{EFFORT_NAMES[e] ?? e}</option>)}
          </select></div>}
        {!!profile?.context_budgets.length && <div className="setting-row"><label htmlFor="set-context">Context window</label>
          <select id="set-context" disabled={disabled} value={options.context_tokens ?? profile.context_window ?? ""} onChange={e => onChange({ ...options, context_tokens: Number(e.target.value) })}>
            {profile.context_budgets.map(n => <option value={n} key={n}>{tokenLabel(n)}</option>)}
          </select></div>}
      </details>}
      <p className="muted small chat-settings-note">Saved for this chat and used for new chats.</p>
    </div>
  </>;
}
