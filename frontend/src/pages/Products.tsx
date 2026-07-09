import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import type { ProductSpec } from "../types";

// Glass panel generation presets (width × height in mm)
const PANEL_GENS: { label: string; w: number; h: number }[] = [
  { label: "Gen 4",   w: 730,  h: 920  },
  { label: "Gen 5",   w: 1100, h: 1300 },
  { label: "Gen 6",   w: 1500, h: 1850 },
  { label: "Gen 7",   w: 1870, h: 2200 },
  { label: "Gen 8",   w: 2160, h: 2460 },
  { label: "Gen 8.5", w: 2200, h: 2500 },
  { label: "Gen 10",  w: 2880, h: 3130 },
  { label: "Gen 10.5",w: 2940, h: 3370 },
];

const DISPLAY_TECHS = [
  "TFT-LCD (a-Si)", "TFT-LCD (LTPS)", "TFT-LCD (IGZO)",
  "OLED", "AMOLED", "QLED", "MicroLED",
];

const WAFER_DIAMETERS = [
  { label: '150 mm (6")',  value: "150" },
  { label: '200 mm (8")',  value: "200" },
  { label: '300 mm (12")', value: "300" },
  { label: '450 mm (18")', value: "450" },
];

const EMPTY_FORM = {
  spec_id:       "",
  product_name:  "",
  substrate_type:"wafer",
  die_width_mm:  "",
  die_height_mm: "",
  // wafer-only
  wafer_diameter_mm: "300",
  process_node_nm:   "",
  // glass-panel-only
  panel_width_mm:     "",
  panel_height_mm:    "",
  display_technology: "",
  // common yield params
  critical_area_fraction: "1.0",
  target_yield:           "0.80",
  alpha:                  "2.0",
  d0_target:              "",
  notes:                  "",
};

type FormState = typeof EMPTY_FORM;

export default function Products() {
  const navigate = useNavigate();
  const [specs,      setSpecs]      = useState<ProductSpec[]>([]);
  const [loading,    setLoading]    = useState(true);
  const [error,      setError]      = useState<string | null>(null);
  const [showForm,   setShowForm]   = useState(false);
  const [form,       setForm]       = useState<FormState>(EMPTY_FORM);
  const [submitting, setSubmitting] = useState(false);
  const [formError,  setFormError]  = useState<string | null>(null);

  const isPanel = form.substrate_type === "glass_panel";

  const load = () => {
    setLoading(true);
    api.products.list()
      .then(setSpecs)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false));
  };

  useEffect(() => { load(); }, []);

  const set = (name: string, value: string) =>
    setForm(f => ({ ...f, [name]: value }));

  const handleChange = (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) =>
    set(e.target.name, e.target.value);

  const applyGenPreset = (g: { w: number; h: number }) =>
    setForm(f => ({ ...f, panel_width_mm: String(g.w), panel_height_mm: String(g.h) }));

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setFormError(null);
    setSubmitting(true);
    try {
      await api.products.create({
        spec_id:       form.spec_id.trim(),
        product_name:  form.product_name.trim(),
        substrate_type:form.substrate_type,
        die_width_mm:  parseFloat(form.die_width_mm),
        die_height_mm: parseFloat(form.die_height_mm),
        // wafer-only — null when glass panel
        wafer_diameter_mm: !isPanel && form.wafer_diameter_mm ? parseFloat(form.wafer_diameter_mm) : null,
        process_node_nm:   !isPanel && form.process_node_nm   ? parseInt(form.process_node_nm)     : null,
        // glass-panel-only — null when wafer
        panel_width_mm:     isPanel && form.panel_width_mm    ? parseFloat(form.panel_width_mm)    : null,
        panel_height_mm:    isPanel && form.panel_height_mm   ? parseFloat(form.panel_height_mm)   : null,
        display_technology: isPanel && form.display_technology ? form.display_technology            : null,
        // common
        critical_area_fraction: parseFloat(form.critical_area_fraction),
        target_yield:           parseFloat(form.target_yield),
        alpha:                  parseFloat(form.alpha),
        d0_target:              form.d0_target ? parseFloat(form.d0_target) : null,
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
    try { await api.products.delete(id); load(); }
    catch (err: unknown) { alert(err instanceof Error ? err.message : "Delete failed"); }
  };

  const pct = (v: number) => `${(v * 100).toFixed(1)}%`;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">Product Specs</h1>
          <p className="text-slate-400 text-sm mt-0.5">
            Define die/cell dimensions and yield targets for semiconductor wafers and glass panel displays.
          </p>
        </div>
        <button
          onClick={() => setShowForm(v => !v)}
          className="bg-emerald-500 hover:bg-emerald-400 text-slate-900 font-semibold text-sm px-4 py-2 rounded-md transition-colors"
        >
          {showForm ? "Cancel" : "+ New Spec"}
        </button>
      </div>

      {/* ── Create form ── */}
      {showForm && (
        <div className="bg-slate-900 border border-slate-800 rounded-xl p-6">
          <h2 className="text-white font-semibold mb-5">New Product Specification</h2>
          {formError && (
            <div className="mb-4 p-3 bg-red-900/30 border border-red-700 rounded text-red-400 text-sm">
              {formError}
            </div>
          )}
          <form onSubmit={handleSubmit} className="space-y-6">

            {/* Identity */}
            <Section label="Identity">
              <div className="grid grid-cols-2 gap-4">
                <Field label="Spec ID *" name="spec_id" value={form.spec_id} onChange={handleChange}
                  placeholder={isPanel ? "e.g. G8-OLED-2024" : "e.g. N3-7x7"} required />
                <Field label="Product Name *" name="product_name" value={form.product_name} onChange={handleChange}
                  placeholder={isPanel ? 'e.g. 65″ OLED Gen 8.5' : "e.g. Flagship SoC N3"} required />
              </div>
              {/* Substrate type toggle */}
              <div>
                <label className="block text-slate-400 text-xs mb-2">Substrate Type</label>
                <div className="flex gap-1 bg-slate-800 border border-slate-700 rounded-lg p-1 w-fit">
                  {([["wafer", "Semiconductor Wafer"], ["glass_panel", "Glass Panel (Display)"]] as const).map(([val, lbl]) => (
                    <button key={val} type="button" onClick={() => set("substrate_type", val)}
                      className={`px-4 py-1.5 rounded-md text-xs font-medium transition-colors ${
                        form.substrate_type === val ? "bg-emerald-600 text-white" : "text-slate-400 hover:text-white"
                      }`}>
                      {lbl}
                    </button>
                  ))}
                </div>
              </div>
            </Section>

            {/* Substrate-specific fields */}
            {!isPanel ? (
              <Section label="Wafer Properties">
                <div>
                  <label className="block text-slate-400 text-xs mb-2">Wafer Diameter</label>
                  <div className="flex gap-1.5 flex-wrap">
                    {WAFER_DIAMETERS.map(d => (
                      <button key={d.value} type="button" onClick={() => set("wafer_diameter_mm", d.value)}
                        className={`px-3 py-1.5 rounded-md text-xs font-medium border transition-colors ${
                          form.wafer_diameter_mm === d.value
                            ? "bg-emerald-600 border-emerald-600 text-white"
                            : "border-slate-700 text-slate-400 hover:border-slate-500 hover:text-white"
                        }`}>
                        {d.label}
                      </button>
                    ))}
                  </div>
                </div>
                <div className="grid grid-cols-3 gap-4">
                  <Field label="Process Node (nm)" name="process_node_nm" value={form.process_node_nm}
                    onChange={handleChange} placeholder="e.g. 3" type="number" />
                  <Field label="Die Width (mm) *" name="die_width_mm" value={form.die_width_mm}
                    onChange={handleChange} placeholder="e.g. 7.5" type="number" step="0.001" required />
                  <Field label="Die Height (mm) *" name="die_height_mm" value={form.die_height_mm}
                    onChange={handleChange} placeholder="e.g. 7.5" type="number" step="0.001" required />
                </div>
              </Section>
            ) : (
              <Section label="Glass Panel Properties">
                <div>
                  <label className="block text-slate-400 text-xs mb-2">Generation Preset</label>
                  <div className="flex gap-1.5 flex-wrap">
                    {PANEL_GENS.map(g => (
                      <button key={g.label} type="button" onClick={() => applyGenPreset(g)}
                        className={`px-3 py-1.5 rounded-md text-xs font-medium border transition-colors ${
                          form.panel_width_mm === String(g.w) && form.panel_height_mm === String(g.h)
                            ? "bg-emerald-600 border-emerald-600 text-white"
                            : "border-slate-700 text-slate-400 hover:border-slate-500 hover:text-white"
                        }`}>
                        {g.label}
                      </button>
                    ))}
                  </div>
                  {form.panel_width_mm && form.panel_height_mm && (
                    <p className="text-slate-500 text-xs mt-1.5">
                      {form.panel_width_mm} × {form.panel_height_mm} mm
                    </p>
                  )}
                </div>
                <div className="grid grid-cols-2 gap-4">
                  <Field label="Panel Width (mm) *" name="panel_width_mm" value={form.panel_width_mm}
                    onChange={handleChange} placeholder="e.g. 2200" type="number" required />
                  <Field label="Panel Height (mm) *" name="panel_height_mm" value={form.panel_height_mm}
                    onChange={handleChange} placeholder="e.g. 2500" type="number" required />
                </div>
                <div className="grid grid-cols-3 gap-4">
                  <div>
                    <label className="block text-slate-400 text-xs mb-1">Display Technology</label>
                    <select name="display_technology" value={form.display_technology} onChange={handleChange}
                      className="w-full bg-slate-800 border border-slate-700 rounded px-3 py-1.5 text-white text-sm focus:outline-none focus:border-emerald-500">
                      <option value="">— select —</option>
                      {DISPLAY_TECHS.map(t => <option key={t} value={t}>{t}</option>)}
                    </select>
                  </div>
                  <Field label="Cell Width (mm) *" name="die_width_mm" value={form.die_width_mm}
                    onChange={handleChange} placeholder="e.g. 290" type="number" step="0.01" required />
                  <Field label="Cell Height (mm) *" name="die_height_mm" value={form.die_height_mm}
                    onChange={handleChange} placeholder="e.g. 165" type="number" step="0.01" required />
                </div>
              </Section>
            )}

            {/* Yield targets */}
            <Section label="Yield Targets">
              <div className="grid grid-cols-2 gap-4">
                <Field label="Target Yield (0–1)" name="target_yield" value={form.target_yield}
                  onChange={handleChange} type="number" step="0.01" />
                <Field label="D₀ Target (defects/cm²)" name="d0_target" value={form.d0_target}
                  onChange={handleChange} placeholder="e.g. 0.12" type="number" step="0.001" />
                <Field label="Critical Area Fraction (0–1)" name="critical_area_fraction"
                  value={form.critical_area_fraction} onChange={handleChange} type="number" step="0.01" />
                <Field label="Alpha (NegBinom clustering)" name="alpha" value={form.alpha}
                  onChange={handleChange} type="number" step="0.1" />
              </div>
            </Section>

            <div>
              <label className="block text-slate-400 text-xs mb-1">Notes</label>
              <textarea name="notes" value={form.notes} onChange={handleChange} rows={2}
                className="w-full bg-slate-800 border border-slate-700 rounded px-3 py-1.5 text-white text-sm resize-none focus:outline-none focus:border-emerald-500"
                placeholder="Optional notes…" />
            </div>

            <div className="flex gap-3">
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

      {/* ── Spec table ── */}
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
                <Th>Type</Th>
                <Th>Substrate Size</Th>
                <Th>Die / Cell (mm)</Th>
                <Th>Target Y</Th>
                <Th>D₀ Target</Th>
                <Th>Alpha</Th>
                <Th></Th>
              </tr>
            </thead>
            <tbody>
              {specs.map(s => (
                <tr key={s.spec_id} className="border-b border-slate-800/50 hover:bg-slate-900/30 transition-colors">
                  <td className="py-3 pr-4 font-mono text-emerald-400">{s.spec_id}</td>
                  <td className="py-3 pr-4 text-white">
                    {s.product_name}
                    {s.process_node_nm != null && (
                      <span className="ml-1.5 text-xs text-slate-500">{s.process_node_nm} nm</span>
                    )}
                    {s.display_technology && (
                      <span className="ml-1.5 text-xs text-slate-500">{s.display_technology}</span>
                    )}
                  </td>
                  <td className="py-3 pr-4">
                    <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${
                      s.substrate_type === "glass_panel"
                        ? "bg-blue-900/50 text-blue-300"
                        : "bg-slate-800 text-slate-300"
                    }`}>
                      {s.substrate_type === "glass_panel" ? "Glass" : "Wafer"}
                    </span>
                  </td>
                  <td className="py-3 pr-4 text-slate-400 text-xs font-mono">
                    {s.substrate_type === "glass_panel"
                      ? (s.panel_width_mm != null && s.panel_height_mm != null
                          ? `${s.panel_width_mm} × ${s.panel_height_mm}`
                          : "—")
                      : (s.wafer_diameter_mm != null ? `⌀ ${s.wafer_diameter_mm}` : "—")
                    }
                  </td>
                  <td className="py-3 pr-4 text-slate-300 font-mono text-xs">
                    {s.die_width_mm.toFixed(1)} × {s.die_height_mm.toFixed(1)}
                    <span className="text-slate-500 ml-1">({s.die_area_mm2.toFixed(1)} mm²)</span>
                  </td>
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

function Section({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="space-y-3">
      <p className="text-xs font-semibold uppercase tracking-wider text-slate-500 border-b border-slate-800 pb-1">
        {label}
      </p>
      {children}
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
