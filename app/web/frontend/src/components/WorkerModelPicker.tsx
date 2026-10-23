import { useApp } from "../context";
import ModelPicker, { priceLabel } from "./ModelPicker";

/** The model delegated workers run on, with its catalogue prices so a wrong price is visible. "No workers" = none. */
export default function WorkerModelPicker({ value, onChange, disabled }: { value: string | null; onChange: (id: string | null) => void; disabled: boolean }) {
  const { llms } = useApp();
  const worker = llms.find(m => m.id === value);
  return <span className="worker-picker" title="Model that cheap delegated workers run on (needs Full access)">
    <ModelPicker value={value} onChange={id => onChange(id || null)} disabled={disabled || !llms.length} emptyLabel="No workers" label="Workers" />
    {worker && <small className="muted worker-price">{priceLabel(worker)}</small>}
  </span>;
}
