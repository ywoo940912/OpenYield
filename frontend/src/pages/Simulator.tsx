import { useState, useEffect } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../api";
import type { ProductSpec, MonteCarloResult, LearningCurveResult } from "../types";

type Tab = "monte-carlo" | "learning-curve";
type LCModel = "linear" | "exponential" | "d0_learning";

export default function Simulator() {
  const [searchParams] = useSearchParams();
  const initialSpec = searchParams.get("spec") ?? "";

  const [specs, setSpecs] = useState<ProductSpec[]>([]);
  const [tab, setTab] = useState<Tab>("monte-carlo");

  // Monte Carlo params
  const [mcSpecId, setMcSpecId] = useState(initialSpec);
  const [mcD0, setMcD0] = useState("0.12");
  const [mcDieArea, setMcDieArea] = useState("56.25");
  const [mcWaferDiam, setMcWaferDiam] = useState("300");
  const [mcRuns, setMcRuns] = useState("2000");
  const [mcCaf, setMcCaf] = useState("1.0");
  const [mcAlpha, setMcAlpha] = useState("2.0");
  const [mcUseSpec, setMcUseSpec] = useState(!!initialSpec);
  const [mcResult, setMcResult] = useState<MonteCarloResult | null>(null);
  const [mcLoading, setMcLoading] = useState(false);
  const [mcError, setMcError] = useState<string | null>(null);

  // Learning curve params
  const [lcModel, setLcModel] = useState<LCModel>("exponential");
  const [lcCurrent, setLcCurrent] = useState("0.55");
  const [lcTarget, setLcTarget] = useState("0.80");
  const [lcRate, setLcRate] = useState("0.05");
  const [lcYmax, setLcYmax] = useState("0.98");
  const [lcMonths, setLcMonths] = useState("24");
  const [lcDieArea, setLcDieArea] = useState("56.25");
  const [lcD0, setLcD0] = useState("0.80");
  const [lcResult, setLcResult] = useState<LearningCurveResult | null>(null);
  const [lcLoading, setLcLoading] = useState(false);
  const [lcError, setLcError] = useState<string | null>(null);

  useEffect(() => {
    api.products.list().then(setSpecs).catch(() => {});
  }, []);

  // When a spec is selected for MC, populate fields from spec
  useEffect(() => {
    if (!mcUseSpec || !mcSpecId) return;
    const spec = specs.find((s) => s.spec_id === mcSpecId);
    if (!spec) return;
    setMcDieArea(String(spec.die_area_mm2));
    setMcWaferDiam(String(spec.wafer_diameter_mm));
    setMcCaf(String(spec.critical_area_fraction));
    setMcAlpha(String(spec.alpha));
    if (spec.d0_target != null) setMcD0(String(spec.d0_target));
  }, [mcSpecId, mcUseSpec, specs]);

  const runMC = async () => {
    setMcError(null);
    setMcLoading(true);
    try {
      let result: MonteCarloResult;
      if (mcUseSpec && mcSpecId) {
        result = await api.simulate.monteCarloFromSpec(mcSpecId, parseInt(mcRuns));
      } else {
        result = await api.simulate.monteCarlo({
          d0: parseFloat(mcD0),
          die_area_mm2: parseFloat(mcDieArea),
          wafer_diameter_mm: parseFloat(mcWaferDiam),
          n_runs: parseInt(mcRuns),
          critical_area_fraction: parseFloat(mcCaf),
          alpha: parseFloat(mcAlpha),
        });
      }
      setMcResult(result);
    } catch (e: unknown) {
      setMcError(e instanceof Error ? e.message : "Simulation failed");
    } finally {
      setMcLoading(false);
    }
  };

  const runLC = async () => {
    setLcError(null);
    setLcLoading(true);
    try {
      const result = await api.simulate.learningCurve({
        current_yield: parseFloat(lcCurrent),
        target_yield: parseFloat(lcTarget),
        model: lcModel,
        improvement_rate: parseFloat(lcRate),
        y_max: parseFloat(lcYmax),
        n_months: parseInt(lcMonths),
        ...(lcModel === "d0_learning"
          ? { die_area_mm2: parseFloat(lcDieArea), initial_d0: parseFloat(lcD0) }
          : {}),
      });
      setLcResult(result);
    } catch (e: unknown) {
      setLcError(e instanceof Error ? e.message : "Simulation failed");
    } finally {
      setLcLoading(false);
    }
  };

  const pct = (v: number) => `${(v * 100).toFixed(2)}%`;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-white">Yield Simulator</h1>
        <p className="text-slate-400 text-sm mt-0.5">
          Monte Carlo distributions and learning curve projections for your process.
        </p>
      </div>

      {/* Tab selector */}
      <div className="flex gap-1 bg-slate-900 border border-slate-800 rounded-lg p-1 w-fit">
        {(["monte-carlo", "learning-curve"] as Tab[]).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`px-4 py-1.5 rounded text-sm font-medium transition-colors ${
              tab === t
                ? "bg-emerald-500/20 text-emerald-400"
                : "text-slate-400 hover:text-slate-200"
            }`}
          >
            {t === "monte-carlo" ? "Monte Carlo" : "Learning Curve"}
          </button>
        ))}
      </div>

      {/* ── Monte Carlo ── */}
      {tab === "monte-carlo" && (
        <div className="grid grid-cols-3 gap-6">
          {/* Params panel */}
          <div className="col-span-1 bg-slate-900 border border-slate-800 rounded-lg p-5 space-y-4">
            <h2 className="text-white font-semibold text-sm">Parameters</h2>

            <label className="flex items-center gap-2 text-sm text-slate-300 cursor-pointer">
              <input type="checkbox" checked={mcUseSpec} onChange={(e) => setMcUseSpec(e.target.checked)}
                className="accent-emerald-500" />
              Use product spec
            </label>

            {mcUseSpec && (
              <div>
                <label className="block text-slate-400 text-xs mb-1">Spec</label>
                <select value={mcSpecId} onChange={(e) => setMcSpecId(e.target.value)}
                  className="w-full bg-slate-800 border border-slate-700 rounded px-3 py-1.5 text-white text-sm">
                  <option value="">— select —</option>
                  {specs.map((s) => (
                    <option key={s.spec_id} value={s.spec_id}>{s.spec_id} · {s.product_name}</option>
                  ))}
                </select>
                {mcSpecId && specs.find((s) => s.spec_id === mcSpecId)?.d0_target == null && (
                  <p className="text-amber-400 text-xs mt-1">
                    This spec has no D₀ target. Set one via Products page or enter D₀ manually.
                  </p>
                )}
              </div>
            )}

            <SimField label="D₀ (defects/cm²)" value={mcD0} onChange={setMcD0} step="0.001" />
            {!mcUseSpec && (
              <>
                <SimField label="Die area (mm²)" value={mcDieArea} onChange={setMcDieArea} step="0.01" />
                <SimField label="Wafer diameter (mm)" value={mcWaferDiam} onChange={setMcWaferDiam} />
                <SimField label="Critical area fraction" value={mcCaf} onChange={setMcCaf} step="0.01" />
                <SimField label="Alpha" value={mcAlpha} onChange={setMcAlpha} step="0.1" />
              </>
            )}
            <SimField label="Runs" value={mcRuns} onChange={setMcRuns} step="100" />

            <button onClick={runMC} disabled={mcLoading}
              className="w-full bg-emerald-500 hover:bg-emerald-400 disabled:opacity-50 text-slate-900
                         font-semibold text-sm py-2 rounded-md transition-colors">
              {mcLoading ? "Running…" : "Run Simulation"}
            </button>
            {mcError && <p className="text-red-400 text-xs">{mcError}</p>}
          </div>

          {/* Results */}
          <div className="col-span-2 space-y-4">
            {mcResult ? (
              <>
                {/* Stats grid */}
                <div className="grid grid-cols-4 gap-3">
                  <StatCard label="Mean Yield" value={pct(mcResult.mean_yield)} accent />
                  <StatCard label="Std Dev" value={pct(mcResult.std_yield)} />
                  <StatCard label="P10" value={pct(mcResult.p10_yield)} />
                  <StatCard label="P90" value={pct(mcResult.p90_yield)} />
                </div>

                {/* Model comparison */}
                <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
                  <h3 className="text-slate-300 text-xs font-semibold uppercase tracking-wider mb-3">
                    Model Comparison
                  </h3>
                  <div className="grid grid-cols-3 gap-4 text-sm">
                    <div>
                      <p className="text-slate-500 text-xs">Monte Carlo (mean)</p>
                      <p className="text-white font-mono">{pct(mcResult.mean_yield)}</p>
                    </div>
                    <div>
                      <p className="text-slate-500 text-xs">Poisson</p>
                      <p className="text-white font-mono">{pct(mcResult.poisson_yield)}</p>
                    </div>
                    <div>
                      <p className="text-slate-500 text-xs">Murphy</p>
                      <p className="text-white font-mono">{pct(mcResult.murphy_yield)}</p>
                    </div>
                    <div>
                      <p className="text-slate-500 text-xs">Neg. Binomial</p>
                      <p className="text-white font-mono">{pct(mcResult.negbinom_yield)}</p>
                    </div>
                    <div>
                      <p className="text-slate-500 text-xs">Dies per wafer</p>
                      <p className="text-white font-mono">{mcResult.n_dies_per_wafer}</p>
                    </div>
                    <div>
                      <p className="text-slate-500 text-xs">Runs</p>
                      <p className="text-white font-mono">{mcResult.n_runs.toLocaleString()}</p>
                    </div>
                  </div>
                </div>

                {/* Sensitivity */}
                <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
                  <h3 className="text-slate-300 text-xs font-semibold uppercase tracking-wider mb-3">
                    Sensitivity — D₀ ±20 %
                  </h3>
                  <div className="flex gap-6 text-sm">
                    <div>
                      <p className="text-slate-500 text-xs">D₀ × 0.8 (improved)</p>
                      <p className="text-emerald-400 font-mono font-semibold">{pct(mcResult.yield_d0_minus20)}</p>
                    </div>
                    <div>
                      <p className="text-slate-500 text-xs">Baseline</p>
                      <p className="text-white font-mono">{pct(mcResult.mean_yield)}</p>
                    </div>
                    <div>
                      <p className="text-slate-500 text-xs">D₀ × 1.2 (degraded)</p>
                      <p className="text-red-400 font-mono">{pct(mcResult.yield_d0_plus20)}</p>
                    </div>
                    <div>
                      <p className="text-slate-500 text-xs">Δ yield from 20 % D₀ reduction</p>
                      <p className="text-emerald-400 font-mono font-semibold">
                        +{pct(mcResult.yield_d0_minus20 - mcResult.mean_yield)}
                      </p>
                    </div>
                  </div>
                </div>

                {/* Histogram */}
                <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
                  <h3 className="text-slate-300 text-xs font-semibold uppercase tracking-wider mb-3">
                    Yield Distribution ({mcResult.n_runs.toLocaleString()} runs)
                  </h3>
                  <Histogram bins={mcResult.histogram} />
                </div>
              </>
            ) : (
              <div className="flex items-center justify-center h-48 text-slate-600 text-sm">
                Configure parameters and click Run Simulation.
              </div>
            )}
          </div>
        </div>
      )}

      {/* ── Learning Curve ── */}
      {tab === "learning-curve" && (
        <div className="grid grid-cols-3 gap-6">
          {/* Params panel */}
          <div className="col-span-1 bg-slate-900 border border-slate-800 rounded-lg p-5 space-y-4">
            <h2 className="text-white font-semibold text-sm">Parameters</h2>

            <div>
              <label className="block text-slate-400 text-xs mb-1">Model</label>
              <select value={lcModel} onChange={(e) => setLcModel(e.target.value as LCModel)}
                className="w-full bg-slate-800 border border-slate-700 rounded px-3 py-1.5 text-white text-sm">
                <option value="linear">Linear</option>
                <option value="exponential">Exponential gap closure</option>
                <option value="d0_learning">D₀ learning (physical)</option>
              </select>
              <p className="text-slate-500 text-xs mt-1">
                {lcModel === "linear" && "Constant pp/month gain until ceiling."}
                {lcModel === "exponential" && "Diminishing returns as yield approaches Y_max."}
                {lcModel === "d0_learning" && "D₀ decays exponentially; yield follows Poisson model."}
              </p>
            </div>

            <SimField label="Current yield (0–1)" value={lcCurrent} onChange={setLcCurrent} step="0.01" />
            <SimField label="Target yield (0–1)" value={lcTarget} onChange={setLcTarget} step="0.01" />
            <SimField
              label={
                lcModel === "linear"
                  ? "Rate (pp / month)"
                  : lcModel === "exponential"
                  ? "Rate (gap fraction / month)"
                  : "D₀ decay rate / month"
              }
              value={lcRate}
              onChange={setLcRate}
              step="0.01"
            />
            <SimField label="Yield ceiling (Y_max)" value={lcYmax} onChange={setLcYmax} step="0.01" />
            <SimField label="Projection months" value={lcMonths} onChange={setLcMonths} />

            {lcModel === "d0_learning" && (
              <>
                <SimField label="Die area (mm²)" value={lcDieArea} onChange={setLcDieArea} step="0.01" />
                <SimField label="Initial D₀ (defects/cm²)" value={lcD0} onChange={setLcD0} step="0.001" />
              </>
            )}

            <button onClick={runLC} disabled={lcLoading}
              className="w-full bg-emerald-500 hover:bg-emerald-400 disabled:opacity-50 text-slate-900
                         font-semibold text-sm py-2 rounded-md transition-colors">
              {lcLoading ? "Running…" : "Project Learning Curve"}
            </button>
            {lcError && <p className="text-red-400 text-xs">{lcError}</p>}
          </div>

          {/* Results */}
          <div className="col-span-2 space-y-4">
            {lcResult ? (
              <>
                {/* Summary */}
                <div className="grid grid-cols-3 gap-3">
                  <StatCard label="Months to Target" accent
                    value={lcResult.months_to_target != null
                      ? `${lcResult.months_to_target} mo`
                      : "Not reached"} />
                  <StatCard label="Current Yield" value={`${(lcResult.current_yield * 100).toFixed(1)}%`} />
                  <StatCard label="Target Yield" value={`${(lcResult.target_yield * 100).toFixed(1)}%`} />
                </div>

                {lcResult.final_d0 != null && (
                  <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
                    <h3 className="text-slate-300 text-xs font-semibold uppercase tracking-wider mb-2">
                      D₀ Trajectory
                    </h3>
                    <div className="flex gap-6 text-sm">
                      <div>
                        <p className="text-slate-500 text-xs">Initial D₀</p>
                        <p className="text-white font-mono">{lcResult.initial_d0?.toFixed(4)}</p>
                      </div>
                      <div>
                        <p className="text-slate-500 text-xs">Final D₀ (month {lcResult.projected.length - 1})</p>
                        <p className="text-emerald-400 font-mono">{lcResult.final_d0.toFixed(4)}</p>
                      </div>
                      <div>
                        <p className="text-slate-500 text-xs">Reduction</p>
                        <p className="text-emerald-400 font-mono">
                          {lcResult.initial_d0
                            ? `-${(((lcResult.initial_d0 - lcResult.final_d0) / lcResult.initial_d0) * 100).toFixed(1)}%`
                            : "—"}
                        </p>
                      </div>
                    </div>
                  </div>
                )}

                {/* Chart */}
                <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
                  <h3 className="text-slate-300 text-xs font-semibold uppercase tracking-wider mb-3">
                    Yield Projection ({lcResult.model} model)
                  </h3>
                  <LearningChart result={lcResult} />
                </div>

                {/* Table (every 3 months) */}
                <div className="bg-slate-900 border border-slate-800 rounded-lg p-4 overflow-x-auto">
                  <h3 className="text-slate-300 text-xs font-semibold uppercase tracking-wider mb-3">
                    Monthly Projection
                  </h3>
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="text-slate-400 text-left border-b border-slate-800">
                        <th className="pb-2 pr-4 font-medium">Month</th>
                        <th className="pb-2 pr-4 font-medium">Yield</th>
                        {lcResult.model === "d0_learning" && (
                          <th className="pb-2 font-medium">D₀</th>
                        )}
                      </tr>
                    </thead>
                    <tbody>
                      {lcResult.projected
                        .filter((p) => p.month % 3 === 0 || p.month === lcResult.projected.length - 1)
                        .map((p) => (
                          <tr key={p.month} className={`border-b border-slate-800/50 ${
                            lcResult.months_to_target != null && p.month >= Math.ceil(lcResult.months_to_target)
                              ? "text-emerald-400"
                              : "text-slate-300"
                          }`}>
                            <td className="py-1.5 pr-4">M{p.month}</td>
                            <td className="py-1.5 pr-4 font-mono">{(p.yield_fraction * 100).toFixed(2)}%</td>
                            {lcResult.model === "d0_learning" && (
                              <td className="py-1.5 font-mono">{p.d0?.toFixed(4) ?? "—"}</td>
                            )}
                          </tr>
                        ))}
                    </tbody>
                  </table>
                </div>
              </>
            ) : (
              <div className="flex items-center justify-center h-48 text-slate-600 text-sm">
                Configure parameters and click Project Learning Curve.
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function SimField({
  label, value, onChange, step,
}: {
  label: string; value: string; onChange: (v: string) => void; step?: string;
}) {
  return (
    <div>
      <label className="block text-slate-400 text-xs mb-1">{label}</label>
      <input
        type="number" value={value} step={step}
        onChange={(e) => onChange(e.target.value)}
        className="w-full bg-slate-800 border border-slate-700 rounded px-3 py-1.5 text-white text-sm
                   focus:outline-none focus:border-emerald-500 transition-colors"
      />
    </div>
  );
}

function StatCard({ label, value, accent }: { label: string; value: string; accent?: boolean }) {
  return (
    <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
      <p className="text-slate-400 text-xs mb-1">{label}</p>
      <p className={`text-xl font-bold ${accent ? "text-emerald-400" : "text-white"}`}>{value}</p>
    </div>
  );
}

function Histogram({ bins }: { bins: { bin_low: number; bin_high: number; count: number }[] }) {
  const maxCount = Math.max(...bins.map((b) => b.count), 1);
  return (
    <div className="flex items-end gap-0.5 h-28">
      {bins.map((b, i) => (
        <div key={i} className="flex-1 flex flex-col items-center gap-0.5 group relative">
          <div
            className="w-full bg-emerald-500/70 hover:bg-emerald-400 transition-colors rounded-t-sm"
            style={{ height: `${(b.count / maxCount) * 100}%` }}
          />
          <div className="hidden group-hover:block absolute bottom-full mb-1 bg-slate-700 text-white text-xs
                          rounded px-2 py-1 whitespace-nowrap z-10 pointer-events-none">
            {(b.bin_low * 100).toFixed(0)}–{(b.bin_high * 100).toFixed(0)}%: {b.count}
          </div>
        </div>
      ))}
    </div>
  );
}

function LearningChart({ result }: { result: LearningCurveResult }) {
  const pts = result.projected;
  if (pts.length === 0) return null;
  const W = 560;
  const H = 160;
  const PAD = { top: 8, right: 12, bottom: 24, left: 44 };
  const innerW = W - PAD.left - PAD.right;
  const innerH = H - PAD.top - PAD.bottom;

  const xMax = pts[pts.length - 1].month;
  const yMin = Math.max(0, Math.min(...pts.map((p) => p.yield_fraction)) - 0.02);
  const yMax = Math.min(1.0, Math.max(...pts.map((p) => p.yield_fraction)) + 0.02);

  const toX = (m: number) => PAD.left + (m / xMax) * innerW;
  const toY = (y: number) => PAD.top + innerH - ((y - yMin) / (yMax - yMin)) * innerH;

  const d = pts
    .map((p, i) => `${i === 0 ? "M" : "L"} ${toX(p.month).toFixed(1)} ${toY(p.yield_fraction).toFixed(1)}`)
    .join(" ");

  const targetY = result.target_yield;
  const targetLine = toY(targetY);

  const yTicks = 4;
  const yTickVals = Array.from({ length: yTicks + 1 }, (_, i) =>
    yMin + (i / yTicks) * (yMax - yMin)
  );

  const xTicks = Math.min(xMax, 6);

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-auto">
      {/* Y gridlines */}
      {yTickVals.map((v, i) => (
        <g key={i}>
          <line x1={PAD.left} y1={toY(v)} x2={W - PAD.right} y2={toY(v)}
            stroke="#334155" strokeDasharray="3,3" />
          <text x={PAD.left - 4} y={toY(v) + 4} textAnchor="end" fontSize="9" fill="#64748b">
            {(v * 100).toFixed(0)}%
          </text>
        </g>
      ))}

      {/* Target line */}
      {targetY >= yMin && targetY <= yMax && (
        <line x1={PAD.left} y1={targetLine} x2={W - PAD.right} y2={targetLine}
          stroke="#10b981" strokeDasharray="6,3" strokeWidth="1.5" opacity="0.7" />
      )}

      {/* X axis labels */}
      {Array.from({ length: xTicks + 1 }, (_, i) => Math.round((i / xTicks) * xMax)).map((m) => (
        <text key={m} x={toX(m)} y={H - 4} textAnchor="middle" fontSize="9" fill="#64748b">
          M{m}
        </text>
      ))}

      {/* Yield line */}
      <path d={d} fill="none" stroke="#10b981" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />

      {/* Target label */}
      {targetY >= yMin && targetY <= yMax && (
        <text x={W - PAD.right - 2} y={targetLine - 3} textAnchor="end" fontSize="9" fill="#10b981" opacity="0.8">
          target {(targetY * 100).toFixed(0)}%
        </text>
      )}
    </svg>
  );
}
