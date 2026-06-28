import { useEffect, useState } from "react";
import { api } from "../api";
import type { Panel, DefectDistribution, CNNStatus } from "../types";
import StatCard from "../components/StatCard";
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell,
} from "recharts";

const COLORS = [
  "#34d399","#60a5fa","#f59e0b","#a78bfa",
  "#f472b6","#38bdf8","#fb923c",
];

export default function Classifier() {
  const [panels, setPanels]     = useState<Panel[]>([]);
  const [panelId, setPanelId]   = useState("");
  const [dist, setDist]         = useState<DefectDistribution | null>(null);
  const [cnn, setCnn]           = useState<CNNStatus | null>(null);
  const [loading, setLoading]   = useState(false);
  const [error, setError]       = useState<string | null>(null);

  useEffect(() => {
    api.panels.list().then(r => setPanels(r.panels));
    api.classify.cnnStatus().then(setCnn).catch(() => null);
  }, []);

  useEffect(() => {
    if (!panelId) { setDist(null); return; }
    setLoading(true);
    setError(null);
    api.classify.defects(panelId)
      .then(setDist)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false));
  }, [panelId]);

  const chartData = dist
    ? Object.entries(dist.defect_counts)
        .sort((a, b) => b[1] - a[1])
        .map(([name, value]) => ({ name, value }))
    : [];

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <h1 className="text-xl font-bold text-slate-100">Defect Classifier</h1>

        {/* CNN status badge */}
        {cnn && (
          <div className={`flex items-center gap-2 text-xs px-3 py-1.5 rounded-full border ${
            cnn.model_available
              ? "border-emerald-700 bg-emerald-500/10 text-emerald-400"
              : "border-slate-700 bg-slate-800 text-slate-400"
          }`}>
            <span className={`w-1.5 h-1.5 rounded-full ${cnn.model_available ? "bg-emerald-400" : "bg-slate-500"}`} />
            {cnn.model_available
              ? `CNN ready · val_acc ${cnn.val_accuracy != null ? `${(cnn.val_accuracy * 100).toFixed(1)}%` : "?"} · ${cnn.n_classes} classes`
              : "CNN not trained"}
          </div>
        )}
      </div>

      {/* Panel selector */}
      <select
        value={panelId}
        onChange={e => setPanelId(e.target.value)}
        className="bg-slate-800 border border-slate-700 rounded-lg px-3 py-2.5 text-sm
                   text-slate-200 focus:outline-none focus:border-emerald-500 w-72"
      >
        <option value="">Select panel…</option>
        {panels.map(p => (
          <option key={p.panel_id} value={p.panel_id}>{p.panel_id}</option>
        ))}
      </select>

      {loading && (
        <div className="flex items-center justify-center h-32">
          <div className="w-8 h-8 border-2 border-emerald-500 border-t-transparent rounded-full animate-spin" />
        </div>
      )}
      {error && (
        <div className="bg-red-500/10 border border-red-500/30 rounded-xl px-5 py-4 text-red-400 text-sm">{error}</div>
      )}

      {dist && (
        <>
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-4">
            <StatCard label="Total Defects"  value={dist.total_defects.toLocaleString()} accent="red" />
            <StatCard label="Dominant Type"  value={dist.top_class}                      accent="amber" />
            <StatCard label="Top Fraction"   value={`${(dist.top_class_fraction * 100).toFixed(1)}%`} accent="blue" />
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            {/* Bar chart */}
            <div className="bg-slate-900 border border-slate-800 rounded-xl p-5">
              <h2 className="text-sm font-semibold text-slate-200 mb-4">Defect Type Distribution</h2>
              <ResponsiveContainer width="100%" height={220}>
                <BarChart data={chartData} layout="vertical" margin={{ left: 16, right: 16 }}>
                  <XAxis type="number" tick={{ fill: "#94a3b8", fontSize: 11 }} axisLine={false} tickLine={false} />
                  <YAxis
                    dataKey="name" type="category"
                    tick={{ fill: "#cbd5e1", fontSize: 11 }}
                    axisLine={false} tickLine={false} width={90}
                  />
                  <Tooltip
                    contentStyle={{ background: "#1e293b", border: "1px solid #334155", borderRadius: 8, fontSize: 12 }}
                    labelStyle={{ color: "#cbd5e1" }}
                  />
                  <Bar dataKey="value" radius={[0, 4, 4, 0]}>
                    {chartData.map((_, i) => (
                      <Cell key={i} fill={COLORS[i % COLORS.length]} fillOpacity={0.85} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>

            {/* Count table */}
            <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
              <div className="px-5 py-3 border-b border-slate-800">
                <h2 className="text-sm font-semibold text-slate-200">Breakdown</h2>
              </div>
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-slate-500 text-xs uppercase border-b border-slate-800">
                    <th className="text-left px-5 py-2.5 font-medium">Type</th>
                    <th className="text-right px-5 py-2.5 font-medium">Count</th>
                    <th className="text-right px-5 py-2.5 font-medium">Share</th>
                  </tr>
                </thead>
                <tbody>
                  {chartData.map(({ name, value }, i) => (
                    <tr key={name} className="border-b border-slate-800/50 hover:bg-slate-800/30">
                      <td className="px-5 py-2.5 flex items-center gap-2">
                        <span
                          className="w-2 h-2 rounded-full"
                          style={{ background: COLORS[i % COLORS.length] }}
                        />
                        <span className="text-slate-300">{name}</span>
                      </td>
                      <td className="px-5 py-2.5 text-right font-mono text-slate-300">{value}</td>
                      <td className="px-5 py-2.5 text-right text-slate-400">
                        {dist.total_defects > 0
                          ? `${((value / dist.total_defects) * 100).toFixed(1)}%`
                          : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}

      {!panelId && !loading && (
        <div className="bg-slate-900 border border-slate-800 rounded-xl px-5 py-10 text-center text-slate-500">
          Select a panel to view its defect type distribution.
        </div>
      )}
    </div>
  );
}
