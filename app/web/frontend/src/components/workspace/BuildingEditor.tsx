import { useMemo, useState } from "react";
import type { GeneratorEntry, GeneratorList, ParamSpec } from "../../api";
import { Control, humanise, type Params } from "../GeneratorForm";

type Config = Record<string, Params>;

/** The grammar's component tree: Building → its groups, in build order. The groups come from the generator's schema
 *  (`x-component`); this only decides where each one sits in the tree. */
const TREE: { id: string }[] = [
  { id: "footprint" }, { id: "storeys" }, { id: "walls" }, { id: "openings" }, { id: "corners" }, { id: "roof" }, { id: "details" }, { id: "base" },
];

const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);

/** Keep only the grammar's own groups, each filled from the defaults. House-dialect keys (width, courses...) are
 *  dropped: the engine lets them override the groups, which would make edits here look ignored. */
export function toConfig(generator: GeneratorEntry, params: Params | undefined): Config {
  const out: Config = {};
  for (const [group, spec] of Object.entries(generator.params.properties)) {
    if (spec.type !== "object") continue;
    out[group] = { ...(generator.defaults[group] as Params), ...((params?.[group] as Params | undefined) ?? {}) };
  }
  return out;
}

/** Component tree + the form of the selected component + preset chips, for the building generator. */
export default function BuildingEditor({ generator, colours, initial, busy, onSubmit }: {
  generator: GeneratorEntry; colours: GeneratorList["colours"]; initial?: Params; busy?: boolean; onSubmit: (params: Params) => void;
}) {
  const [config, setConfig] = useState<Config>(() => toConfig(generator, initial));
  const [selected, setSelected] = useState("footprint");
  const defaults = useMemo(() => toConfig(generator, undefined), [generator]);
  const groups = generator.params.properties;
  const nodes = TREE.filter((n) => groups[n.id]);
  const spec = groups[selected];
  const presets = Object.entries(generator.presets ?? {});
  const changed = (group: string) => !same(config[group], defaults[group]);
  const set = (group: string, name: string) => (v: unknown) => setConfig((c) => ({ ...c, [group]: { ...c[group], [name]: v } }));

  return (
    <form className="bld-editor" onSubmit={(e) => { e.preventDefault(); onSubmit({ ...config }); }}>
      {presets.length > 0 && (
        <div className="bld-presets" role="group" aria-label="Presets">
          {presets.map(([id, p]) => (
            <button key={id} type="button" className="bld-chip" title={p.description} onClick={() => setConfig(toConfig(generator, p.config))}>{p.title}</button>
          ))}
        </div>
      )}
      <nav className="bld-tree" aria-label="Building components">
        <div className="bld-root label">Building</div>
        {nodes.map((n) => (
          <button key={n.id} type="button" className={`bld-node${n.id === selected ? " selected" : ""}`} aria-current={n.id === selected}
            onClick={() => setSelected(n.id)} title={groups[n.id].description}>
            {humanise(groups[n.id]["x-component"] ?? n.id)}
            {changed(n.id) && <i className="bld-dot" aria-label="changed from the defaults" />}
          </button>
        ))}
      </nav>
      {spec?.properties && (
        <fieldset className="bld-form">
          <legend className="label">{humanise(spec["x-component"] ?? selected)}</legend>
          {spec.description && <p className="muted small">{spec.description}</p>}
          <div className="bld-fields">
            {Object.entries(spec.properties).map(([name, field]: [string, ParamSpec]) => (
              <div className={`gen-field${field.type === "boolean" ? " inline" : ""}`} key={name}>
                <label htmlFor={`gen-${name}`} title={field.description}>{humanise(name)}</label>
                <Control name={name} spec={field} value={config[selected]?.[name] ?? field.default} colours={colours} onChange={set(selected, name)} />
                {field.description && <span className="gen-hint muted">{field.description}</span>}
              </div>
            ))}
          </div>
        </fieldset>
      )}
      <div className="gen-actions">
        <button type="button" onClick={() => setConfig(toConfig(generator, undefined))} title="Back to the grammar's defaults">Reset</button>
        <button type="submit" className="primary" disabled={busy}>{busy && <span className="spinner" aria-hidden />} Regenerate</button>
      </div>
    </form>
  );
}
