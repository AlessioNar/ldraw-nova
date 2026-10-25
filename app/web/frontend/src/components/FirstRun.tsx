import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";

const DISMISS_KEY = "obd.firstRun.dismissed";

const readDismissed = () => { try { return localStorage.getItem(DISMISS_KEY) === "1"; } catch { return false; } };

type Props = { hasParts: boolean | null; hasBuilds: boolean | null };

/** A short checklist on Home: add a key, add parts, first build. It hides itself once all three are done or dismissed. */
export default function FirstRun({ hasParts, hasBuilds }: Props) {
  const [hasKey, setHasKey] = useState<boolean | null>(null);
  const [dismissed, setDismissed] = useState(readDismissed);
  useEffect(() => { api.providerKeys().then((r) => setHasKey(Object.values(r.keys ?? {}).some(Boolean))).catch(() => setHasKey(null)); }, []);

  if (dismissed || hasKey === null || hasParts === null || hasBuilds === null) return null;
  const steps = [
    { done: hasKey, to: "/settings", title: "Add a model key", line: "Paste an API key so the assistant can answer. Editing by hand works without one." },
    { done: hasParts, to: "/parts", title: "Add your parts", line: "Import a set number or a parts list so builds match what you own." },
    { done: hasBuilds, to: "/new", title: "Start your first build", line: "Open the 3D editor and place a few bricks, or ask the assistant." },
  ];
  if (steps.every((s) => s.done)) return null;
  const dismiss = () => { setDismissed(true); try { localStorage.setItem(DISMISS_KEY, "1"); } catch { /* storage unavailable */ } };
  const next = steps.findIndex((s) => !s.done);

  return (
    <section className="first-run" aria-label="Getting started">
      <header>
        <strong>Getting started</strong>
        <span className="muted small">{steps.filter((s) => s.done).length} of {steps.length} done</span>
        <button type="button" className="first-run-dismiss" onClick={dismiss} aria-label="Dismiss getting started">×</button>
      </header>
      <ol>
        {steps.map((s, i) => (
          <li key={s.to} className={`${s.done ? "done" : ""}${i === next ? " next" : ""}`}>
            <span className="first-run-mark" aria-hidden>{s.done ? "✓" : i + 1}</span>
            <div>
              <strong>{s.title}</strong>
              <span className="muted small">{s.line}</span>
            </div>
            {!s.done && <Link to={s.to} className={`button${i === next ? " primary" : ""}`}>{i === next ? "Do this" : "Open"}</Link>}
          </li>
        ))}
      </ol>
    </section>
  );
}
