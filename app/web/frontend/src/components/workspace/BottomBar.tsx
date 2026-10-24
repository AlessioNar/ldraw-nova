import { useEffect, useRef, useState } from "react";
import { buildsApi, estimateLabel, ORIGIN_LABEL, timeAgo, type DescribeHint } from "../../api";
import { useColours } from "../../workspace/colours";
import { missingCount, type DescribeResult, type PartsCheckData } from "../../workspace/types";
import type { Workspace } from "../../workspace/useWorkspace";
import Icon from "./Icon";

type Ask = { note: string; estimate: string | null } | { question: string };

/** One line: type a change, see the free rule-based hint while typing, Apply puts it in the draft. Nothing is spent here. */
function DescribeBar({ ws }: { ws: Workspace }) {
  const [text, setText] = useState("");
  const [hint, setHint] = useState<DescribeHint | null>(null);
  const [ask, setAsk] = useState<Ask | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const generator = ws.ws?.generator?.id;
  useEffect(() => { setAsk(null); setError(null); }, [text]);
  useEffect(() => {      // rules only, a millisecond: no AI call while typing
    setHint(null);
    if (text.trim().length < 3) return;
    const t = setTimeout(() => buildsApi.hint(text, generator).then(setHint).catch(() => {}), 350);
    return () => clearTimeout(t);
  }, [text, generator]);

  async function apply() {
    const value = text.trim();
    if (!value || busy) return;
    setBusy(true); setError(null);
    try {
      const r: DescribeResult = await ws.describe(value);
      if (r.route === "generator" || r.route === "edit") setText("");
      else if (r.route === "clarify") setAsk({ question: r.question ?? "Can you say a little more?" });
      else setAsk({ note: r.reason ?? "No rule fits this.", estimate: estimateLabel(r.estimate) });
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  const moveToAi = () => { ws.askAi(text.trim()); setText(""); };

  let line: React.ReactNode = null;
  if (error) line = <span className="ws-warn">{error}</span>;
  else if (ask && "question" in ask) line = <b className="cost">{ask.question}</b>;
  else if (ask) line = <><b className="cost">{ask.note.replace(/[.\s]+$/, "")}.</b> The AI can try{ask.estimate ? `: ${ask.estimate}` : ""}. Nothing is spent until you send it there.</>;
  else if (hint?.route === "needs_ai") line = <>No rule fits this. The AI can try{estimateLabel(hint.estimate) ? `: ${estimateLabel(hint.estimate)}` : ""}. It asks before it spends.</>;
  else if (hint?.hint) line = <><b>{hint.confidence >= 0.7 ? "Fast path" : "Probably"}:</b> {hint.hint}. Free, about 1 s.</>;

  const toAi = !!ask && !("question" in ask);
  return (
    <div className="ws-describe">
      <Icon name="pencil" />
      <input id="ws-describe-input" type="text" value={text} placeholder="Describe a change, e.g. make the roof blue" aria-label="Describe a change"
        onChange={(e) => setText(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") (toAi ? moveToAi : apply)(); }} />
      <span className="ws-hint muted small" aria-live="polite">{line}</span>
      {toAi
        ? <button className="primary" onClick={moveToAi}>Ask the AI</button>
        : <button className="primary" disabled={!text.trim() || busy || !!ws.viewing} onClick={apply} title={ws.viewing ? "Go back to the draft first" : "Apply to the draft (free)"}>
            {busy && <span className="spinner" aria-hidden />} Apply
          </button>}
    </div>
  );
}

const Check = ({ ok }: { ok: boolean }) => <span className={`ws-ck ${ok ? "ok" : "warn"}`} aria-label={ok ? "all parts owned" : "parts missing"}><Icon name={ok ? "check" : "warn"} size={9} /></span>;

function VersionStrip({ ws }: { ws: Workspace }) {
  const { versions, viewing, draft, changes, baseVersion } = ws;
  const showDraft = !!draft && (changes > 0 || versions.length === 0);
  const selectedVersion = viewing?.kind === "version" ? viewing.version : !showDraft && !viewing ? baseVersion : null;
  return (
    <div className="ws-vstrip" role="tablist" aria-label="Versions">
      {versions.length === 0 && !showDraft && <span className="muted small">No versions yet</span>}
      {versions.map((v) => {
        const miss = v.parts_check ? missingCount(v.parts_check) : null;
        return (
          <button key={v.version} role="tab" className="ws-v" aria-selected={selectedVersion === v.version} onClick={() => ws.viewVersion(v.version)}
            title={`${ORIGIN_LABEL[v.origin]}${v.detail ? `, ${timeAgo(v.detail.created_at)}` : ""}. Click to view it read-only.`}>
            <span className="t"><span className="mono">v{v.version}</span> <span className={`origin-badge origin-${v.origin}`}>{ORIGIN_LABEL[v.origin]}</span></span>
            <span className="s"><span className="mono">{v.parts.toLocaleString()}</span> parts {miss !== null && <Check ok={miss === 0} />}</span>
          </button>
        );
      })}
      {showDraft && (
        <button role="tab" className="ws-v draft" aria-selected={!viewing} onClick={ws.backToDraft} title="Your working copy. Edits save into it automatically.">
          <span className="t">Draft on v{baseVersion}</span>
          <span className="s">{versions.length === 0 && changes === 0 ? "0 parts" : `${changes} ${changes === 1 ? "change" : "changes"}`}</span>
        </button>
      )}
    </div>
  );
}

function PartsCheck({ ws }: { ws: Workspace }) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const colours = useColours();
  useEffect(() => {
    if (!open) return;
    const on = (e: MouseEvent) => { if (!box.current?.contains(e.target as Node)) setOpen(false); };
    const key = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", on); document.addEventListener("keydown", key);
    return () => { document.removeEventListener("mousedown", on); document.removeEventListener("keydown", key); };
  }, [open]);

  const ofVersion = ws.viewing?.kind === "version" ? ws.versions.find((v) => v.version === (ws.viewing as { version: number }).version)?.parts_check : undefined;
  const c: PartsCheckData | null | undefined = ws.viewing ? ofVersion : ws.check;
  if (!c) return <span className="muted small ws-pc-none">{ws.viewing ? "" : "Parts check loading…"}</span>;
  const miss = missingCount(c);
  const lines = Array.isArray(c.missing) ? c.missing : [];
  const colourName = (x: number | string) => (typeof x === "number" ? colours.find((k) => k.code === x)?.name ?? `colour ${x}` : x);
  return (
    <div className="ws-pc-wrap" ref={box}>
      <button className={`ws-pcheck ${miss ? "warn" : "ok"}`} aria-expanded={open} onClick={() => miss > 0 && setOpen(!open)} title="Do you own the parts for what is on the canvas?">
        <Check ok={miss === 0} />
        <span className="ws-pc-txt">Parts check: <span className="mono">{c.owned.toLocaleString()} of {c.needed.toLocaleString()}</span> owned{miss ? `, ${miss} missing` : ""}</span>
      </button>
      {open && (
        <div className="ws-pc-pop" role="dialog" aria-label="Missing parts">
          <div className="label">Missing from your collection</div>
          {lines.length
            ? <ul>{lines.map((l, i) => <li key={i}><span className="mono">{l.need - l.have} ×</span> {l.part} <span className="muted">{colourName(l.colour)} · you own {l.have} of {l.need}</span></li>)}</ul>
            : <p className="muted small">{miss} part{miss === 1 ? "" : "s"} missing. Open the draft to see which.</p>}
          <p className="muted small">The check counts all your parts lists together.</p>
        </div>
      )}
    </div>
  );
}

/** Describe bar, version strip and parts check: global to the build, whatever tab is open. */
export default function BottomBar({ ws }: { ws: Workspace }) {
  return (
    <footer className="ws-bottom">
      <DescribeBar ws={ws} />
      <div className="ws-versions">
        <span className="label ws-vlabel">Versions</span>
        <VersionStrip ws={ws} />
        <span className="ws-spacer" />
        <PartsCheck ws={ws} />
      </div>
    </footer>
  );
}
