import { useEffect, useRef, useState } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { api, buildsApi, type BuildSource, type GeneratorList } from "../api";
import DescribeBox, { type DescribeOutcome } from "../components/DescribeBox";
import GeneratorForm, { GeneratorPicker } from "../components/GeneratorForm";
import { useApp } from "../context";

type Way = "describe" | "type" | "import" | "empty";
const WAYS: { id: Way; title: string; hint: string }[] = [
  { id: "describe", title: "Describe it", hint: "Say what you want. Simple models come straight from a generator; the AI only if needed." },
  { id: "type", title: "Start from a type", hint: "Pick a house, car or spaceship and tune its settings with a live preview." },
  { id: "import", title: "Import a model file", hint: "Bring an .mpd or .ldr you already have." },
  { id: "empty", title: "Empty", hint: "Open the 3D editor on an empty plan and place bricks yourself." },
];

const EXAMPLES = [
  "A 6 x 8 cottage with a door, two windows and a sloped roof",
  "Build a small red car with four black wheels",
  "Design a harbour crane with a long boom and a cargo dock",
];

/** The start of every journey: a new build. The four ways below all create the build first, then land on its page. */
export default function NewBuild() {
  const { llms, defaultLlmId, refreshBuilds, refreshChats } = useApp();
  const navigate = useNavigate();
  const prompt = (useLocation().state as { prompt?: string } | null)?.prompt ?? "";   // "Design something like this" on What can I build?
  const wayParam = useSearchParams()[0].get("way");     // the Workbench links here with a tile preselected
  const [way, setWay] = useState<Way>(WAYS.find((w) => w.id === wayParam)?.id ?? "describe");
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [list, setList] = useState<GeneratorList | null>(null);
  const [genId, setGenId] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);

  useEffect(() => {
    if (way !== "type" || list) return;
    buildsApi.generators().then((l) => { setList(l); setGenId((g) => g || l.generators.find((x) => x.id === "house")?.id || l.generators[0]?.id || ""); })
      .catch((e: Error) => setError(e.message));
  }, [way, list]);

  async function run(source: BuildSource, fallbackTitle: string, then: (id: string) => Promise<string>) {
    setBusy(true); setError(null);
    try {
      const created = await buildsApi.create(title.trim() || fallbackTitle, source);
      const to = await then(created.id);
      refreshBuilds(); refreshChats();
      navigate(to);
    } catch (e) {
      setError((e as Error).message);
      refreshBuilds();
    } finally { setBusy(false); }
  }

  const fromGenerator = (generator: string, params: Record<string, unknown>, label: string) =>
    run({ kind: "generator", generator }, label, async (id) => { await buildsApi.generate(id, generator, params); return `/build/${id}`; });

  async function onDescribed(outcome: DescribeOutcome, text: string) {
    if (outcome.kind === "generator") {
      const g = list?.generators.find((x) => x.id === outcome.generator) ?? (await buildsApi.generators().then((l) => (setList(l), l.generators.find((x) => x.id === outcome.generator))).catch(() => undefined));
      return fromGenerator(outcome.generator, outcome.params, title.trim() || g?.title || text.slice(0, 40));
    }
    return run({ kind: "ai" }, text.slice(0, 60), async (id) => {
      const chat = await buildsApi.chat(id, defaultLlmId, true);
      await api.send(chat.id, text, defaultLlmId, { mode: "agent", permissions: "ask" });
      return `/build/${id}?tab=chat`;
    });
  }

  const generator = list?.generators.find((g) => g.id === genId);

  return (
    <div className="page new-build">
      <div className="home-hero">
        <div className="brick-row" aria-hidden="true"><i /><i /><i /><i /></div>
        <h1>New build</h1>
        <p className="muted">A build is a model with versions. Start it any of these ways; chats, the 3D editor and generators all work on the same build afterwards.</p>
      </div>
      <div className="build-title-row">
        <label className="label" htmlFor="build-title">Name</label>
        <input id="build-title" value={title} maxLength={80} placeholder="Optional, e.g. Red cottage" onChange={(e) => setTitle(e.target.value)} />
      </div>
      <div className="ways" role="tablist" aria-label="How to start">
        {WAYS.map((w) => (
          <button key={w.id} role="tab" aria-selected={way === w.id} className={`way${way === w.id ? " selected" : ""}`} onClick={() => { setWay(w.id); setError(null); }}>
            <strong>{w.title}</strong>
            <span className="muted small">{w.hint}</span>
          </button>
        ))}
      </div>
      {error && <div className="banner error">{error}</div>}
      <section className="way-panel" role="tabpanel">
        {way === "describe" && (
          <DescribeBox initialText={prompt} examples={EXAMPLES} busy={busy} llmReady={llms.length > 0} submitLabel="Build it" onOutcome={onDescribed} />
        )}
        {way === "type" && (!list ? <p className="muted">Loading the generators…</p> : generator && (
          <>
            <GeneratorPicker list={list.generators} value={genId} onPick={setGenId} />
            <GeneratorForm generator={generator} colours={list.colours} submitLabel="Create build" busy={busy}
              onSubmit={(params) => fromGenerator(generator.id, params, generator.title)} />
          </>
        ))}
        {way === "import" && (
          <div className="import-panel">
            <p className="muted">Choose an LDraw model (.mpd or .ldr). It becomes v1 of a new build.</p>
            <input ref={fileRef} type="file" accept=".mpd,.ldr" aria-label="Model file" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
            <div><button className="primary" disabled={!file || busy} onClick={() => file && run({ kind: "import", filename: file.name }, file.name.replace(/\.[^.]+$/, ""), async (id) => {
              await buildsApi.importFile(id, file.name, await file.text());
              return `/build/${id}`;
            })}>{busy && <span className="spinner" aria-hidden />} Import</button></div>
          </div>
        )}
        {way === "empty" && (
          <div className="import-panel">
            <p className="muted">Start with an empty plan in the 3D editor. Saving it creates v1.</p>
            <div><button className="primary" disabled={busy} onClick={() => run({ kind: "empty" }, "Untitled build", async (id) => { await buildsApi.edit(id); return `/build/${id}/edit`; })}>
              {busy && <span className="spinner" aria-hidden />} Open the 3D editor
            </button></div>
          </div>
        )}
      </section>
    </div>
  );
}
