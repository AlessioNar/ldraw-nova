import { useEffect, useMemo, useRef, useState } from "react";
import { colourHex } from "../../ldrawColours";
import { useColours } from "../../workspace/colours";
import type { CanvasCommand, CanvasEvent } from "../../workspace/types";
import type { Workspace } from "../../workspace/useWorkspace";
import Icon from "./Icon";

function useTheme(): "dark" | "light" {
  const read = (): "dark" | "light" => {
    const forced = document.documentElement.dataset.theme;
    return forced === "light" || forced === "dark" ? forced : matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
  };
  const [theme, setTheme] = useState(read);
  useEffect(() => {
    const update = () => setTheme(read());
    const mo = new MutationObserver(update);
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    const mq = matchMedia("(prefers-color-scheme: light)");
    mq.addEventListener("change", update);
    return () => { mo.disconnect(); mq.removeEventListener("change", update); };
  }, []);
  return theme;
}

/** The canvas iframe (viewer/editor.html in embed mode) plus the workspace's overlays on top of it. */
export default function CanvasPane({ ws }: { ws: Workspace }) {
  const frame = useRef<HTMLIFrameElement>(null);
  const theme = useTheme();
  const initialMode = useRef(ws.phone ? "view" : "edit");
  const colours = useColours();
  const { id, registerCanvas, onCanvasEvent } = ws;
  const loaded = !!ws.draft;
  const src = useMemo(() => `/viewer/editor.html?space=builds&id=${encodeURIComponent(id)}&embed=1&mode=${initialMode.current}&theme=${theme}`, [id, theme]);

  // messages in: only from our own frame
  useEffect(() => {
    const on = (e: MessageEvent) => {
      if (e.source !== frame.current?.contentWindow || e.origin !== location.origin) return;
      const d = e.data as CanvasEvent | undefined;
      if (typeof d?.type === "string" && d.type.startsWith("obd:")) onCanvasEvent(d);
    };
    window.addEventListener("message", on);
    return () => window.removeEventListener("message", on);
  }, [onCanvasEvent]);

  // commands out
  useEffect(() => {
    registerCanvas((c: CanvasCommand) => {
      frame.current?.contentWindow?.postMessage({ type: "obd:command", ...c }, location.origin);
    });
  }, [registerCanvas]);

  const viewing = ws.viewing;
  const viewedRow = viewing?.kind === "version" ? ws.versions.find((v) => v.version === viewing.version) : undefined;
  const lead = ws.selection[0];
  const colourName = (c: number | string) => colours.find((x) => x.code === Number(c))?.name ?? String(c);
  const parts = ws.check?.needed ?? ws.versions.find((v) => v.version === ws.baseVersion)?.parts;
  const shownParts = viewedRow?.parts ?? parts;

  return (
    <main className="ws-stage">
      {loaded && <iframe key={src} ref={frame} className="ws-frame" src={src} title="3D canvas" allow="fullscreen" />}
      {!loaded && <div className="ws-loading muted"><span className="spinner" aria-hidden /> Opening the build…</div>}

      {viewing && (
        <div className="ws-ro" role="status">
          {viewing.kind === "version" ? (
            <span>Viewing <strong>v{viewing.version}</strong> (read-only){viewedRow && <span className="muted"> · {viewedRow.parts.toLocaleString()} parts</span>}</span>
          ) : <span>Previewing the AI proposal <span className="muted">· read-only, not in your draft yet</span></span>}
          <span className="ws-spacer" />
          {viewing.kind === "version" && <button className="primary" disabled={ws.busy === "branch"} onClick={() => ws.branch(viewing.version)}>Branch from here</button>}
          {viewing.kind === "proposal" && (() => {
            const p = ws.proposals.find((x) => x.id === viewing.id);
            return p && <button className="primary" disabled={ws.busy === "proposal"} onClick={() => ws.keepProposal(p)}>Keep in draft</button>;
          })()}
          <button onClick={ws.backToDraft}>{ws.changes > 0 || viewing.kind === "proposal" ? "Back to draft" : `Back to v${ws.baseVersion}`}</button>
        </div>
      )}

      {ws.canvasReady ? (
        <>
          <div className={`ws-overlay ws-tl${viewing ? " lower" : ""}`}>
            <button className="ws-icon-btn solid" title="Reset view (Home)" aria-label="Reset view" onClick={() => ws.send({ cmd: "focus" })}><Icon name="fit" /></button>
          </div>
          {shownParts != null && (
            <div className={`ws-overlay ws-tr${viewing ? " lower" : ""}`}>
              {ws.mode === "player" && ws.steps && ws.steps.total > 0 && <span className="ws-chip mono" id="ws-step-label" role="status">Step {ws.steps.current} of {ws.steps.total}</span>}
              <span className="ws-chip mono">{shownParts.toLocaleString()} parts</span>
            </div>
          )}
        </>
      ) : null}

      {lead && !viewing && (
        <div className="ws-selchip" role="status">
          {ws.selection.length > 1
            ? <strong>{ws.selection.length} pieces selected</strong>
            : <><strong>{lead.name ?? lead.part}</strong> <span className="mono">{lead.part} · {colourName(lead.colour)}</span></>}
        </div>
      )}
      {ws.empty && loaded && (
        <div className="ws-empty-canvas">
          <div className="ws-empty-card">
            <div className="brick-row" aria-hidden="true">{[4, 14, 1, 2].map((c) => <i key={c} style={{ background: colourHex(c) }} />)}</div>
            <p><strong>Nothing here yet.</strong></p>
            <p className="muted">Describe what you want in the chat, or pick a part and click to place it.</p>
            <div className="ws-empty-actions">
              <button className="primary" onClick={() => { ws.setTab("chat"); setTimeout(() => document.querySelector<HTMLTextAreaElement>(".ws-composer textarea")?.focus(), 60); }}>Describe it</button>
              <button onClick={() => ws.setTab("parts")}>Pick a part</button>
            </div>
          </div>
        </div>
      )}
    </main>
  );
}
