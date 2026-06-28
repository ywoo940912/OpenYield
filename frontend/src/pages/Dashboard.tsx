import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import type { Panel } from "../types";
import StatCard from "../components/StatCard";

export default function Dashboard() {
  const [panels, setPanels]   = useState<Panel[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError]     = useState<string | null>(null);

  useEffect(() => {
    api.panels.list()
      .then(r => setPanels(r.panels))
      .catch(e => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <Spinner />;
  if (error)   return <ErrorBox msg={error} />;

  const totalDefects   = panels.reduce((s, p) => s + p.defect_count, 0);
  const waferCount     = panels.filter(p => p.substrate_type === "wafer").length;
  const glassCount     = panels.filter(p => p.substrate_type === "glass_panel").length;

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-bold text-slate-100">Dashboard</h1>

      {/* Summary stats */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
        <StatCard label="Total Panels"    value={panels.length}  accent="blue" />
        <StatCard label="Wafers"          value={waferCount}     accent="green" />
        <StatCard label="Glass Panels"    value={glassCount}     accent="amber" />
        <StatCard label="Total Defects"   value={totalDefects.toLocaleString()} accent="red" />
      </div>

      {/* Panel table */}
      <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
        <div className="px-5 py-3 border-b border-slate-800 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-slate-200">All Panels</h2>
          <Link
            to="/upload"
            className="text-xs bg-emerald-600 hover:bg-emerald-500 text-white px-3 py-1.5 rounded-md transition-colors"
          >
            + Upload KLARF
          </Link>
        </div>

        {panels.length === 0 ? (
          <div className="px-5 py-8 text-center">
            <p className="text-slate-500 text-sm">No panels yet.</p>
            <Link to="/upload" className="text-emerald-400 text-sm hover:underline mt-1 inline-block">
              Upload a KLARF 2.0 file to get started
            </Link>
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="text-slate-500 text-xs uppercase border-b border-slate-800">
                {["Panel ID", "Substrate", "Lot", "Grid", "Pitch (mm)", "Defects", ""].map(h => (
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
                        ? "bg-sky-500/20 text-sky-400"
                        : "bg-violet-500/20 text-violet-400"
                    }`}>
                      {p.substrate_type}
                    </span>
                  </td>
                  <td className="px-5 py-3 text-slate-400 text-xs font-mono">{p.lot_id || "—"}</td>
                  <td className="px-5 py-3 text-slate-400 text-xs">{p.rows}×{p.cols}</td>
                  <td className="px-5 py-3 text-slate-400 text-xs">{p.component_pitch_mm}</td>
                  <td className="px-5 py-3">
                    <span className={`text-xs font-medium ${p.defect_count > 0 ? "text-red-400" : "text-emerald-400"}`}>
                      {p.defect_count}
                    </span>
                  </td>
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
    <div className="bg-red-500/10 border border-red-500/30 rounded-xl px-5 py-4 text-red-400 text-sm">
      {msg}
    </div>
  );
}
