import { Suspense, lazy, useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, buildsApi, downloadGlb, downloadUrl, ORIGIN_LABEL, timeAgo, type BuildDetail, type BuildVersion, type GeneratorList } from "../api";
import DescribeBox, { type DescribeOutcome } from "../components/DescribeBox";
import GeneratorForm from "../components/GeneratorForm";
import { useApp, type ViewerTarget } from "../context";

const ViewerStage = lazy(() => import("../components/ViewerStage"));
const POLL_MS = 1500;
type Panel = "none" | "describe" | "settings";

const targetOf = (v: BuildVersion): ViewerTarget => ({ modelUrl: v.model_url!, title: v.title || v.file, parts: v.parts, version: v.version });

/** The build: its latest version on the 3D stage, the versions rail, and every way to work on it. */
export default function BuildPage() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const { llms, defaultLlmId, refreshBuilds, stageRef } = useApp();
  const [detail, setDetail] = useState<BuildDetail | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [picked, setPicked] = useState<number | null>(null);
  const [panel, setPanel] = useState<Panel>("none");
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [generators, setGenerators] = useState<GeneratorList | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [renaming, setRenaming] = useState<string | null>(null);
  const [glbBusy, setGlbBusy] = useState(false);

  const reload = useCallback(async () => {
    const d = await buildsApi.get(id);
    setDetail(d);
    return d;
  }, [id]);

  useEffect(() => {
    setDetail(null); setNotFound(false); setPicked(null); setPanel("none"); setError(null); setNotice(null);
    reload().catch(() => setNotFound(true));
  }, [id, reload]);

  // Snapshots and parts lists of new versions are still being made: refetch until they are done.
  const pending = detail?.pending ?? false;
  useEffect(() => {
    if (!pending) return;
    const timer = setTimeout(() => reload().then(refreshBuilds).catch(() => {}), POLL_MS);
    return () => clearTimeout(timer);
  }, [pending, detail, reload, refreshBuilds]);

  useEffect(() => {
    if (panel !== "settings" || generators) return;
    buildsApi.generators().then(setGenerators).catch((e: Error) => setError(e.message));
  }, [panel, generators]);

  const versions = useMemo(() => [...(detail?.versions ?? [])].reverse(), [detail]);
  const latest = versions[0];
  const shown = versions.find((v) => v.version === picked) ?? latest;
  const stageTarget = useMemo<ViewerTarget | null>(() => {
    if (panel === "settings" && previewUrl) return { modelUrl: previewUrl, title: "Preview of the new settings" };
    return shown?.model_url ? targetOf(shown) : null;
  }, [panel, previewUrl, shown]);

  // "View in 3D" anywhere shows the model on this stage.
  useEffect(() => {
    stageRef.current = (t) => { const v = detail?.versions.find((x) => x.model_url === t.modelUrl); if (v) setPicked(v.version); };
    return () => { stageRef.current = null; };
  }, [detail, stageRef]);

  async function act<T>(label: string, work: () => Promise<T>): Promise<T | undefined> {
    setBusy(label); setError(null); setNotice(null);
    try { return await work(); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(null); }
  }

  async function generate(generator: string, params: Record<string, unknown>, summary = "") {
    const made = await act("generate", () => buildsApi.generate(id, generator, params));
    if (!made) return;
    await reload(); refreshBuilds();
    setPicked(null); setPanel("none");
    setNotice(`Saved as v${made.version}.${summary ? ` ${summary}.` : ""}`);
  }

  async function askAi(text?: string) {
    const chat = await act("ai", async () => {
      const c = await buildsApi.chat(id, defaultLlmId, false);
      if (text) await api.send(c.id, text, defaultLlmId, { mode: "agent", permissions: "ask" });
      return c;
    });
    if (chat) { refreshBuilds(); navigate(`/chat/${chat.id}`); }
  }

  async function openEditor(file?: string) {
    const opened = await act("edit", () => (file ? api.editModel(file) : buildsApi.edit(id)));
    if (opened) navigate(`/build/${encodeURIComponent(id)}/edit`);
  }

  async function onDescribed(outcome: DescribeOutcome, text: string) {
    if (outcome.kind === "generator") return generate(outcome.generator, outcome.params, outcome.summary);
    return askAi(text);
  }

  async function rename() {
    const title = renaming?.trim();
    setRenaming(null);
    if (!title || title === detail?.build.title) return;
    const d = await act("rename", () => buildsApi.rename(id, title));
    if (d) { setDetail(d); refreshBuilds(); }
  }

  async function saveGlb(v: BuildVersion) {
    if (!v.model_url) return;
    setGlbBusy(true); setError(null);
    try { await downloadGlb(v.model_url, v.file); } catch (e) { setError((e as Error).message); } finally { setGlbBusy(false); }
  }

  if (notFound) return <div className="page"><p className="muted">This build doesn't exist (any more). <Link to="/new">Start a new one</Link>.</p></div>;
  if (!detail) return <div className="page"><p className="muted">Loading…</p></div>;

  const { build, source } = detail;
  const generator = source.generator ? generators?.generators.find((g) => g.id === source.generator) : undefined;
  const current = source.generator && source.params ? { generator: source.generator, params: source.params } : undefined;
  const toggle = (p: Panel) => { setPanel((cur) => (cur === p ? "none" : p)); setError(null); setNotice(null); };

  return (
    <div className="build-split">
      <div className="build-side">
        <header className="build-head">
          {renaming == null
            ? <h1 className="ellipsis" title="Click to rename" onClick={() => setRenaming(build.title)} tabIndex={0}
                onKeyDown={(e) => { if (e.key === "Enter") setRenaming(build.title); }}>{build.title}</h1>
            : <input className="build-rename" autoFocus value={renaming} maxLength={120} aria-label="Build name"
                onChange={(e) => setRenaming(e.target.value)} onBlur={rename}
                onKeyDown={(e) => { if (e.key === "Enter") rename(); if (e.key === "Escape") setRenaming(null); }} />}
          <p className="muted small">
            {build.versions} version{build.versions === 1 ? "" : "s"}
            {build.versions > 0 && <> · {latest && `${latest.parts != null ? latest.parts.toLocaleString() : "…"} parts`}</>}
            {" · updated "}{timeAgo(build.updated_at)}
          </p>
        </header>

        <div className="build-actions" role="group" aria-label="Work on this build">
          <button className={panel === "describe" ? "active" : ""} aria-pressed={panel === "describe"} onClick={() => toggle("describe")}>Describe a change</button>
          {source.generator && (
            <button className={panel === "settings" ? "active" : ""} aria-pressed={panel === "settings"} onClick={() => toggle("settings")}
              title="Reopen the generator's form with the settings of the last generated version">Change settings</button>
          )}
          <button onClick={() => openEditor()} disabled={busy === "edit"} title="Move, add or remove bricks by hand; saving makes the next version">
            {busy === "edit" && <span className="spinner" aria-hidden />} Edit in 3D
          </button>
          <button onClick={() => askAi()} disabled={busy === "ai"} title="Open a chat attached to this build; what the AI publishes becomes the next version">
            {busy === "ai" && <span className="spinner" aria-hidden />} Ask the AI
          </button>
        </div>
        {shown?.model_url && (
          <div className="build-downloads small">
            <span className="label">Download v{shown.version}</span>
            <a className="button" href={downloadUrl(shown.model_url)}>.{shown.file.split(".").pop()}</a>
            <button onClick={() => saveGlb(shown)} disabled={glbBusy}>{glbBusy && <span className="spinner" aria-hidden />} .glb</button>
            {shown.bom_url && <a className="button" href={downloadUrl(shown.bom_url)}>BOM</a>}
          </div>
        )}

        {error && <div className="banner error">{error}</div>}
        {notice && <div className="banner ok-banner" role="status">{notice}</div>}

        {panel === "describe" && (
          <section className="build-panel">
            <h3>Describe a change</h3>
            <DescribeBox key={id} busy={busy === "generate" || busy === "ai"} llmReady={llms.length > 0} submitLabel="Make the change" buildId={id} current={current}
              placeholder={source.generator ? "e.g. make the roof blue and add two windows" : "e.g. add a second door"} onOutcome={onDescribed} />
          </section>
        )}
        {panel === "settings" && (
          <section className="build-panel">
            <h3>Change settings</h3>
            {!detail.latest_from_generator && (
              <p className="muted small">The latest version was changed by hand or by the AI. Saving builds a fresh version from these settings; every earlier version is kept.</p>
            )}
            {generator && generators
              ? <GeneratorForm generator={generator} colours={generators.colours} initial={source.params} submitLabel="Save as next version"
                  busy={busy === "generate"} onSubmit={(params) => generate(generator.id, params)} onPreview={setPreviewUrl} onCancel={() => setPanel("none")} />
              : <p className="muted">Loading the form…</p>}
          </section>
        )}

        {build.versions === 0 && panel === "none" && (
          <p className="build-empty muted">No version yet. Describe it, change settings, or open the 3D editor to make v1.</p>
        )}

        {versions.length > 0 && (
          <section aria-label="Versions">
            <div className="section-label label">Versions</div>
            <ol className="versions-rail">
              {versions.map((v) => (
                <li key={v.version} className={`version-item${v.version === shown?.version ? " current" : ""}`}>
                  <button className="version-thumb" onClick={() => setPicked(v.version)} disabled={!v.model_url} title={`Show v${v.version} on the stage`}>
                    {v.image_url ? <img src={v.image_url} alt={`v${v.version}`} loading="lazy" />
                      : <span className="thumb-missing small">{v.status === "failed" ? "No preview" : <><span className="spinner" aria-hidden /> Rendering…</>}</span>}
                  </button>
                  <div className="version-meta">
                    <div className="version-line">
                      <span className="version-badge">v{v.version}{v.version === latest?.version ? " · latest" : ""}</span>
                      <span className={`origin-badge origin-${v.origin}`} title={`Made by: ${ORIGIN_LABEL[v.origin]}`}>{ORIGIN_LABEL[v.origin]}</span>
                    </div>
                    {v.version > 1 && v.changes
                      ? <p className="model-changes small"><strong>Changed since v{v.version - 1}:</strong> {v.changes.summary}</p>
                      : v.version > 1 && <p className="muted small">Changes appear once the parts list is ready.</p>}
                    <p className="muted small ellipsis">{v.parts != null ? `${v.parts.toLocaleString()} parts · ` : ""}{timeAgo(v.created_at)}
                      {v.chat && <> · <Link to={`/chat/${v.chat.id}`}>chat</Link></>}</p>
                    <div className="version-actions">
                      <button onClick={() => setPicked(v.version)} disabled={!v.model_url}>View</button>
                      <button onClick={() => openEditor(v.file)} disabled={!v.model_url || busy === "edit"} title="Edit this version in 3D; saving makes the next version">Edit</button>
                    </div>
                  </div>
                </li>
              ))}
            </ol>
          </section>
        )}

        {detail.chats.length > 0 && (
          <section aria-label="Chats">
            <div className="section-label label">Chats on this build</div>
            <ul className="build-chats">
              {detail.chats.map((c) => (
                <li key={c.id}>
                  <Link to={`/chat/${c.id}`} className="ellipsis">{c.running && <span className="live-dot" title="Working…" />} {c.title}</Link>
                  <span className="muted small">{timeAgo(c.updated_at)}</span>
                </li>
              ))}
            </ul>
          </section>
        )}
      </div>
      <Suspense fallback={<section className="chat-stage" aria-busy="true" />}>
        <ViewerStage editorPath={`/build/${id}/edit`} onOpenEditor={() => openEditor()} target={stageTarget}
          emptyText="Make the first version with Describe a change, Change settings or Edit in 3D, and it opens here." />
      </Suspense>
    </div>
  );
}
