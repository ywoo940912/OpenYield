import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import type { LotSummary, ClaudeYieldReport } from "../types";
import DonutChart from "../components/DonutChart";

// ── Helpers ───────────────────────────────────────────────────────────────────

function pct(v: number | null) {
  return v == null ? "—" : `${(v * 100).toFixed(1)}%`;
}

function statusStyle(s: string) {
  if (s === "excursion") return { dot: "bg-red-500",    badge: "bg-red-500/15 text-red-400 border-red-500/30" };
  if (s === "watch")     return { dot: "bg-amber-500",  badge: "bg-amber-500/15 text-amber-400 border-amber-500/30" };
  return                        { dot: "bg-emerald-500",badge: "bg-emerald-500/15 text-emerald-400 border-emerald-500/30" };
}

function yieldBarColor(v: number | null) {
  if (v == null)  return "bg-slate-700";
  if (v >= 0.5)   return "bg-emerald-500";
  if (v >= 0.25)  return "bg-amber-500";
  return "bg-red-500";
}

function yieldTextColor(v: number | null) {
  if (v == null)  return "text-slate-500";
  if (v >= 0.5)   return "text-emerald-400";
  if (v >= 0.25)  return "text-amber-400";
  return "text-red-400";
}

type SortKey = "lot_id" | "avg_yield" | "status" | "panel_count" | "excursions";
type SubFilter = "" | "glass_panel" | "wafer";

function sortLots(lots: LotSummary[], key: SortKey): LotSummary[] {
  return [...lots].sort((a, b) => {
    switch (key) {
      case "avg_yield":   return (b.avg_yield_negbinom ?? -1) - (a.avg_yield_negbinom ?? -1);
      case "status": {
        const order: Record<string, number> = { excursion: 0, watch: 1, clean: 2 };
        return (order[a.lot_status] ?? 3) - (order[b.lot_status] ?? 3);
      }
      case "panel_count": return b.panel_count - a.panel_count;
      case "excursions":  return b.excursion_count - a.excursion_count;
      default:            return a.lot_id.localeCompare(b.lot_id);
    }
  });
}

// ── Panel drawer ──────────────────────────────────────────────────────────────

function PanelDrawer({ lot }: { lot: LotSummary }) {
  return (
    <div className="border-t border-slate-800 bg-slate-950">
      <table className="w-full text-xs">
        <thead>
          <tr className="text-slate-600 border-b border-slate-800">
            <th className="px-5 py-2.5 text-left font-medium uppercase tracking-wide">Panel ID</th>
            <th className="px-5 py-2.5 text-left font-medium uppercase tracking-wide">Defect Density</th>
            <th className="px-5 py-2.5 text-left font-medium uppercase tracking-wide">Yield (NegBinom)</th>
            <th className="px-5 py-2.5 text-left font-medium uppercase tracking-wide">Cluster Class</th>
            <th className="px-5 py-2.5 text-left font-medium uppercase tracking-wide"></th>
          </tr>
        </thead>
        <tbody>
          {lot.panels.map(p => (
            <tr key={p.panel_id} className="border-t border-slate-800/40 hover:bg-slate-800/20 transition-colors">
              <td className="px-5 py-3 font-mono text-slate-300">{p.panel_id}</td>
              <td className="px-5 py-3 font-mono text-slate-400">
                {p.defect_density.toFixed(5)} /mm²
              </td>
              <td className="px-5 py-3">
                <div className="flex items-center gap-3">
                  <div className="w-24 h-1.5 bg-slate-800 rounded-full overflow-hidden">
                    <div
                      className={`h-full rounded-full ${yieldBarColor(p.yield_negbinom)}`}
                      style={{ width: `${(p.yield_negbinom ?? 0) * 100}%` }}
                    />
                  </div>
                  <span className={`font-semibold ${yieldTextColor(p.yield_negbinom)}`}>
                    {pct(p.yield_negbinom)}
                  </span>
                </div>
              </td>
              <td className="px-5 py-3">
                {p.cluster_class ? (
                  <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${
                    p.cluster_class === "excursion"  ? "bg-red-500/15 text-red-400" :
                    p.cluster_class === "systematic" ? "bg-amber-500/15 text-amber-400" :
                    "bg-emerald-500/15 text-emerald-400"
                  }`}>
                    {p.cluster_class}
                  </span>
                ) : <span className="text-slate-600">—</span>}
              </td>
              <td className="px-5 py-3 text-right">
                <Link
                  to={`/yield-map?panel=${p.panel_id}`}
                  className="text-xs text-emerald-400 hover:text-emerald-300 hover:underline whitespace-nowrap"
                >
                  Yield Map →
                </Link>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── Lot card ──────────────────────────────────────────────────────────────────

function LotCard({ lot }: { lot: LotSummary }) {
  const [open,       setOpen]       = useState(false);
  const [report,     setReport]     = useState<ClaudeYieldReport | null>(null);
  const [reporting,  setReporting]  = useState(false);
  const [reportErr,  setReportErr]  = useState<string | null>(null);
  const st = statusStyle(lot.lot_status);

  function runReport() {
    setReporting(true);
    setReportErr(null);
    api.claude.yieldReport(lot.lot_id)
      .then(setReport)
      .catch(e => setReportErr(e.message))
      .finally(() => setReporting(false));
  }

  return (
    <div className={`bg-slate-900 border rounded-xl overflow-hidden transition-colors ${
      open ? "border-slate-600" : "border-slate-800 hover:border-slate-700"
    }`}>
      {/* Header row */}
      <button
        onClick={() => setOpen(o => !o)}
        className="w-full text-left px-5 py-4 flex items-center gap-4"
      >
        {/* Chevron */}
        <span className={`text-slate-600 text-xs transition-transform ${open ? "rotate-90" : ""}`}>▶</span>

        {/* Lot ID */}
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-mono text-sm font-semibold text-slate-100">{lot.lot_id}</span>
            <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${
              lot.substrate_type === "wafer"
                ? "bg-sky-500/15 text-sky-400"
                : "bg-violet-500/15 text-violet-400"
            }`}>
              {lot.substrate_type === "wafer" ? "Wafer" : "Glass Panel"}
            </span>
            <span className={`text-xs px-2 py-0.5 rounded-full font-medium border ${st.badge}`}>
              <span className={`inline-block w-1.5 h-1.5 rounded-full mr-1.5 ${st.dot}`} />
              {lot.lot_status}
            </span>
          </div>
          {lot.status_reason && (
            <p className="text-xs text-slate-500 mt-0.5 truncate">{lot.status_reason}</p>
          )}
        </div>

        {/* Stats */}
        <div className="hidden sm:flex items-center gap-8 shrink-0 text-right">
          <div>
            <div className="text-xs text-slate-500 uppercase tracking-wide">Panels</div>
            <div className="text-sm font-semibold text-slate-200">{lot.panel_count}</div>
          </div>
          <div>
            <div className="text-xs text-slate-500 uppercase tracking-wide">Avg Yield</div>
            <div className={`text-sm font-semibold ${yieldTextColor(lot.avg_yield_negbinom)}`}>
              {pct(lot.avg_yield_negbinom)}
            </div>
          </div>
          <div>
            <div className="text-xs text-slate-500 uppercase tracking-wide">Excursions</div>
            <div className={`text-sm font-semibold ${lot.excursion_count > 0 ? "text-red-400" : "text-slate-500"}`}>
              {lot.excursion_count}
            </div>
          </div>
          {/* Yield bar */}
          <div className="w-28">
            <div className="text-xs text-slate-500 uppercase tracking-wide mb-1">Yield</div>
            <div className="h-1.5 bg-slate-800 rounded-full overflow-hidden">
              <div
                className={`h-full rounded-full ${yieldBarColor(lot.avg_yield_negbinom)}`}
                style={{ width: `${(lot.avg_yield_negbinom ?? 0) * 100}%` }}
              />
            </div>
          </div>
        </div>
      </button>

      {open && (
        <>
          <PanelDrawer lot={lot} />

          {/* Claude yield report */}
          <div className="border-t border-slate-800 px-5 py-4 bg-slate-950/40">
            {!report && !reporting && (
              <button
                onClick={runReport}
                className="flex items-center gap-2 px-4 py-2 rounded-lg bg-violet-600/20 hover:bg-violet-600/30 border border-violet-500/40 text-violet-300 text-xs font-medium transition-colors"
              >
                <span>✦</span> Generate AI Yield Report
              </button>
            )}

            {reporting && (
              <div className="flex items-center gap-2 text-xs text-violet-400">
                <div className="w-3 h-3 border border-violet-400 border-t-transparent rounded-full animate-spin" />
                Claude is generating the yield report…
              </div>
            )}

            {reportErr && (
              <div className="text-xs text-red-400 bg-red-500/10 rounded-lg px-3 py-2">
                {reportErr}
              </div>
            )}

            {report && (
              <div className="bg-violet-500/8 border border-violet-500/25 rounded-xl px-5 py-4 space-y-2">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-1.5 text-xs text-violet-400 font-semibold">
                    <span>✦</span> Claude Yield Report — {lot.lot_id}
                  </div>
                  <button
                    onClick={() => setReport(null)}
                    className="text-slate-600 hover:text-slate-400 text-xs"
                  >
                    dismiss
                  </button>
                </div>
                <p className="text-sm text-slate-300 leading-relaxed whitespace-pre-wrap">{report.report}</p>
                <div className="text-xs text-slate-600 pt-1">{report.model}</div>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}

// ── Summary bar ───────────────────────────────────────────────────────────────

function SummaryBar({ lots }: { lots: LotSummary[] }) {
  const totalPanels   = lots.reduce((s, l) => s + l.panel_count, 0);
  const excursionLots = lots.filter(l => l.lot_status === "excursion").length;
  const watchLots     = lots.filter(l => l.lot_status === "watch").length;
  const cleanLots     = lots.filter(l => l.lot_status === "clean").length;
  const yields        = lots.map(l => l.avg_yield_negbinom).filter((v): v is number => v != null);
  const avgYield      = yields.length ? yields.reduce((s, v) => s + v, 0) / yields.length : null;

  const allPanelYields = lots.flatMap(l => l.panels.map(p => p.yield_negbinom)).filter((v): v is number => v != null);
  const yHigh = allPanelYields.filter(v => v >= 0.5).length;
  const yMid  = allPanelYields.filter(v => v >= 0.25 && v < 0.5).length;
  const yLow  = allPanelYields.filter(v => v < 0.25).length;

  return (
    <div className="flex gap-4 flex-wrap">
      {/* KPI tiles */}
      <div className="grid grid-cols-2 sm:grid-cols-3 gap-3 flex-1 min-w-0">
        {[
          { label: "Total Lots",    value: String(lots.length),    color: "text-slate-100" },
          { label: "Total Panels",  value: String(totalPanels),    color: "text-slate-100" },
          { label: "Fleet Yield",   value: avgYield != null ? `${(avgYield * 100).toFixed(1)}%` : "—",
            color: avgYield == null ? "text-slate-500" : avgYield >= 0.5 ? "text-emerald-400" : avgYield >= 0.25 ? "text-amber-400" : "text-red-400" },
          { label: "Clean",         value: String(cleanLots),      color: "text-emerald-400" },
          { label: "Watch",         value: String(watchLots),      color: "text-amber-400" },
          { label: "Excursion",     value: String(excursionLots),  color: "text-red-400" },
        ].map(({ label, value, color }) => (
          <div key={label} className="bg-slate-900 border border-slate-800 rounded-xl px-4 py-3">
            <div className="text-xs text-slate-500 uppercase tracking-wide">{label}</div>
            <div className={`text-xl font-bold mt-0.5 ${color}`}>{value}</div>
          </div>
        ))}
      </div>

      {/* Lot status donut */}
      {lots.length > 0 && (
        <div className="bg-slate-900 border border-slate-800 rounded-xl px-5 py-4 shrink-0">
          <div className="text-xs font-semibold uppercase tracking-wide text-slate-500 mb-3">Status</div>
          <DonutChart
            size={100}
            thickness={18}
            label={String(lots.length)}
            sublabel="lots"
            slices={[
              { label: "Clean",     value: cleanLots,     color: "#22c55e" },
              { label: "Watch",     value: watchLots,     color: "#f59e0b" },
              { label: "Excursion", value: excursionLots, color: "#ef4444" },
            ].filter(s => s.value > 0)}
          />
        </div>
      )}

      {/* Yield donut */}
      {allPanelYields.length > 0 && (
        <div className="bg-slate-900 border border-slate-800 rounded-xl px-5 py-4 shrink-0">
          <div className="text-xs font-semibold uppercase tracking-wide text-slate-500 mb-3">Panel Yield</div>
          <DonutChart
            size={100}
            thickness={18}
            label={String(allPanelYields.length)}
            sublabel="panels"
            slices={[
              { label: "≥ 50%",  value: yHigh, color: "#22c55e" },
              { label: "25–50%", value: yMid,  color: "#f59e0b" },
              { label: "< 25%",  value: yLow,  color: "#ef4444" },
            ].filter(s => s.value > 0)}
          />
        </div>
      )}
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function Lots() {
  const [all,     setAll]     = useState<LotSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error,   setError]   = useState<string | null>(null);
  const [sub,     setSub]     = useState<SubFilter>("");
  const [sort,    setSort]    = useState<SortKey>("lot_id");
  const [search,  setSearch]  = useState("");

  useEffect(() => {
    setLoading(true);
    api.lots.list(sub || undefined)
      .then(setAll)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false));
  }, [sub]);

  const filtered = sortLots(
    search
      ? all.filter(l => l.lot_id.toLowerCase().includes(search.toLowerCase()))
      : all,
    sort,
  );

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold text-slate-100">Lots</h1>
        <Link
          to="/generate"
          className="text-xs bg-emerald-600 hover:bg-emerald-500 text-white px-3 py-1.5 rounded-md transition-colors"
        >
          + Generate Lot
        </Link>
      </div>

      {error && (
        <div className="bg-red-500/10 border border-red-500/30 rounded-xl px-5 py-4 text-red-400 text-sm">{error}</div>
      )}

      {loading ? (
        <div className="flex items-center justify-center h-64">
          <div className="w-8 h-8 border-2 border-emerald-500 border-t-transparent rounded-full animate-spin" />
        </div>
      ) : (
        <>
          <SummaryBar lots={all} />

          {/* Filter / sort bar */}
          <div className="flex flex-wrap gap-3 items-center">
            {/* Substrate filter */}
            <div className="flex gap-1 bg-slate-900 border border-slate-800 rounded-lg p-1">
              {([["", "All"], ["glass_panel", "Glass Panel"], ["wafer", "Wafer"]] as [SubFilter, string][]).map(([val, label]) => (
                <button
                  key={val}
                  onClick={() => setSub(val)}
                  className={`px-3 py-1.5 rounded-md text-xs font-medium transition-colors ${
                    sub === val
                      ? "bg-emerald-600 text-white"
                      : "text-slate-400 hover:text-slate-100"
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>

            {/* Sort */}
            <select
              value={sort}
              onChange={e => setSort(e.target.value as SortKey)}
              className="bg-slate-900 border border-slate-800 rounded-lg px-3 py-2 text-xs text-slate-300 focus:outline-none focus:border-emerald-500"
            >
              <option value="lot_id">Sort: Lot ID</option>
              <option value="avg_yield">Sort: Yield ↓</option>
              <option value="status">Sort: Status (worst first)</option>
              <option value="excursions">Sort: Excursions ↓</option>
              <option value="panel_count">Sort: Panel Count ↓</option>
            </select>

            {/* Search */}
            <input
              type="text"
              value={search}
              onChange={e => setSearch(e.target.value)}
              placeholder="Search lot ID…"
              className="bg-slate-900 border border-slate-800 rounded-lg px-3 py-2 text-xs text-slate-300 placeholder-slate-600 focus:outline-none focus:border-emerald-500 w-48"
            />

            <span className="text-xs text-slate-600 ml-auto">
              {filtered.length} lot{filtered.length !== 1 ? "s" : ""}
            </span>
          </div>

          {/* Lot cards */}
          {filtered.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-20 text-center">
              <p className="text-slate-500 text-sm">No lots found.</p>
              <Link to="/generate" className="text-emerald-400 text-xs hover:underline mt-2">
                Generate synthetic lots to get started →
              </Link>
            </div>
          ) : (
            <div className="flex flex-col gap-2">
              {filtered.map(lot => (
                <LotCard key={lot.lot_id} lot={lot} />
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}
