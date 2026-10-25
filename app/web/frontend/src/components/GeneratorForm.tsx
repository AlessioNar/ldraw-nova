import { useEffect, useMemo, useRef, useState } from "react";
import { buildsApi, viewerUrl, type GeneratorEntry, type GeneratorList, type ParamSpec } from "../api";
import { colourHex } from "../ldrawColours";

const PREVIEW_DELAY_MS = 600;
export type Params = Record<string, unknown>;

export const humanise = (name: string) => name.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
const isColour = (spec: ParamSpec) => spec["x-colour"] === true;
const isInteger = (spec: ParamSpec) => spec.type === "integer" && spec.minimum != null && spec.maximum != null;

/** Each control comes from the parameter's JSON schema: sliders for bounded integers, selects for enums and colours
 *  (named as in generators/colours.py), checkboxes for yes/no flags and for lists of choices. */
export function Control({ name, spec, value, colours, onChange }: {
  name: string; spec: ParamSpec; value: unknown; colours: GeneratorList["colours"]; onChange: (v: unknown) => void;
}) {
  const id = `gen-${name}`;
  if (isColour(spec)) {
    const text = String(value ?? "");
    const known = colours.find((c) => c.name === text.toLowerCase() || String(c.code) === text);
    const options = known ? colours : [{ name: text, code: -1 }, ...colours];
    return (
      <div className="gen-colour">
        <i className="gen-swatch" style={{ background: known ? colourHex(known.code) : "#8a8a8a" }} aria-hidden />
        <select id={id} value={known?.name ?? text} onChange={(e) => onChange(e.target.value)}>
          {options.map((c) => <option key={c.name} value={c.name}>{humanise(c.name)}</option>)}
        </select>
      </div>
    );
  }
  if (spec.enum) {
    return (
      <select id={id} value={String(value)} onChange={(e) => onChange(typeof spec.enum![0] === "number" ? Number(e.target.value) : e.target.value)}>
        {spec.enum.map((o) => <option key={String(o)} value={String(o)}>{humanise(String(o))}</option>)}
      </select>
    );
  }
  if (spec.type === "boolean") {
    return <input id={id} type="checkbox" className="gen-check" checked={Boolean(value)} onChange={(e) => onChange(e.target.checked)} />;
  }
  if (spec.type === "array" && spec.items?.enum) {
    const chosen = new Set((value as string[]) ?? []);
    return (
      <div className="gen-multi" role="group" aria-label={humanise(name)}>
        {spec.items.enum.map((o) => (
          <label key={o}>
            <input type="checkbox" checked={chosen.has(o)} onChange={(e) => {
              const next = new Set(chosen);
              if (e.target.checked) next.add(o); else next.delete(o);
              onChange(spec.items!.enum!.filter((x) => next.has(x)));
            }} />
            {humanise(o)}
          </label>
        ))}
      </div>
    );
  }
  if (isInteger(spec)) {
    const step = spec["x-even"] ? 2 : 1;
    return (
      <div className="gen-slider">
        <input id={id} type="range" min={spec.minimum} max={spec.maximum} step={step} value={Number(value)} onChange={(e) => onChange(Number(e.target.value))} />
        <output className="mono" htmlFor={id}>{String(value)}</output>
      </div>
    );
  }
  return <input id={id} value={String(value ?? "")} onChange={(e) => onChange(e.target.value)} />;
}

/** The generator picker: one tile per generator in the registry. */
export function GeneratorPicker({ list, value, onPick }: { list: GeneratorEntry[]; value: string; onPick: (id: string) => void }) {
  return (
    <div className="gen-picker" role="radiogroup" aria-label="Type of model">
      {list.map((g) => (
        <button key={g.id} type="button" role="radio" aria-checked={g.id === value} className={`gen-tile${g.id === value ? " selected" : ""}`}
          onClick={() => onPick(g.id)} title={g.description}>
          <strong>{g.title}</strong>
          <span className="muted small">{g.families[0] ?? ""}</span>
        </button>
      ))}
    </div>
  );
}

/**
 * The settings form of one generator. Every change asks the build worker for a quick preview (debounced), shown
 * in an iframe beside the form (`inline`) or handed to the caller to show on its own stage (`onPreview`).
 */
export default function GeneratorForm({ generator, colours, initial, submitLabel, busy, error, onSubmit, onPreview, onCancel }: {
  generator: GeneratorEntry; colours: GeneratorList["colours"]; initial?: Params; submitLabel: string; busy?: boolean; error?: string | null;
  onSubmit: (params: Params) => void; onPreview?: (url: string | null) => void; onCancel?: () => void;
}) {
  const [params, setParams] = useState<Params>({ ...generator.defaults, ...initial });
  const [preview, setPreview] = useState<{ url: string | null; status: "idle" | "working" | "error"; message?: string }>({ url: null, status: "idle" });
  const seq = useRef(0);
  useEffect(() => setParams({ ...generator.defaults, ...initial }), [generator, initial]);

  useEffect(() => {
    const mine = ++seq.current;
    setPreview((p) => ({ ...p, status: "working" }));
    const timer = setTimeout(() => {
      buildsApi.preview(generator.id, params)
        .then((r) => {
          if (mine !== seq.current) return;
          setPreview({ url: r.model_url, status: "idle" });
          onPreview?.(r.model_url);
        })
        .catch((e: Error) => { if (mine === seq.current) setPreview((p) => ({ ...p, status: "error", message: e.message })); });
    }, PREVIEW_DELAY_MS);
    return () => clearTimeout(timer);
  }, [generator.id, params, onPreview]);
  useEffect(() => () => onPreview?.(null), [onPreview]);

  const entries = useMemo(() => Object.entries(generator.params.properties), [generator]);
  const plain = entries.filter(([, s]) => !isColour(s));
  const coloured = entries.filter(([, s]) => isColour(s));
  const set = (name: string) => (v: unknown) => setParams((p) => ({ ...p, [name]: v }));
  const field = ([name, spec]: [string, ParamSpec]) => (
    <div className={`gen-field${spec.type === "boolean" ? " inline" : ""}`} key={name}>
      <label htmlFor={`gen-${name}`} title={spec.description}>{humanise(name)}</label>
      <Control name={name} spec={spec} value={params[name] ?? spec.default} colours={colours} onChange={set(name)} />
      {spec.description && <span className="gen-hint muted">{spec.description}</span>}
    </div>
  );

  return (
    <div className={`gen-form${onPreview ? " no-preview" : ""}`}>
      <form onSubmit={(e) => { e.preventDefault(); onSubmit(params); }}>
        <p className="muted small gen-about">{generator.description}</p>
        <div className="gen-fields">{plain.map(field)}</div>
        {coloured.length > 0 && <>
          <div className="label gen-group">Colours</div>
          <div className="gen-fields">{coloured.map(field)}</div>
        </>}
        {error && <div className="banner error">{error}</div>}
        <div className="gen-actions">
          <button type="button" onClick={() => setParams({ ...generator.defaults })} title="Back to the generator's defaults">Reset</button>
          {onCancel && <button type="button" onClick={onCancel}>Cancel</button>}
          <button type="submit" className="primary" disabled={busy}>{busy && <span className="spinner" aria-hidden />} {submitLabel}</button>
        </div>
      </form>
      {!onPreview && (
        <div className="gen-preview" aria-live="polite">
          {preview.url
            ? <iframe key={preview.url} title="Live preview" src={viewerUrl(preview.url)} />
            : <div className="stage-empty"><div className="brick-row" aria-hidden="true"><i /><i /><i /><i /></div><p className="muted">Preview loads in a moment.</p></div>}
          <span className="gen-preview-status small">
            {preview.status === "working" && <><span className="spinner" aria-hidden /> Updating the preview…</>}
            {preview.status === "error" && <span className="warn-text">{preview.message}</span>}
          </span>
        </div>
      )}
    </div>
  );
}
