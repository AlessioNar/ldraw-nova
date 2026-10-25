import { useEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { buildsApi } from "../api";
import { useApp } from "../context";

/** /new has no screen of its own: it creates an empty build and opens it in the workspace. A prompt passed in
 *  ("Design something like this" on What can I build?) goes along and lands in the workspace chat's composer. */
export default function NewBuild() {
  const { refreshBuilds } = useApp();
  const navigate = useNavigate();
  const prompt = (useLocation().state as { prompt?: string } | null)?.prompt;
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const started = useRef(-1);      // one build per attempt, also when an effect runs twice (StrictMode)

  useEffect(() => {
    if (started.current === attempt) return;
    started.current = attempt;
    setError(null);
    buildsApi.create("Untitled build", { kind: "empty" })
      .then((b) => { refreshBuilds(); navigate(`/build/${b.id}`, { replace: true, state: prompt ? { prompt } : undefined }); })
      .catch((e: Error) => setError(e.message));
  }, [attempt, prompt, navigate, refreshBuilds]);

  return (
    <div className="page">
      {error
        ? <div className="banner error">Could not start a new build: {error} <button onClick={() => setAttempt((n) => n + 1)}>Try again</button> <Link to="/builds">My Builds</Link></div>
        : <p className="muted"><span className="spinner" aria-hidden /> Starting a new build…</p>}
    </div>
  );
}
