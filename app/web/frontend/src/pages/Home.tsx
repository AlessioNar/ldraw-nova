import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { inventoryApi, setsApi, timeAgo, type Inventory, type OwnedSet } from "../api";
import FirstRun from "../components/FirstRun";
import Logo from "../components/Logo";
import { useApp } from "../context";

/** The home screen is a menu: five tiles, each with at most one small live line. */
export default function Home() {
  const { builds, buildsLoaded } = useApp();
  const [lists, setLists] = useState<Inventory[] | null>(null);
  useEffect(() => { inventoryApi.list().then((r) => setLists(r.inventories)).catch(() => setLists([])); }, []);

  const [sets, setSets] = useState<OwnedSet[] | null>(null);
  useEffect(() => { setsApi.list().then((r) => setSets(r.sets)).catch(() => setSets([])); }, []);

  const last = buildsLoaded ? [...builds].sort((a, b) => b.updated_at - a.updated_at)[0] : undefined;
  const buildsLine = !buildsLoaded ? "" : builds.length === 0 ? "No builds yet" : `${builds.length} build${builds.length === 1 ? "" : "s"}${last ? ` · last: ${last.title} ${timeAgo(last.updated_at)}` : ""}`;
  const pieces = (lists ?? []).reduce((n, i) => n + i.total_pieces, 0);
  const review = (lists ?? []).reduce((n, i) => n + (i.review_items ?? 0), 0);
  const partsLine = lists === null ? "" : lists.length === 0 ? "No parts lists yet" : `${pieces.toLocaleString()} pieces${review > 0 ? ` · ${review} to review` : ""}`;

  const setsLine = sets === null ? "" : sets.length === 0 ? "No sets yet" : `${sets.length} set${sets.length === 1 ? "" : "s"} · ${sets.filter((x) => x.state === "built").length} built`;

  const tiles = [
    { to: "/new", title: "New build", line: "Open the 3D editor on an empty build", icon: "+", primary: true },
    { to: "/builds", title: "My Builds", line: buildsLine, icon: "▣" },
    { to: "/parts", title: "My Parts", line: partsLine, icon: "▤" },
    { to: "/sets", title: "My Sets", line: setsLine, icon: "▦" },
    { to: "/settings", title: "Settings", line: "Models, keys and budget", icon: "⚙" },
  ];

  return (
    <div className="page home-menu">
      <div className="home-menu-inner">
        <div className="home-brand">
          <Logo className="home-logo" />
          <h1>Open Brick Designer</h1>
        </div>
        <FirstRun hasParts={lists === null ? null : lists.length > 0} hasBuilds={buildsLoaded ? builds.length > 0 : null} />
        <nav className="tiles" aria-label="Home">
          {tiles.map((t) => (
            <Link key={t.to} to={t.to} className={`tile${t.primary ? " primary-tile" : ""}`}>
              <span className="tile-icon" aria-hidden>{t.icon}</span>
              <strong>{t.title}</strong>
              <span className="tile-line muted small ellipsis">{t.line || " "}</span>
            </Link>
          ))}
        </nav>
      </div>
    </div>
  );
}
