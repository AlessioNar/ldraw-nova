import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, buildsApi, inventoryApi, ORIGIN_LABEL, timeAgo, type BuildableModel, type BuildableReport, type BuildSource, type BuildSummary, type Inventory } from "../api";
import DescribeBox, { type DescribeOutcome } from "../components/DescribeBox";
import { useApp } from "../context";

const greeting = () => { const h = new Date().getHours(); return h < 5 ? "Good evening" : h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening"; };
const pct = (x: number) => `${Math.floor(x * 100)}%`;

/** The newest builds as big cards; the empty state doubles as the first-run welcome. */
function Continue({ builds }: { builds: BuildSummary[] | null }) {
  if (!builds) return <div className="wb-cards">{[0, 1, 2, 3].map((i) => <div key={i} className="wb-card skeleton" aria-hidden />)}</div>;
  if (!builds.length) return (
    <div className="panel wb-empty">
      <p><strong>Nothing here yet.</strong> Describe something above, or pick another way to start.</p>
      <div className="wb-links"><Link to="/new?way=type">Start from a type</Link><Link to="/new?way=import">Import a model file</Link><Link to="/new?way=empty">Empty editor</Link></div>
    </div>
  );
  return (
    <div className="wb-cards">
      {builds.slice(0, 8).map((b) => {
        const origin = b.latest_origin;
        return (
          <Link key={b.id} to={`/build/${b.id}`} className="wb-card" title={b.title}>
            <span className="wb-thumb">{b.latest?.image_url ? <img src={b.latest.image_url} alt="" loading="lazy" /> : <i aria-hidden>no preview yet</i>}</span>
            <span className="wb-meta">
              <strong className="ellipsis">{b.title}</strong>
              <span className="muted small">{b.versions > 0 ? `v${b.versions}` : "no version yet"} · {timeAgo(b.updated_at)}</span>
              {origin && ORIGIN_LABEL[origin] && <span className={`origin-badge origin-${origin}`}>{ORIGIN_LABEL[origin]}</span>}
            </span>
          </Link>
        );
      })}
    </div>
  );
}

/** Totals from the parts lists, plus a few bundled models they can build. The ranking may need warming: never block on it. */
function FromParts({ lists }: { lists: Inventory[] | null }) {
  const [top, setTop] = useState<BuildableModel[] | null>(null);
  const [warming, setWarming] = useState(false);
  const key = (lists ?? []).map((i) => i.id).join(",");

  useEffect(() => {
    if (!key) return;
    let dead = false, timer: ReturnType<typeof setTimeout>;
    const load = () => inventoryApi.buildable({ ids: key.split(","), limit: 6, min_parts: 20 }).then(async (r) => {
      if (dead) return;
      if ("results" in r) { setWarming(false); return setTop((r as BuildableReport).results); }
      setWarming(true);
      if (r.state === "idle") await inventoryApi.buildableWarm().catch(() => {});
      timer = setTimeout(load, 2000);
    }).catch(() => { if (!dead) { setWarming(false); setTop([]); } });
    load();
    return () => { dead = true; clearTimeout(timer); };
  }, [key]);

  if (!lists) return <div className="panel skeleton wb-parts-skel" aria-hidden />;
  if (!lists.length) return <div className="panel wb-parts"><p className="muted">Got a pile of bricks? <Link to="/parts">Add your parts</Link> and see what you can build with them.</p></div>;
  const pieces = lists.reduce((n, i) => n + i.total_pieces, 0);
  const full = (top ?? []).filter((m) => m.missing === 0);
  const review = lists.reduce((n, i) => n + (i.review_items ?? 0), 0);
  return (
    <div className="panel wb-parts">
      <p><strong>{pieces.toLocaleString()} pieces</strong> in {lists.length} parts list{lists.length === 1 ? "" : "s"}
        {review > 0 && <> · <Link to="/parts" className="warn-text">{review} colour famil{review === 1 ? "y" : "ies"} to review</Link></>}</p>
      {top === null || warming
        ? <p className="muted small">Checking what you can build…</p>
        : top.length > 0
          ? <p className="wb-buildable"><span className="muted">{full.length ? "You can build:" : "Closest:"}</span> {(full.length ? full : top).slice(0, 3).map((m) => (
            <span key={m.file} className="wb-model"><Link to="/parts?view=build" title={m.description || m.id}>{m.description || m.id}</Link>{!full.length && <span className="muted small"> {pct(m.completeness)}</span>}</span>))}</p>
          : <p className="muted small">Nothing to suggest yet. <Link to="/parts?view=build">What can I build?</Link></p>}
    </div>
  );
}

/** The home page: describe a build, pick up where you left off, see what your parts can do. */
export default function Home() {
  const { llms, defaultLlmId, refreshBuilds, refreshChats } = useApp();
  const navigate = useNavigate();
  const [builds, setBuilds] = useState<BuildSummary[] | null>(null);
  const [lists, setLists] = useState<Inventory[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    buildsApi.list().then((r) => setBuilds(r.builds)).catch(() => setBuilds([]));
    inventoryApi.list().then((r) => setLists(r.inventories)).catch(() => setLists([]));
  }, []);

  async function run(source: BuildSource, title: string, then: (id: string) => Promise<string>) {
    setBusy(true); setError(null);
    try {
      const created = await buildsApi.create(title, source);
      const to = await then(created.id);
      refreshBuilds(); refreshChats();
      navigate(to);
    } catch (e) {
      setError((e as Error).message);
      refreshBuilds();
    } finally { setBusy(false); }
  }

  async function onDescribed(outcome: DescribeOutcome, text: string) {
    if (outcome.kind === "generator") {
      const g = await buildsApi.generators().then((l) => l.generators.find((x) => x.id === outcome.generator)).catch(() => undefined);
      return run({ kind: "generator", generator: outcome.generator }, g?.title || text.slice(0, 40),
        async (id) => { await buildsApi.generate(id, outcome.generator, outcome.params); return `/build/${id}`; });
    }
    return run({ kind: "ai" }, text.slice(0, 60), async (id) => {
      const chat = await buildsApi.chat(id, defaultLlmId, true);
      await api.send(chat.id, text, defaultLlmId, { mode: "agent", permissions: "ask" });
      return `/chat/${chat.id}`;
    });
  }

  return (
    <div className="page workbench">
      <section className="wb-hero">
        <h1>{greeting()}</h1>
        {error && <div className="banner error">{error}</div>}
        <DescribeBox compact busy={busy} llmReady={llms.length > 0} submitLabel="Build" placeholder="Describe a new build, e.g. a small red car" onOutcome={onDescribed} />
        <div className="wb-links">
          <Link to="/new?way=type">Start from a type</Link><Link to="/new?way=import">Import</Link><Link to="/new?way=empty">Empty editor</Link>
        </div>
      </section>
      <section aria-labelledby="wb-continue">
        <h2 id="wb-continue" className="label">Continue</h2>
        <Continue builds={builds} />
      </section>
      <section aria-labelledby="wb-parts">
        <h2 id="wb-parts" className="label">From my parts</h2>
        <FromParts lists={lists} />
      </section>
    </div>
  );
}
