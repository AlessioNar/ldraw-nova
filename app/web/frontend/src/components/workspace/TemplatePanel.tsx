import { useCallback, useEffect, useRef, useState } from "react";
import { colourHex } from "../../ldrawColours";
import { humanise } from "../GeneratorForm";
import { templateApi, type Correction, type JobRow, type Knob, type RolePreview, type SavedTemplate, type TemplateCheck } from "../../workspace/templateApi";
import type { Workspace } from "../../workspace/useWorkspace";

type Stage = "checking" | "refused" | "roles" | "working" | "result" | "saved" | "failed";

const STEPS: Record<string, string> = {
  start: "Loading the part library", roles: "Classifying the parts", baseline: "Measuring the original",
  stretch: "Checking longer and shorter versions", repeat: "Checking repeated sections", checked: "Checking longer and shorter versions",
  courses: "Checking wall courses", pictures: "Drawing the extreme values", done: "Finished",
};
const minutes = (s: number) => (s < 90 ? `${Math.round(s)} s` : `${Math.round(s / 60)} min`);
const words = (text: string) => text.split(",").map((w) => w.trim()).filter(Boolean);
const jobKey = (id: string) => `obd-template-job:${id}`;
const remembered = (id: string): string | null => { try { return sessionStorage.getItem(jobKey(id)); } catch { return null; } };
const remember = (id: string, job: string | null) => { try { if (job) sessionStorage.setItem(jobKey(id), job); else sessionStorage.removeItem(jobKey(id)); } catch { /* private window */ } };

/** "Make this a template": the quality gate, a role-coloured preview with role correction, the derivation (a background job
 *  with progress) and a card of the knobs it found. Saving stores the template for this person; it is then offered next to the generators. */
export default function TemplatePanel({ ws }: { ws: Workspace }) {
  const id = ws.id;
  const { send, canvasReady, say } = ws;
  const [stage, setStage] = useState<Stage>("checking");
  const [error, setError] = useState<string | null>(null);
  const [checked, setChecked] = useState<TemplateCheck | null>(null);
  const [preview, setPreview] = useState<RolePreview | null>(null);
  const [fixes, setFixes] = useState<Record<string, Correction>>({});
  const [title, setTitle] = useState("");
  const [kinds, setKinds] = useState("");
  const [job, setJob] = useState<string | null>(null);
  const [row, setRow] = useState<JobRow | null>(null);
  const [off, setOff] = useState<Set<string>>(new Set());
  const [saved, setSaved] = useState<SavedTemplate | null>(null);
  const [busy, setBusy] = useState(false);
  const [pick, setPick] = useState("");
  const showing = useRef<string | null>(null);

  // the canvas shows the role-coloured model while this panel is open, and the draft again when it closes
  useEffect(() => () => { if (showing.current) send({ cmd: "back-to-draft" }); showing.current = null; }, [send]);
  useEffect(() => {
    if (stage === "roles" && preview && canvasReady && showing.current !== preview.url) { showing.current = preview.url; send({ cmd: "view-version", url: preview.url }); }
    if (stage !== "roles" && showing.current) { showing.current = null; send({ cmd: "back-to-draft" }); }
  }, [stage, preview, canvasReady, send]);

  const run = useCallback(async (corrections: Correction[], gate: boolean) => {
    setError(null);
    try {
      const out = await templateApi.check(id, corrections, gate);
      if (gate) {
        setChecked(out);
        setTitle((t) => t || out.source.name);
        setKinds((k) => k || out.suggested_kinds.join(", "));
        if (!out.gate?.ok) { setStage("refused"); return; }
      }
      if (out.preview) { setPreview(out.preview); setStage("roles"); }
    } catch (e) { setError((e as Error).message); setStage("failed"); }
  }, [id]);

  useEffect(() => {
    const again = remembered(id);
    if (again) { setJob(again); setStage("working"); return; }
    setStage("checking"); run([], true);
  }, [id, run]);

  // follow a running job (it survives closing this panel); a finished one shows its card
  useEffect(() => {
    if (!job || (stage !== "working" && stage !== "result")) return;
    let alive = true;
    const poll = async () => {
      try {
        const next = await templateApi.job(id, job);
        if (!alive) return;
        setRow(next);
        if (next.state === "done") { setStage("result"); return; }
        if (next.state !== "running") { setError(next.error ?? "The job stopped."); setStage("failed"); remember(id, null); return; }
      } catch (e) {
        if (!alive) return;
        remember(id, null); setError((e as Error).message); setStage("failed"); return;
      }
      timer = setTimeout(poll, 2000);
    };
    let timer = setTimeout(poll, 0);
    return () => { alive = false; clearTimeout(timer); };
  }, [id, job, stage]);

  useEffect(() => {
    if (row?.result && stage === "result") { setTitle((t) => row.result?.title ?? t); setKinds((k) => (row.result?.kinds.length ? row.result.kinds.join(", ") : k)); }
  }, [row?.result, stage]);

  const corrections = Object.values(fixes);
  const fix = (key: string, c: Correction) => {
    const next = { ...fixes, [key]: c };
    setFixes(next);
    run(Object.values(next), false);
  };
  const applyToSelection = () => {
    if (!pick || !ws.selection.length) return;
    const next = { ...fixes };
    for (const p of ws.selection) next[`:${p.id}`] = { section: "", id: String(p.id), role: pick };
    setFixes(next);
    run(Object.values(next), false);
  };

  const start = async () => {
    setBusy(true); setError(null);
    try {
      const out = await templateApi.start(id, { title: title.trim(), kinds: words(kinds), corrections });
      remember(id, out.job); setJob(out.job); setRow(null); setStage("working");
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };
  const cancel = async () => {
    if (job) await templateApi.cancel(id, job).catch(() => {});
    remember(id, null); setJob(null); setRow(null); setStage("checking"); run(corrections, true);
  };
  const save = async () => {
    if (!job) return;
    setBusy(true); setError(null);
    try {
      const out = await templateApi.save(id, job, { title: title.trim(), kinds: words(kinds), disabled: [...off] });
      remember(id, null); setSaved(out); setStage("saved");
      say(`Saved the template “${out.title}”.`);
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };
  const toggle = (param: string) => setOff((s) => { const n = new Set(s); if (n.has(param)) n.delete(param); else n.add(param); return n; });
  const done = () => ws.setTab("chat");

  const result = row?.result;
  return (
    <section className="ws-panel tpl" role="tabpanel" aria-label="Make this a template">
      <div className="ws-chat-head"><span className="label">Make this a template</span><span className="ws-chip mono">no AI</span></div>
      {error && <div className="banner error" role="alert">{error}</div>}

      {stage === "checking" && <p className="muted"><span className="spinner" aria-hidden /> Checking that this build is sound enough to vary…</p>}

      {stage === "refused" && checked?.gate && (
        <>
          <p className="muted small ws-about">A template stretches and shrinks the build, and every size it offers is built and checked. That only works from a sound build ({checked.gate.parts} parts here).</p>
          <ul className="tpl-reasons">{checked.gate.reasons.map((r) => <li key={r}>{r}</li>)}</ul>
          <div className="gen-actions"><button onClick={() => { setStage("checking"); run(corrections, true); }}>Check again</button><button onClick={done}>Close</button></div>
        </>
      )}

      {stage === "roles" && preview && (
        <>
          <p className="muted small ws-about">The canvas shows each part in the colour of its role. Check them: a template recolours, removes and stretches by role. Roles you correct are kept.</p>
          <ul className="tpl-legend" aria-label="Roles">
            {Object.entries(preview.roles).map(([role, info]) => (
              <li key={role}><i style={{ background: colourHex(preview.palette[role] ?? 71) }} aria-hidden /><span>{humanise(role)}</span><span className="muted mono">{info.count}</span></li>
            ))}
          </ul>
          {ws.selection.length > 0 && (
            <div className="tpl-row">
              <span className="small">{ws.selection.length} selected</span>
              <select value={pick} aria-label="Role for the selected pieces" onChange={(e) => setPick(e.target.value)}>
                <option value="">is a…</option>
                {preview.vocabulary.map((r) => <option key={r} value={r}>{humanise(r)}</option>)}
              </select>
              <button disabled={!pick} onClick={applyToSelection}>Set role</button>
            </div>
          )}
          {preview.low_confidence.length > 0 && (
            <>
              <p className="small tpl-sub">Not sure about {preview.low_total} {preview.low_total === 1 ? "part" : "parts"}</p>
              <ul className="tpl-unsure">
                {preview.low_confidence.map((p) => {
                  const current = fixes[p.key]?.role ?? p.role;
                  return (
                    <li key={p.key}>
                      <i style={{ background: colourHex(p.colour) }} aria-hidden />
                      <span className="ellipsis" title={`${p.name} (${p.part})`}>{p.name}</span>
                      <select value={current} aria-label={`Role of ${p.name}`} onChange={(e) => fix(p.key, { section: p.section, id: p.id, role: e.target.value })}>
                        {[...new Set([current, ...preview.vocabulary])].map((r) => <option key={r} value={r}>{humanise(r)}</option>)}
                      </select>
                    </li>
                  );
                })}
              </ul>
            </>
          )}
          <label className="tpl-field">Name<input value={title} maxLength={120} onChange={(e) => setTitle(e.target.value)} /></label>
          <label className="tpl-field">It answers to<input value={kinds} onChange={(e) => setKinds(e.target.value)} aria-describedby="tpl-kinds-help" />
            <span id="tpl-kinds-help" className="muted small">Words, comma separated: “another one of those” will find it by these.</span></label>
          <p className="muted small ws-about">Making it builds and checks every size on offer: from under a minute to about 40 minutes for a large build. You can keep working meanwhile.</p>
          <div className="gen-actions"><button onClick={done}>Cancel</button><button className="primary" disabled={busy || !title.trim()} onClick={start}>{busy && <span className="spinner" aria-hidden />} Make the template</button></div>
        </>
      )}

      {stage === "working" && (
        <>
          <p><span className="spinner" aria-hidden /> {STEPS[row?.progress?.step ?? "start"] ?? "Working"}…</p>
          {row?.progress?.step === "checked" && <p className="muted small">{row.progress.detail} candidate versions built and checked so far.</p>}
          {row?.elapsed != null && <p className="muted small">{minutes(row.elapsed)} so far. You can close this panel; it keeps running.</p>}
          <div className="gen-actions"><button onClick={cancel}>Cancel</button></div>
        </>
      )}

      {stage === "result" && result && (
        <>
          <p className="muted small ws-about">{result.quality}</p>
          <label className="tpl-field">Name<input value={title} maxLength={120} onChange={(e) => setTitle(e.target.value)} /></label>
          <label className="tpl-field">It answers to<input value={kinds} onChange={(e) => setKinds(e.target.value)} /></label>
          <p className="small tpl-sub">Colours by role: {result.roles.map(humanise).join(", ")}</p>
          <p className="small tpl-sub">What you can vary</p>
          {result.knobs.length === 0 && <p className="muted small">No size, swap or removal survived the checks; only the colours.</p>}
          <ul className="tpl-knobs">
            {result.knobs.map((k: Knob) => (
              <li key={k.param} className={off.has(k.param) ? "off" : ""}>
                <label><input type="checkbox" checked={!off.has(k.param)} onChange={() => toggle(k.param)} /> <strong>{k.label}</strong></label>
                <div className="tpl-extremes">
                  {k.extremes.map((x) => (
                    <figure key={String(x.value)}>
                      {x.thumb ? <img src={x.thumb} alt={`${k.label}: ${x.text}`} loading="lazy" /> : <div className="tpl-nothumb" aria-hidden />}
                      <figcaption className="small">{x.text}</figcaption>
                    </figure>
                  ))}
                </div>
              </li>
            ))}
          </ul>
          {result.rejected.length > 0 && (
            <details className="small"><summary>Not offered ({result.rejected.length})</summary>
              <ul className="tpl-rejected">{result.rejected.map((r) => <li key={r.param}>{r.reason}</li>)}</ul></details>
          )}
          {result.source.author && <p className="muted small">Credit: {result.source.author}{result.source.license ? ` · ${result.source.license}` : ""}</p>}
          <div className="gen-actions"><button onClick={() => { remember(id, null); done(); }}>Discard</button><button className="primary" disabled={busy || !title.trim()} onClick={save}>{busy && <span className="spinner" aria-hidden />} Save template</button></div>
        </>
      )}

      {stage === "saved" && saved && (
        <>
          <p><strong>{saved.title}</strong> is saved. It is now offered next to the generators{saved.kinds.length ? ` and answers to “${saved.kinds.join("”, “")}”` : ""}.</p>
          <p className="muted small">Try “another {saved.kinds[0] ?? "one"}, but longer and in green” in the chat of a new build.</p>
          <div className="gen-actions"><button className="primary" onClick={done}>Done</button></div>
        </>
      )}

      {stage === "failed" && <div className="gen-actions"><button onClick={() => { remember(id, null); setJob(null); setStage("checking"); run(corrections, true); }}>Start again</button><button onClick={done}>Close</button></div>}
    </section>
  );
}
