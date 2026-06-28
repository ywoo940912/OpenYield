import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import type { ProductSpec } from "../types";

const EMPTY_FORM = {
  spec_id: "",
  product_name: "",
  substrate_type: "wafer",
  die_width_mm: "",
  die_height_mm: "",
  wafer_diameter_mm: "300",
  critical_area_fraction: "1.0",
  target_yield: "0.80",
  alpha: "2.0",
  d0_target: "",
  process_node_nm: "",
  notes: "",
};

type FormState = typeof EMPTY_FORM;

export default function Products() {
  const navigate = useNavigate();
  const [specs, setSpecs] = useState<ProductSpec[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const load = () => {
    setLoading(true);
    api.products
      .list()
      .then(setSpecs)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  };

  useEffect(() => { load(); }, []);

  const handleChange = (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) => {
    setForm((f) => ({ ...f, [e.target.name]: e.target.value }));
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setFormError(null);
    setSubmitting(true);
    try {
      await api.products.create({
        spec_id:                form.spec_id.trim(),
        product_name:           form.product_name.trim(),
        substrate_type:         form.substrate_type,
        die_width_mm:           parseFloat(form.die_width_mm),
        die_height_mm:          parseFloat(form.die_height_mm),
        wafer_diameter_mm:      parseFloat(form.wafer_diameter_mm),
        critical_area_fraction: parseFloat(form.critical_area_fraction),
        target_yield:           parseFloat(form.target_yield),
        alpha:                  parseFloat(form.alpha),
        d0_target:              form.d0_target ? parseFloat(form.d0_target) : null,
        process_node_nm:        form.process_node_nm ? parseInt(form.process_node_nm) : null,
        notes:                  form.notes.trim(),
      });
      setForm(EMPTY_FORM);
      setShowForm(false);
      load();
    } catch (err: unknown) {
      setFormError(err instanceof Error ? err.message : "Unknown error");
    } finally {
      setSubmitting(false);
    }
  };

  const handleDelete = async (id: string) => {
    if (!confirm(`Delete spec "${id}"?`)) return;
    try {
      await api.products.delete(id);
      load();
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : "Delete failed");
    }
  };

  const pct = (v: number) => `${(v * 100).toFixed(1)}%`;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">Product Specs</h1>
          <p className="text-slate-400 text-sm mt-0.5">
            Define die dimensions, target yield, and D₀ for use across all yield tools and simulators.
          </p>
        </div>
        <button
          onClick={() => setShowForm((v) => !v)}
          className="bg-emerald-500 hover:bg-emerald-400 text-slate-900 font-semibold text-sm px-4 py-2 rounded-md transition-colors"
        >
          {showForm ? "Cancel" : "+ New Spec"}
        </button>
      </div>

      {/* Create form */}
      {showForm && (
        <div className="bg-slate-900 border border-slate-800 rounded-lg p-5">
          <h2 className="text-white font-semibold mb-4">New Product Specification</h2>
          {formError && (
            <div className="mb-4 p-3 bg-red-900/30 border border-red-700 rounded text-red-400 text-sm">
              {formError}
            </div>
          )}
          <form onSubmit={handleSubmit}>
            <div className="grid grid-cols-2 gap-4">
              <Field label="Spec ID *" name="spec_id" value={form.spec_id} onChange={handleChange}
                placeholder="e.g. N3-7x7" required />
              <Field label="Product Name *" name="product_name" value={form.product_name} onChange={handleChange}
                placeholder="e.g. Flagship SoC N3" required />

              <div>
                <label className="block text-slate-400 text-xs mb-1">Substrate Type</label>
                <select name="substrate_type" value={form.substrate_type} onChange={handleChange}
                  className="w-full bg-slate-800 border border-slate-700 rounded px-3 py-1.5 text-white text-sm">
                  <option value="wafer">Wafer</option>
                  <option value="panel">Panel</option>
                </select>
              </div>

              <Field label="Process Node (nm)" name="process_node_nm" value={form.process_node_nm}
                onChange={handleChange} placeholder="e.g. 3" type="number" />

              <Field label="Die Width (mm) *" name="die_width_mm" value={form.die_width_mm}
                onChange={handleChange} placeholder="e.g. 7.5" type="number" step="0.001" required />
              <Field label="Die Height (mm) *" name="die_height_mm" value={form.die_height_mm}
                onChange={handleChange} placeholder="e.g. 7.5" type="number" step="0.001" required />

              <Field label="Wafer Diameter (mm)" name="wafer_diameter_mm" value={form.wafer_diameter_mm}
                onChange={handleChange} type="number" />
              <Field label="Critical Area Fraction (0–1)" name="critical_area_fraction"
                value={form.critical_area_fraction} onChange={handleChange} type="number" step="0.01" />

              <Field label="Target Yield (0–1)" name="target_yield" value={form.target_yield}
                onChange={handleChange} type="number" step="0.01" />
              <Field label="Alpha (NegBinom clustering)" name="alpha" value={form.alpha}
                onChange={handleChange} type="number" step="0.1" />

              <Field label="D₀ Target (defects/cm²)" name="d0_target" value={form.d0_target}
                onChange={handleChange} placeholder="e.g. 0.12" type="number" step="0.001" />

              <div className="col-span-2">
                <label className="block text-slate-400 text-xs mb-1">Notes</label>
                <textarea name="notes" value={form.notes} onChange={handleChange} rows={2}
                  className="w-full bg-slate-800 border border-slate-700 rounded px-3 py-1.5 text-white text-sm resize-none"
                  placeholder="Optional notes..." />
              </div>
            </div>

            <div className="mt-4 flex gap-3">
              <button type="submit" disabled={submitting}
                className="bg-emerald-500 hover:bg-emerald-400 disabled:opacity-50 text-slate-900 font-semibold text-sm px-5 py-2 rounded-md transition-colors">
                {submitting ? "Saving…" : "Create Spec"}
              </button>
              <button type="button" onClick={() => { setShowForm(false); setFormError(null); }}
                className="text-slate-400 hover:text-slate-200 text-sm px-4 py-2 rounded-md transition-colors">
                Cancel
              </button>
            </div>
          </form>
        </div>
      )}

      {/* Spec table */}
      {loading ? (
        <div className="text-slate-500 text-sm">Loading…</div>
      ) : error ? (
        <div className="text-red-400 text-sm">{error}</div>
      ) : specs.length === 0 ? (
        <div className="text-slate-500 text-sm">
          No product specs yet. Click "+ New Spec" to define your first process target.
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-slate-800 text-slate-400 text-left">
                <Th>Spec ID</Th>
                <Th>Product</Th>
                <Th>Node</Th>
                <Th>Die (mm²)</Th>
                <Th>CA</Th>
                <Th>Target Y</Th>
                <Th>D₀ Target</Th>
                <Th>Alpha</Th>
                <Th></Th>
              </tr>
            </thead>
            <tbody>
              {specs.map((s) => (
                <tr key={s.spec_id} className="border-b border-slate-800/50 hover:bg-slate-900/30 transition-colors">
                  <td className="py-3 pr-4 font-mono text-emerald-400">{s.spec_id}</td>
                  <td className="py-3 pr-4 text-white">{s.product_name}</td>
                  <td className="py-3 pr-4 text-slate-300">{s.process_node_nm ? `${s.process_node_nm} nm` : "—"}</td>
                  <td className="py-3 pr-4 text-slate-300">
                    {s.die_width_mm.toFixed(2)} × {s.die_height_mm.toFixed(2)}
                    <span className="text-slate-500 text-xs ml-1">({s.die_area_mm2.toFixed(2)})</span>
                  </td>
                  <td className="py-3 pr-4 text-slate-300">{pct(s.critical_area_fraction)}</td>
                  <td className="py-3 pr-4 text-slate-300">{pct(s.target_yield)}</td>
                  <td className="py-3 pr-4 text-slate-300">{s.d0_target != null ? s.d0_target.toFixed(3) : "—"}</td>
                  <td className="py-3 pr-4 text-slate-300">{s.alpha.toFixed(1)}</td>
                  <td className="py-3 text-right space-x-3">
                    <button
                      onClick={() => navigate(`/simulator?spec=${s.spec_id}`)}
                      className="text-emerald-400 hover:text-emerald-300 text-xs font-medium transition-colors"
                    >
                      Run Simulation
                    </button>
                    <button
                      onClick={() => handleDelete(s.spec_id)}
                      className="text-slate-500 hover:text-red-400 text-xs transition-colors"
                    >
                      Delete
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function Field({
  label, name, value, onChange, placeholder, type = "text", step, required,
}: {
  label: string; name: string; value: string;
  onChange: (e: React.ChangeEvent<HTMLInputElement>) => void;
  placeholder?: string; type?: string; step?: string; required?: boolean;
}) {
  return (
    <div>
      <label className="block text-slate-400 text-xs mb-1">{label}</label>
      <input
        name={name} value={value} onChange={onChange}
        placeholder={placeholder} type={type} step={step} required={required}
        className="w-full bg-slate-800 border border-slate-700 rounded px-3 py-1.5 text-white text-sm
                   focus:outline-none focus:border-emerald-500 transition-colors"
      />
    </div>
  );
}

function Th({ children }: { children: React.ReactNode }) {
  return <th className="pb-3 pr-4 font-medium text-xs uppercase tracking-wider">{children}</th>;
}
