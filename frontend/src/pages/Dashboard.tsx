import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import type { Panel, LotSummary } from "../types";
import StatCard from "../components/StatCard";
import DonutChart from "../components/DonutChart";

export default function Dashboard() {
  const [panels,  setPanels]  = useState<Panel[]>([]);
  const [lots,    setLots]    = useState<LotSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error,   setError]   = useState<string | null>(null);

  useEffect(() => {
    Promise.all([
      api.panels.list().then(r => r.results),
      api.lots.list(),
    ])
      .then(([p, l]) => { setPanels(p); setLots(l); })
      .catch(e => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <Spinner />;
  if (error)   return <ErrorBox msg={error} />;

  const totalPanels = lots.reduce((s, l) => s + l.panel_count, 0);
  const waferCount   = panels.filter(p => p.substrate_type === "wafer").length;
  const glassCount   = panels.filter(p => p.substrate_type === "glass_panel").length;

  const cleanLots     = lots.filter(l => l.lot_status === "clean").length;
  const watchLots     = lots.filter(l => l.lot_status === "watch").length;
  const excursionLots = lots.filter(l => l.lot_status === "excursion").length;

  const yieldValues = lots.map(l => l.avg_yield_negbinom).filter((v): v is number => v != null);
  const fleetYield  = yieldValues.length
    ? yieldValues.reduce((s, v) => s + v, 0) / yieldValues.length
    : null;

  // Yield bucket breakdown across all panels
  const allPanelYields = lots.flatMap(l => l.panels.map(p => p.yield_negbinom)).filter((v): v is number => v != null);
  const yHigh   = allPanelYields.filter(v => v >= 0.5).length;
  const yMid    = allPanelYields.filter(v => v >= 0.25 && v < 0.5).length;
  const yLow    = allPanelYields.filter(v => v < 0.25).length;

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-bold text-slate-100">Dashboard</h1>

      {/* KPI row */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
        <StatCard label="Total Panels"  value={totalPanels}  accent="blue"  />
        <StatCard label="Total Lots"    value={lots.length}  accent="green" />
        <StatCard label="Excursion Lots" value={excursionLots} accent="red" />
        <StatCard
          label="Fleet Avg Yield"
          value={fleetYield != null ? `${(fleetYield * 100).toFixed(1)}%` : "—"}
          accent={fleetYield == null ? "blue" : fleetYield >= 0.5 ? "green" : fleetYield >= 0.25 ? "amber" : "red"}
        />
      </div>

      {/* Charts row */}
      {lots.length > 0 && (
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          {/* Lot status donut */}
          <div className="bg-slate-900 border border-slate-800 rounded-xl px-5 py-4">
            <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-500 mb-4">Lot Status</h2>
            <DonutChart
              size={110}
              thickness={20}
              label={String(lots.length)}
              sublabel="lots"
              slices={[
                { label: "Clean",     value: cleanLots,     color: "#22c55e" },
                { label: "Watch",     value: watchLots,     color: "#f59e0b" },
                { label: "Excursion", value: excursionLots, color: "#ef4444" },
              ].filter(s => s.value > 0)}
            />
          </div>

          {/* Substrate split donut */}
          <div className="bg-slate-900 border border-slate-800 rounded-xl px-5 py-4">
            <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-500 mb-4">Substrate Split</h2>
            <DonutChart
              size={110}
              thickness={20}
              label={String(panels.length)}
              sublabel="panels"
              slices={[
                { label: "Glass Panel", value: glassCount,  color: "#a78bfa" },
                { label: "Wafer",       value: waferCount,  color: "#38bdf8" },
              ].filter(s => s.value > 0)}
            />
          </div>

          {/* Panel yield buckets donut */}
          <div className="bg-slate-900 border border-slate-800 rounded-xl px-5 py-4">
            <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-500 mb-4">Panel Yield Distribution</h2>
            {allPanelYields.length > 0 ? (
              <DonutChart
                size={110}
                thickness={20}
                label={String(allPanelYields.length)}
                sublabel="panels"
                slices={[
                  { label: "≥ 50%",    value: yHigh, color: "#22c55e" },
                  { label: "25–50%",   value: yMid,  color: "#f59e0b" },
                  { label: "< 25%",    value: yLow,  color: "#ef4444" },
                ].filter(s => s.value > 0)}
              />
            ) : (
              <p className="text-xs text-slate-600">No yield data — generate a lot with yield enabled.</p>
            )}
          </div>
        </div>
      )}

      {/* Lot status bar — excursion/watch at a glance */}
      {lots.length > 0 && (
        <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
          <div className="px-5 py-3 border-b border-slate-800 flex items-center justify-between">
            <h2 className="text-sm font-semibold text-slate-200">Lot Overview</h2>
            <Link to="/lots" className="text-xs text-emerald-400 hover:underline">View all →</Link>
          </div>
          <div className="divide-y divide-slate-800/50">
            {lots.slice(0, 8).map(lot => {
              const y = lot.avg_yield_negbinom;
              return (
                <div key={lot.lot_id} className="px-5 py-3 flex items-center gap-4">
                  <span className={`w-2 h-2 rounded-full shrink-0 ${
                    lot.lot_status === "excursion" ? "bg-red-500" :
                    lot.lot_status === "watch"     ? "bg-amber-500" : "bg-emerald-500"
                  }`} />
                  <span className="font-mono text-xs text-slate-300 w-44 truncate">{lot.lot_id}</span>
                  <span className={`text-xs px-2 py-0.5 rounded-full ${
                    lot.substrate_type === "wafer"
                      ? "bg-sky-500/15 text-sky-400"
                      : "bg-violet-500/15 text-violet-400"
                  }`}>
                    {lot.substrate_type === "wafer" ? "Wafer" : "Glass"}
                  </span>
                  <span className="text-xs text-slate-500">{lot.panel_count} panels</span>

                  {/* Inline yield bar */}
                  <div className="flex-1 flex items-center gap-2">
                    <div className="flex-1 h-1.5 bg-slate-800 rounded-full overflow-hidden">
                      <div
                        className={`h-full rounded-full ${
                          y == null ? "bg-slate-700" :
                          y >= 0.5  ? "bg-emerald-500" :
                          y >= 0.25 ? "bg-amber-500" : "bg-red-500"
                        }`}
                        style={{ width: y != null ? `${y * 100}%` : "0%" }}
                      />
                    </div>
                    <span className={`text-xs font-mono w-12 text-right ${
                      y == null ? "text-slate-600" :
                      y >= 0.5  ? "text-emerald-400" :
                      y >= 0.25 ? "text-amber-400" : "text-red-400"
                    }`}>
                      {y != null ? `${(y * 100).toFixed(1)}%` : "—"}
                    </span>
                  </div>

                  {lot.excursion_count > 0 && (
                    <span className="text-xs text-red-400">⚠ {lot.excursion_count}</span>
                  )}
                  <Link to="/lots" className="text-xs text-slate-600 hover:text-emerald-400">→</Link>
                </div>
              );
            })}
          </div>
          {lots.length > 8 && (
            <div className="px-5 py-3 border-t border-slate-800 text-center">
              <Link to="/lots" className="text-xs text-emerald-400 hover:underline">
                + {lots.length - 8} more lots
              </Link>
            </div>
          )}
        </div>
      )}

      {/* Panel table */}
      <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
        <div className="px-5 py-3 border-b border-slate-800 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-slate-200">All Panels</h2>
          <div className="flex gap-2">
            <Link to="/generate" className="text-xs bg-slate-800 hover:bg-slate-700 text-slate-300 px-3 py-1.5 rounded-md transition-colors">
              + Generate
            </Link>
            <Link to="/upload" className="text-xs bg-emerald-600 hover:bg-emerald-500 text-white px-3 py-1.5 rounded-md transition-colors">
              + Upload KLARF
            </Link>
          </div>
        </div>

        {panels.length === 0 ? (
          <div className="px-5 py-10 text-center space-y-2">
            <p className="text-slate-500 text-sm">No panels yet.</p>
            <Link to="/generate" className="text-emerald-400 text-sm hover:underline inline-block">
              Generate synthetic data to get started →
            </Link>
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="text-slate-500 text-xs uppercase border-b border-slate-800">
                {["Panel ID", "Substrate", "Lot", "Grid", ""].map(h => (
                  <th key={h} className="text-left px-5 py-2.5 font-medium">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {panels.map(p => (
                <tr key={p.panel_id} className="border-b border-slate-800/50 hover:bg-slate-800/30 transition-colors">
                  <td className="px-5 py-3 font-mono text-slate-200 text-xs">{p.panel_id}</td>
                  <td className="px-5 py-3">
                    <span className={`text-xs px-2 py-0.5 rounded-full ${
                      p.substrate_type === "wafer"
                        ? "bg-sky-500/15 text-sky-400"
                        : "bg-violet-500/15 text-violet-400"
                    }`}>
                      {p.substrate_type === "wafer" ? "Wafer" : "Glass"}
                    </span>
                  </td>
                  <td className="px-5 py-3 text-slate-400 text-xs font-mono">{p.lot_id || "—"}</td>
                  <td className="px-5 py-3 text-slate-400 text-xs">{p.rows}×{p.cols}</td>
                  <td className="px-5 py-3">
                    <Link
                      to={`/yield-map?panel=${p.panel_id}`}
                      className="text-xs text-emerald-400 hover:underline"
                    >
                      Yield Map →
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

function Spinner() {
  return (
    <div className="flex items-center justify-center h-64">
      <div className="w-8 h-8 border-2 border-emerald-500 border-t-transparent rounded-full animate-spin" />
    </div>
  );
}

function ErrorBox({ msg }: { msg: string }) {
  return (
    <div className="bg-red-500/10 border border-red-500/30 rounded-xl px-5 py-4 text-red-400 text-sm">{msg}</div>
  );
}
