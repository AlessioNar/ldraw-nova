import { useEffect, useState } from "react";
import { buildsApi, estimateLabel, type Describe } from "../api";

export type DescribeOutcome =
  | { kind: "generator"; generator: string; params: Record<string, unknown>; summary: string }
  | { kind: "ai" };

type Step =
  | { step: "idle" }
  | { step: "thinking" }
  | { step: "clarify"; question: string; options: string[] }
  | { step: "ai"; estimate: string | null; note: string };

/**
 * "Describe it": plain text in, the cheapest way that works out. The decision engine (/api/describe) picks a
 * generator or an edit of the current one without any AI. When it cannot, the box ASKS before a paid AI build,
 * with the estimate. Without the engine (HTTP 404) only "Build with AI" is offered.
 */
export default function DescribeBox({ initialText = "", examples = [], busy, llmReady, submitLabel, placeholder, current, buildId, onOutcome }: {
  initialText?: string; examples?: string[]; busy?: boolean; llmReady: boolean; submitLabel: string; placeholder?: string;
  current?: { generator: string; params: Record<string, unknown> }; buildId?: string;
  onOutcome: (outcome: DescribeOutcome, text: string) => void | Promise<void>;
}) {
  const [text, setText] = useState(initialText);
  const [state, setState] = useState<Step>({ step: "idle" });
  const [error, setError] = useState<string | null>(null);
  useEffect(() => setText(initialText), [initialText]);
  useEffect(() => setState({ step: "idle" }), [text]);

  const [hint, setHint] = useState("");
  const generatorId = current?.generator;
  useEffect(() => {      // rules only, a millisecond: a soft hint under the box while typing
    setHint("");
    if (text.trim().length < 3) return;
    const timer = setTimeout(() => buildsApi.hint(text, generatorId).then((h) => setHint(h.confidence >= 0.7 ? h.hint : h.hint && `Probably: ${h.hint}`)).catch(() => {}), 350);
    return () => clearTimeout(timer);
  }, [text, generatorId]);

  async function askAi(note: string, known?: Describe["estimate"]) {
    let label = estimateLabel(known);
    if (!label) label = await buildsApi.hint(text, generatorId).then((h) => estimateLabel(h.estimate)).catch(() => null);
    setState({ step: "ai", estimate: label, note });
  }

  async function submit() {
    const value = text.trim();
    if (!value) return;
    setError(null);
    setState({ step: "thinking" });
    let answer: Describe;
    try {
      answer = await buildsApi.describe(value, buildId, current);
    } catch (e) {
      if ([404, 405].includes((e as { status?: number }).status ?? 0)) return askAi("The fast path is not installed here, so only the AI can build this.");
      setState({ step: "idle" });
      setError((e as Error).message);
      return;
    }
    const generator = answer.generator ?? (answer.route === "edit" ? current?.generator : undefined);
    if (answer.history) {
      setState({ step: "idle" });
      return setError(`"${answer.history}" is done from the versions list: pick the version you want and edit or view it.`);
    }
    if ((answer.route === "generator" || answer.route === "edit") && generator) {
      setState({ step: "idle" });
      const changed = (answer.changes ?? []).map((c) => `${c.label}: ${String(c.from)} to ${String(c.to)}`);
      const summary = [...changed, ...(answer.notes ?? [])].join("; ");
      return onOutcome({ kind: "generator", generator, params: answer.params ?? {}, summary }, value);
    }
    if (answer.route === "clarify") {
      return setState({ step: "clarify", question: answer.question ?? answer.message ?? "Can you say a little more?", options: answer.options ?? [] });
    }
    return askAi(answer.reason ?? answer.message ?? "A generator cannot do this one.", answer.estimate);
  }

  const working = busy || state.step === "thinking";
  return (
    <div className="describe-box">
      <textarea value={text} rows={3} aria-label="Describe your model" placeholder={placeholder ?? "e.g. A 6 x 8 cottage with a door, two windows and a sloped roof"}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); submit(); } }} />
      {examples.length > 0 && !text && (
        <div className="describe-examples">{examples.map((ex) => <button key={ex} type="button" className="chip" onClick={() => setText(ex)}>{ex}</button>)}</div>
      )}
      {hint && state.step === "idle" && <p className="muted small describe-hint">{hint}</p>}
      {error && <div className="banner error">{error}</div>}
      {state.step === "clarify" && (
        <div className="banner describe-ask" role="status">
          <strong>{state.question}</strong>
          {state.options.length > 0 && <div className="describe-examples">{state.options.map((o) => (
            <button key={o} type="button" className="chip" onClick={() => setText((t) => `${t.trim()} (${o})`)}>{o}</button>))}</div>}
        </div>
      )}
      {state.step === "ai" && (
        <div className="banner describe-ask" role="alertdialog" aria-label="Build with AI?">
          <p>{state.note}</p>
          <strong>Build with AI{state.estimate ? ` for ${state.estimate}` : ""}?</strong>
          {!llmReady && <p className="warn-text small">Add a model in Settings first.</p>}
          <div className="gen-actions">
            <button type="button" onClick={() => setState({ step: "idle" })}>Cancel</button>
            <button type="button" className="primary" disabled={working || !llmReady} onClick={() => onOutcome({ kind: "ai" }, text.trim())}>
              {working && <span className="spinner" aria-hidden />} Build with AI
            </button>
          </div>
        </div>
      )}
      {state.step !== "ai" && (
        <div className="gen-actions">
          <button type="button" className="primary" disabled={!text.trim() || working} onClick={submit}>
            {working && <span className="spinner" aria-hidden />} {submitLabel}
          </button>
        </div>
      )}
    </div>
  );
}
