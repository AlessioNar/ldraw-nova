import { useEffect, useRef, useState } from "react";
import { ORIGIN_LABEL, timeAgo } from "../../api";
import { useColours } from "../../workspace/colours";
import { missingCount, type PartsCheckData } from "../../workspace/types";
import type { Workspace } from "../../workspace/useWorkspace";
import { STRIP, type Layout } from "../../workspace/useLayout";
import Icon from "./Icon";
import Splitter from "./Splitter";

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
            <span className="s"><span className="mono">{v.parts.toLocaleString()}</span> parts {miss !== null && <Check ok={miss === 0} />}
              {v.check && <span className={`ws-vcheck ${v.check.status}`} title={v.check.warnings.join(String.fromCharCode(10)) || "Geometry and connection check"}>{v.check.status === "checking" ? "checking…" : v.check.status === "ok" ? "OK" : v.check.status === "warnings" ? `${v.check.warnings.length} warning${v.check.warnings.length === 1 ? "" : "s"}` : "check failed"}</span>}</span>
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

/** Version strip and parts check: global to the build, whatever tab is open. Resizable, and collapsible to a thin bar. */
export default function BottomBar({ ws, layout }: { ws: Workspace; layout: Layout }) {
  const { stripThin, stripH } = layout;
  return (
    <footer className={`ws-bottom${stripThin ? " thin" : ""}`}>
      <Splitter edge="strip" label="Version strip height" value={stripH ?? 76} min={STRIP.min} max={STRIP.max} step={STRIP.step} collapsed={stripThin}
        onChange={layout.setStripH} onReset={layout.resetStrip} onToggle={layout.toggleStrip} hidden={ws.phone} />
      <div className={`ws-versions${stripH ? " sized" : ""}`} style={!stripThin && stripH ? { height: stripH } : undefined}>
        <span className="label ws-vlabel">Versions</span>
        {!stripThin && <VersionStrip ws={ws} />}
        {stripThin && <button className="ws-vthin" onClick={layout.toggleStrip} title="Show the versions">{ws.versions.length} version{ws.versions.length === 1 ? "" : "s"}</button>}
        <span className="ws-spacer" />
        <PartsCheck ws={ws} />
      </div>
    </footer>
  );
}
