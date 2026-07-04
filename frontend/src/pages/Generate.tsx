import { useState } from "react";
import { api } from "../api";
import type { GenerateResult } from "../types";

// ── Presets ───────────────────────────────────────────────────────────────────

const GLASS_PRESETS = ["TFT-LCD-G8", "OLED-G8.5", "TFT-LCD-G10", "AMOLED-G6", "LTPS-G6", "Mini-LED-G8"];
const WAFER_PRESETS = ["LOGIC-7NM", "LOGIC-5NM", "LOGIC-3NM", "DRAM-1ALPHA", "FLASH-3D-128L", "ANALOG-180NM"];

const CONDITION_LABELS: Record<string, string> = {
  control: "Control",
  treatment_a: "Treatment A",
  treatment_b: "Treatment B",
};

const EVENT_LABELS: Record<string, string> = {
  none: "None",
  pre_pm: "Pre-PM",
  post_pm: "Post-PM",
  recipe_change: "Recipe Change",
  tool_swap: "Tool Swap",
  excursion: "Excursion",
};

// ── Types ─────────────────────────────────────────────────────────────────────

interface RunRecord {
  id: number;
  result: GenerateResult;
  doeId: string;
  condition: string;
  eventTag: string;
  timestamp: Date;
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function pct(v: number | null) {
  return v == null ? "—" : `${(v * 100).toFixed(1)}%`;
}

function yieldColor(v: number | null) {
  if (v == null) return "bg-slate-700";
  if (v >= 0.5) return "bg-emerald-500";
  if (v >= 0.25) return "bg-amber-500";
  return "bg-red-500";
}

function avgYield(panels: GenerateResult["panels"]) {
  const vals = panels.map(p => p.yield_negbinom).filter((v): v is number => v != null);
  return vals.length ? vals.reduce((s, v) => s + v, 0) / vals.length : null;
}

// ── Sub-components ────────────────────────────────────────────────────────────

function Toggle({
  label, checked, onChange,
}: { label: string; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <label className="flex items-center gap-2 cursor-pointer select-none">
      <div
        className={`w-9 h-5 rounded-full transition-colors relative ${checked ? "bg-emerald-500" : "bg-slate-700"}`}
        onClick={() => onChange(!checked)}
      >
        <span className={`absolute top-0.5 left-0.5 w-4 h-4 bg-white rounded-full shadow transition-transform ${checked ? "translate-x-4" : ""}`} />
      </div>
      <span className="text-sm text-slate-300">{label}</span>
    </label>
  );
}

function ResultCard({ run, onRemove }: { run: RunRecord; onRemove: () => void }) {
  const [expanded, setExpanded] = useState(false);
  const avg = avgYield(run.result.panels);
  const excursions = run.result.panels.filter(p => p.clustering_class === "excursion").length;
  const lotId = run.result.panels[0]?.lot_id ?? "—";

  return (
    <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
      {/* Header */}
      <div className="px-5 py-4 flex items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-mono text-sm text-slate-100">{lotId}</span>
            <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${
              run.result.substrate_type === "wafer"
                ? "bg-sky-500/20 text-sky-400"
                : "bg-violet-500/20 text-violet-400"
            }`}>
              {run.result.substrate_type === "wafer" ? "Wafer" : "Glass Panel"}
            </span>
            {run.condition !== "none" && run.condition !== "" && (
              <span className="text-xs px-2 py-0.5 rounded-full bg-slate-800 text-slate-400">
                {CONDITION_LABELS[run.condition] ?? run.condition}
              </span>
            )}
            {run.eventTag !== "none" && run.eventTag !== "" && (
              <span className="text-xs px-2 py-0.5 rounded-full bg-amber-500/20 text-amber-400">
                {EVENT_LABELS[run.eventTag] ?? run.eventTag}
              </span>
            )}
            {run.doeId && (
              <span className="text-xs px-2 py-0.5 rounded-full bg-emerald-500/10 text-emerald-500 font-mono">
                {run.doeId}
              </span>
            )}
          </div>
          <div className="text-xs text-slate-500 mt-1">
            {run.timestamp.toLocaleTimeString()}
          </div>
        </div>
        <button onClick={onRemove} className="text-slate-600 hover:text-slate-400 text-lg leading-none shrink-0">×</button>
      </div>

      {/* Stats row */}
      <div className="px-5 pb-4 grid grid-cols-3 gap-3">
        <div>
          <div className="text-xs text-slate-500 uppercase tracking-wide">Panels</div>
          <div className="text-lg font-semibold text-slate-100">{run.result.n_panels}</div>
        </div>
        <div>
          <div className="text-xs text-slate-500 uppercase tracking-wide">Defects</div>
          <div className="text-lg font-semibold text-slate-100">{run.result.total_defects.toLocaleString()}</div>
        </div>
        <div>
          <div className="text-xs text-slate-500 uppercase tracking-wide">Avg Yield</div>
          <div className={`text-lg font-semibold ${
            avg == null ? "text-slate-500" :
            avg >= 0.5 ? "text-emerald-400" :
            avg >= 0.25 ? "text-amber-400" : "text-red-400"
          }`}>{pct(avg)}</div>
        </div>
      </div>

      {/* Yield bar */}
      {avg != null && (
        <div className="px-5 pb-4">
          <div className="h-1.5 bg-slate-800 rounded-full overflow-hidden">
            <div className={`h-full rounded-full transition-all ${yieldColor(avg)}`} style={{ width: `${avg * 100}%` }} />
          </div>
        </div>
      )}

      {/* Excursion alert */}
      {excursions > 0 && (
        <div className="mx-5 mb-4 px-3 py-2 bg-red-500/10 border border-red-500/30 rounded-lg text-red-400 text-xs">
          ⚠ {excursions} excursion panel{excursions > 1 ? "s" : ""} detected by DBSCAN clustering
        </div>
      )}

      {/* Panel breakdown toggle */}
      <div className="border-t border-slate-800">
        <button
          onClick={() => setExpanded(e => !e)}
          className="w-full px-5 py-2.5 text-left text-xs text-slate-500 hover:text-slate-300 flex items-center gap-1"
        >
          <span>{expanded ? "▲" : "▼"}</span>
          <span>Panel breakdown</span>
        </button>

        {expanded && (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-slate-500 border-t border-slate-800">
                  {["Panel ID", "Dies", "Defects", "Density", "Yield (NB)", "Clustering"].map(h => (
                    <th key={h} className="px-4 py-2 text-left font-medium uppercase tracking-wide whitespace-nowrap">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {run.result.panels.map(p => (
                  <tr key={p.panel_id} className="border-t border-slate-800/50 hover:bg-slate-800/30">
                    <td className="px-4 py-2 font-mono text-slate-300">{p.panel_id}</td>
                    <td className="px-4 py-2 text-slate-400">{p.active_dies}</td>
                    <td className="px-4 py-2 text-slate-400">{p.total_defects}</td>
                    <td className="px-4 py-2 text-slate-400">
                      {p.defect_density != null ? p.defect_density.toFixed(4) : "—"}
                    </td>
                    <td className="px-4 py-2">
                      <span className={
                        p.yield_negbinom == null ? "text-slate-500" :
                        p.yield_negbinom >= 0.5 ? "text-emerald-400" :
                        p.yield_negbinom >= 0.25 ? "text-amber-400" : "text-red-400"
                      }>
                        {pct(p.yield_negbinom)}
                      </span>
                    </td>
                    <td className="px-4 py-2">
                      {p.clustering_class ? (
                        <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${
                          p.clustering_class === "excursion" ? "bg-red-500/20 text-red-400" :
                          p.clustering_class === "systematic" ? "bg-amber-500/20 text-amber-400" :
                          "bg-emerald-500/20 text-emerald-400"
                        }`}>
                          {p.clustering_class}
                        </span>
                      ) : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function Generate() {
  const [substrate, setSubstrate] = useState<"glass_panel" | "wafer">("glass_panel");
  const [productType, setProductType] = useState("TFT-LCD-G8");
  const [nPanels, setNPanels] = useState(4);
  const [meanDefect, setMeanDefect] = useState(3.5);
  const [runYield, setRunYield] = useState(true);
  const [runClustering, setRunClustering] = useState(true);
  const [seed, setSeed] = useState<number | "">("");
  const [doeId, setDoeId] = useState("");
  const [condition, setCondition] = useState("control");
  const [eventTag, setEventTag] = useState("none");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [runs, setRuns] = useState<RunRecord[]>([]);
  const [nextId, setNextId] = useState(1);

  const presets = substrate === "glass_panel" ? GLASS_PRESETS : WAFER_PRESETS;

  async function handleGenerate() {
    setLoading(true);
    setError(null);
    try {
      const result = await api.generate({
        substrate_type: substrate,
        product_type: productType || undefined,
        n_panels: nPanels,
        mean_defect_count: meanDefect,
        run_yield: runYield,
        run_clustering: runClustering,
        seed: seed !== "" ? Number(seed) : undefined,
      });
      setRuns(prev => [{
        id: nextId,
        result,
        doeId,
        condition,
        eventTag,
        timestamp: new Date(),
      }, ...prev]);
      setNextId(n => n + 1);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Generation failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex h-full overflow-hidden">
      {/* ── Form panel ──────────────────────────────────────────────────────── */}
      <div className="w-96 shrink-0 border-r border-slate-800 overflow-y-auto flex flex-col">
        <div className="px-6 py-5 border-b border-slate-800">
          <h1 className="text-lg font-semibold text-slate-100">Generate Synthetic Data</h1>
          <p className="text-xs text-slate-500 mt-0.5">Create inspection lots with controlled parameters</p>
        </div>

        <div className="px-6 py-5 flex flex-col gap-6 flex-1">

          {/* Substrate toggle */}
          <div>
            <label className="text-xs font-semibold uppercase tracking-wide text-slate-500 block mb-2">
              Substrate Type
            </label>
            <div className="grid grid-cols-2 gap-2">
              {(["glass_panel", "wafer"] as const).map(s => (
                <button
                  key={s}
                  onClick={() => { setSubstrate(s); setProductType(s === "glass_panel" ? GLASS_PRESETS[0] : WAFER_PRESETS[0]); }}
                  className={`py-2.5 rounded-lg text-sm font-medium border transition-colors ${
                    substrate === s
                      ? "bg-emerald-500/20 border-emerald-500 text-emerald-400"
                      : "bg-slate-900 border-slate-700 text-slate-400 hover:border-slate-500"
                  }`}
                >
                  {s === "glass_panel" ? "Glass Panel" : "Wafer"}
                </button>
              ))}
            </div>
          </div>

          {/* Product type */}
          <div>
            <label className="text-xs font-semibold uppercase tracking-wide text-slate-500 block mb-2">
              Product Type
            </label>
            <input
              type="text"
              value={productType}
              onChange={e => setProductType(e.target.value)}
              className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-emerald-500"
              placeholder="e.g. TFT-LCD-G8"
            />
            <div className="flex flex-wrap gap-1.5 mt-2">
              {presets.map(p => (
                <button
                  key={p}
                  onClick={() => setProductType(p)}
                  className={`text-xs px-2.5 py-1 rounded-full border transition-colors ${
                    productType === p
                      ? "bg-emerald-500/20 border-emerald-500 text-emerald-400"
                      : "border-slate-700 text-slate-500 hover:border-slate-500 hover:text-slate-300"
                  }`}
                >
                  {p}
                </button>
              ))}
            </div>
          </div>

          {/* Panel count */}
          <div>
            <div className="flex justify-between mb-2">
              <label className="text-xs font-semibold uppercase tracking-wide text-slate-500">Panel Count</label>
              <span className="text-sm font-semibold text-slate-200">{nPanels}</span>
            </div>
            <input
              type="range" min={1} max={20} value={nPanels}
              onChange={e => setNPanels(Number(e.target.value))}
              className="w-full accent-emerald-500"
            />
            <div className="flex justify-between text-xs text-slate-600 mt-1"><span>1</span><span>20</span></div>
          </div>

          {/* Mean defect count */}
          <div>
            <div className="flex justify-between mb-2">
              <label className="text-xs font-semibold uppercase tracking-wide text-slate-500">Mean Defect Count (λ)</label>
              <span className="text-sm font-semibold text-slate-200">{meanDefect.toFixed(1)}</span>
            </div>
            <input
              type="range" min={0.5} max={15} step={0.5} value={meanDefect}
              onChange={e => setMeanDefect(Number(e.target.value))}
              className="w-full accent-emerald-500"
            />
            <div className="flex justify-between text-xs text-slate-600 mt-1"><span>0.5 (clean)</span><span>15.0 (excursion)</span></div>
          </div>

          {/* Toggles */}
          <div className="flex flex-col gap-3">
            <Toggle label="Compute Yield (Poisson / Murphy / NegBinom)" checked={runYield} onChange={setRunYield} />
            <Toggle label="Run DBSCAN Clustering" checked={runClustering} onChange={setRunClustering} />
          </div>

          {/* DOE section */}
          <div className="border-t border-slate-800 pt-5">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500 mb-4">DOE Context</h3>

            <div className="flex flex-col gap-4">
              <div>
                <label className="text-xs text-slate-400 block mb-1.5">Experiment ID</label>
                <input
                  type="text"
                  value={doeId}
                  onChange={e => setDoeId(e.target.value)}
                  placeholder="e.g. DOE-2024-PM-001"
                  className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-emerald-500 font-mono"
                />
              </div>

              <div>
                <label className="text-xs text-slate-400 block mb-1.5">Process Condition</label>
                <div className="grid grid-cols-3 gap-2">
                  {Object.entries(CONDITION_LABELS).map(([k, v]) => (
                    <button
                      key={k}
                      onClick={() => setCondition(k)}
                      className={`py-2 rounded-lg text-xs font-medium border transition-colors ${
                        condition === k
                          ? "bg-emerald-500/20 border-emerald-500 text-emerald-400"
                          : "bg-slate-900 border-slate-700 text-slate-400 hover:border-slate-500"
                      }`}
                    >
                      {v}
                    </button>
                  ))}
                </div>
              </div>

              <div>
                <label className="text-xs text-slate-400 block mb-1.5">Event Tag</label>
                <select
                  value={eventTag}
                  onChange={e => setEventTag(e.target.value)}
                  className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-emerald-500"
                >
                  {Object.entries(EVENT_LABELS).map(([k, v]) => (
                    <option key={k} value={k}>{v}</option>
                  ))}
                </select>
              </div>
            </div>
          </div>

          {/* Advanced */}
          <details className="border-t border-slate-800 pt-4">
            <summary className="text-xs font-semibold uppercase tracking-wide text-slate-500 cursor-pointer select-none">
              Advanced
            </summary>
            <div className="mt-3">
              <label className="text-xs text-slate-400 block mb-1.5">Random Seed</label>
              <input
                type="number"
                value={seed}
                onChange={e => setSeed(e.target.value === "" ? "" : Number(e.target.value))}
                placeholder="Leave blank for random"
                className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-emerald-500"
              />
            </div>
          </details>

        </div>

        {/* Generate button */}
        <div className="px-6 py-5 border-t border-slate-800">
          {error && (
            <div className="mb-3 text-xs text-red-400 bg-red-500/10 border border-red-500/30 rounded-lg px-3 py-2">
              {error}
            </div>
          )}
          <button
            onClick={handleGenerate}
            disabled={loading}
            className="w-full py-3 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 disabled:cursor-not-allowed text-white font-semibold rounded-lg transition-colors text-sm"
          >
            {loading ? "Generating…" : "Generate Lot"}
          </button>
        </div>
      </div>

      {/* ── Results panel ────────────────────────────────────────────────────── */}
      <div className="flex-1 overflow-y-auto p-6">
        {runs.length === 0 ? (
          <div className="h-full flex flex-col items-center justify-center text-center">
            <div className="text-4xl mb-4">⚗</div>
            <p className="text-slate-400 text-sm font-medium">No lots generated yet</p>
            <p className="text-slate-600 text-xs mt-1 max-w-xs">
              Configure parameters on the left and click Generate Lot. Results appear here and accumulate across runs.
            </p>
          </div>
        ) : (
          <div className="flex flex-col gap-4">
            <div className="flex items-center justify-between mb-2">
              <h2 className="text-sm font-semibold text-slate-300">{runs.length} lot{runs.length > 1 ? "s" : ""} generated this session</h2>
              <button
                onClick={() => setRuns([])}
                className="text-xs text-slate-600 hover:text-slate-400"
              >
                Clear all
              </button>
            </div>
            {runs.map(run => (
              <ResultCard
                key={run.id}
                run={run}
                onRemove={() => setRuns(prev => prev.filter(r => r.id !== run.id))}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
