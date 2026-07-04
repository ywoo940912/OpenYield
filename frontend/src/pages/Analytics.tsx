import { useEffect, useState } from "react";
import { api } from "../api";
import type {
  Panel, ParetoResult, SpcResult, TrendResult, Defect,
} from "../types";

// ── Math helper ───────────────────────────────────────────────────────────────

function sc(v: number, dLo: number, dHi: number, rLo: number, rHi: number): number {
  if (dHi === dLo) return (rLo + rHi) / 2;
  return rLo + ((v - dLo) / (dHi - dLo)) * (rHi - rLo);
}

// ── Shared UI ─────────────────────────────────────────────────────────────────

function Spinner() {
  return (
    <div className="flex items-center justify-center h-64">
      <div className="w-8 h-8 border-2 border-emerald-500 border-t-transparent rounded-full animate-spin" />
    </div>
  );
}

function ErrBox({ msg }: { msg: string }) {
  return (
    <div className="bg-red-500/10 border border-red-500/30 rounded-xl px-5 py-4 text-red-400 text-sm">
      {msg}
    </div>
  );
}

function Empty({ msg = "No data available." }: { msg?: string }) {
  return (
    <div className="bg-slate-900 border border-slate-800 rounded-xl px-5 py-12 text-center text-slate-500 text-sm">
      {msg}
    </div>
  );
}

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="bg-slate-900 border border-slate-800 rounded-xl p-5">
      <h2 className="text-sm font-semibold text-slate-200 mb-4">{title}</h2>
      {children}
    </div>
  );
}

function StatPill({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-slate-800 rounded-lg px-4 py-3">
      <p className="text-slate-500 text-xs">{label}</p>
      <p className="text-slate-100 font-mono text-sm mt-0.5">{value}</p>
    </div>
  );
}

function SubstrateSelect({ value, onChange }: {
  value: string; onChange: (v: string) => void;
}) {
  return (
    <select
      value={value}
      onChange={e => onChange(e.target.value)}
      className="bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-200
                 focus:outline-none focus:border-emerald-500"
    >
      <option value="">All substrates</option>
      <option value="glass_panel">Glass Panel</option>
      <option value="wafer">Wafer</option>
    </select>
  );
}

// ── Tab bar ───────────────────────────────────────────────────────────────────

type Tab = "pareto" | "spc" | "trend" | "scatter";

const TABS: { id: Tab; label: string }[] = [
  { id: "pareto",  label: "Pareto" },
  { id: "spc",     label: "SPC" },
  { id: "trend",   label: "Lot Trend" },
  { id: "scatter", label: "Defect Scatter" },
];

// ── Main page ─────────────────────────────────────────────────────────────────

export default function Analytics() {
  const [tab, setTab] = useState<Tab>("pareto");

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-bold text-slate-100">Analytics</h1>

      <div className="flex gap-1 bg-slate-900 border border-slate-800 rounded-lg p-1 w-fit">
        {TABS.map(t => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={`px-4 py-1.5 rounded-md text-sm font-medium transition-colors ${
              tab === t.id
                ? "bg-emerald-600 text-white"
                : "text-slate-400 hover:text-slate-100"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === "pareto"  && <ParetoTab />}
      {tab === "spc"     && <SpcTab />}
      {tab === "trend"   && <TrendTab />}
      {tab === "scatter" && <ScatterTab />}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════════════
// PARETO TAB
// ═══════════════════════════════════════════════════════════════════════════════

function ParetoTab() {
  const [panels, setPanels] = useState<Panel[]>([]);
  const [panelId, setPanelId] = useState("");
  const [substrate, setSubstrate] = useState("");
  const [data, setData] = useState<ParetoResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => { api.panels.list().then(r => setPanels(r.results)); }, []);

  useEffect(() => {
    setLoading(true);
    setErr(null);
    api.analytics
      .pareto(panelId || undefined, substrate || undefined)
      .then(setData)
      .catch(e => setErr(e.message))
      .finally(() => setLoading(false));
  }, [panelId, substrate]);

  if (loading) return <Spinner />;
  if (err)     return <ErrBox msg={err} />;

  return (
    <div className="space-y-4">
      <div className="flex gap-3 flex-wrap">
        <select
          value={panelId}
          onChange={e => setPanelId(e.target.value)}
          className="bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-200
                     focus:outline-none focus:border-emerald-500"
        >
          <option value="">All panels</option>
          {panels.map(p => (
            <option key={p.panel_id} value={p.panel_id}>{p.panel_id}</option>
          ))}
        </select>
        <SubstrateSelect value={substrate} onChange={setSubstrate} />
      </div>

      {!data || data.items.length === 0 ? (
        <Empty msg="No defect data yet. Upload a KLARF file to get started." />
      ) : (
        <div className="space-y-4">
          <div className="flex gap-3 flex-wrap items-center">
            {data.vital_few.length > 0 && (
              <span className="px-3 py-1 rounded-full bg-amber-500/15 text-amber-400 text-xs font-medium">
                Vital few: {data.vital_few.join(", ")}
              </span>
            )}
            <span className="px-3 py-1 rounded-full bg-slate-700 text-slate-300 text-xs font-medium">
              {data.total_defects.toLocaleString()} total defects
            </span>
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <Card title="Pareto Chart — Count + Cumulative %">
              <ParetoChart data={data} />
            </Card>
            <Card title="Defect Type Rankings">
              <ParetoTable data={data} />
            </Card>
          </div>
        </div>
      )}
    </div>
  );
}

function ParetoChart({ data }: { data: ParetoResult }) {
  const { items, vital_few } = data;
  const W = 560, H = 280;
  const ml = 48, mr = 48, mt = 16, mb = 68;
  const cw = W - ml - mr;
  const ch = H - mt - mb;

  const maxCount = Math.max(...items.map(i => i.count), 1);
  const barW = cw / items.length;
  const gap = 3;
  const vitalSet = new Set(vital_few);

  const cumLine = items
    .map((item, i) => {
      const cx = ml + i * barW + barW / 2;
      const cy = mt + (1 - item.cumulative_fraction) * ch;
      return `${cx},${cy}`;
    })
    .join(" ");

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full">
      {/* Horizontal grid */}
      {[0, 0.25, 0.5, 0.75, 1.0].map(f => {
        const gy = mt + (1 - f) * ch;
        return (
          <g key={f}>
            <line x1={ml} x2={W - mr} y1={gy} y2={gy} stroke="#1e293b" strokeWidth={1} />
            <text x={ml - 5} y={gy + 4} fill="#475569" fontSize={8} textAnchor="end">
              {Math.round(f * maxCount)}
            </text>
            <text x={W - mr + 5} y={gy + 4} fill="#475569" fontSize={8}>
              {Math.round(f * 100)}%
            </text>
          </g>
        );
      })}

      {/* 80% reference */}
      {(() => {
        const y80 = mt + 0.2 * ch;
        return (
          <line x1={ml} x2={W - mr} y1={y80} y2={y80}
            stroke="#f59e0b" strokeWidth={1} strokeDasharray="5,3" opacity={0.5} />
        );
      })()}

      {/* Bars */}
      {items.map((item, i) => {
        const bx = ml + i * barW + gap / 2;
        const bh = (item.count / maxCount) * ch;
        const by = mt + ch - bh;
        const isVital = vitalSet.has(item.defect_type);
        const midX = bx + (barW - gap) / 2;
        return (
          <g key={item.defect_type}>
            <rect
              x={bx} y={by}
              width={Math.max(barW - gap, 1)} height={bh}
              fill={isVital ? "#f59e0b" : "#334155"}
              opacity={0.9}
            />
            {bh > 16 && (
              <text x={midX} y={by + bh / 2 + 4}
                fill="#e2e8f0" fontSize={9} textAnchor="middle">
                {item.count}
              </text>
            )}
            <text
              x={midX} y={mt + ch + 10}
              fill="#64748b" fontSize={8} textAnchor="end"
              transform={`rotate(-40,${midX},${mt + ch + 10})`}
            >
              {item.defect_type.replace(/_/g, " ")}
            </text>
          </g>
        );
      })}

      {/* Cumulative line */}
      <polyline points={cumLine} fill="none" stroke="#34d399" strokeWidth={2} />
      {items.map((item, i) => {
        const cx = ml + i * barW + barW / 2;
        const cy = mt + (1 - item.cumulative_fraction) * ch;
        return <circle key={i} cx={cx} cy={cy} r={3} fill="#34d399" />;
      })}

      {/* Axes */}
      <line x1={ml} y1={mt} x2={ml} y2={mt + ch} stroke="#334155" />
      <line x1={ml} y1={mt + ch} x2={W - mr} y2={mt + ch} stroke="#334155" />
      <line x1={W - mr} y1={mt} x2={W - mr} y2={mt + ch} stroke="#334155" />

      {/* Legend */}
      <g transform={`translate(${ml}, ${H - 10})`}>
        <rect x={0} y={-8} width={10} height={8} fill="#f59e0b" opacity={0.9} />
        <text x={14} y={0} fill="#94a3b8" fontSize={8}>Vital few</text>
        <rect x={72} y={-8} width={10} height={8} fill="#334155" opacity={0.9} />
        <text x={86} y={0} fill="#94a3b8" fontSize={8}>Trivial many</text>
        <line x1={168} y1={-4} x2={182} y2={-4} stroke="#34d399" strokeWidth={2} />
        <text x={186} y={0} fill="#94a3b8" fontSize={8}>Cumulative %</text>
        <line x1={268} y1={-4} x2={282} y2={-4} stroke="#f59e0b" strokeWidth={1} strokeDasharray="4,3" />
        <text x={286} y={0} fill="#94a3b8" fontSize={8}>80% line</text>
      </g>
    </svg>
  );
}

function ParetoTable({ data }: { data: ParetoResult }) {
  const vitalSet = new Set(data.vital_few);
  return (
    <div className="overflow-y-auto max-h-64">
      <table className="w-full text-xs">
        <thead>
          <tr className="text-slate-500 text-xs uppercase border-b border-slate-800">
            {["#", "Type", "Count", "Impact", "Cumul."].map(h => (
              <th key={h} className={`py-1.5 pr-3 font-medium ${h === "#" ? "text-left" : h === "Type" ? "text-left" : "text-right"}`}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {data.items.map(item => (
            <tr key={item.rank} className="border-b border-slate-800/50 hover:bg-slate-800/30">
              <td className="py-1.5 pr-3 text-slate-500">{item.rank}</td>
              <td className="py-1.5 pr-3">
                <span className={`font-medium ${vitalSet.has(item.defect_type) ? "text-amber-400" : "text-slate-300"}`}>
                  {item.defect_type}
                </span>
                {vitalSet.has(item.defect_type) && (
                  <span className="ml-1 text-amber-500 text-xs">★</span>
                )}
              </td>
              <td className="py-1.5 pr-3 text-right text-slate-300 font-mono">{item.count}</td>
              <td className="py-1.5 pr-3 text-right text-slate-400 font-mono">
                {(item.impact_fraction * 100).toFixed(1)}%
              </td>
              <td className="py-1.5 text-right text-slate-400 font-mono">
                {(item.cumulative_fraction * 100).toFixed(1)}%
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════════════
// SPC TAB
// ═══════════════════════════════════════════════════════════════════════════════

function SpcTab() {
  const [lotId, setLotId] = useState("");
  const [substrate, setSubstrate] = useState("");
  const [chart, setChart] = useState<"shewhart" | "ewma">("shewhart");
  const [data, setData] = useState<SpcResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    setErr(null);
    api.analytics
      .spc(lotId || undefined, substrate || undefined)
      .then(setData)
      .catch(e => setErr(e.message))
      .finally(() => setLoading(false));
  }, [lotId, substrate]);

  const stateColor =
    data?.process_state === "in_control" ? "text-emerald-400" :
    data?.process_state === "warning"     ? "text-amber-400"   :
    "text-red-400";

  if (loading) return <Spinner />;
  if (err)     return <ErrBox msg={err} />;

  return (
    <div className="space-y-4">
      <div className="flex gap-3 flex-wrap items-center">
        <input
          type="text"
          placeholder="Lot ID (optional)"
          value={lotId}
          onChange={e => setLotId(e.target.value)}
          className="bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-200
                     placeholder-slate-600 focus:outline-none focus:border-emerald-500"
        />
        <SubstrateSelect value={substrate} onChange={setSubstrate} />
        {data && (
          <span className={`text-sm font-semibold uppercase tracking-wide ${stateColor}`}>
            {data.process_state.replace(/_/g, " ")}
          </span>
        )}
      </div>

      {!data || data.n_points === 0 ? (
        <Empty msg="No panel data found. Upload KLARF files to populate SPC charts." />
      ) : (
        <div className="space-y-4">
          {/* Stats */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
            <StatPill label="Centerline (D₀)" value={data.centerline.toFixed(4)} />
            <StatPill label="Sigma" value={data.sigma.toFixed(4)} />
            <StatPill label="Cp" value={data.capability.cp != null ? data.capability.cp.toFixed(3) : "—"} />
            <StatPill label="Cpk" value={data.capability.cpk != null ? data.capability.cpk.toFixed(3) : "—"} />
          </div>

          {/* Alarms */}
          {data.alarms.length > 0 && (
            <div className="bg-red-500/10 border border-red-500/20 rounded-lg px-4 py-3 flex gap-3 items-start">
              <span className="text-red-400 font-bold text-sm">!</span>
              <div>
                <p className="text-red-400 text-xs font-semibold">
                  {data.alarms.length} alarm{data.alarms.length !== 1 ? "s" : ""} detected
                </p>
                <p className="text-red-300/70 text-xs mt-0.5">
                  {data.alarms[0].rule_fired}
                  {data.alarms.length > 1 && ` (+${data.alarms.length - 1} more)`}
                </p>
              </div>
            </div>
          )}

          {/* Chart selector */}
          <div className="flex gap-1 bg-slate-800 rounded-lg p-1 w-fit">
            {(["shewhart", "ewma"] as const).map(c => (
              <button
                key={c}
                onClick={() => setChart(c)}
                className={`px-3 py-1 rounded-md text-xs font-medium transition-colors ${
                  chart === c ? "bg-slate-600 text-slate-100" : "text-slate-400 hover:text-slate-200"
                }`}
              >
                {c === "shewhart" ? "Shewhart I-Chart" : "EWMA Chart"}
              </button>
            ))}
          </div>

          <Card title={
            chart === "shewhart"
              ? `Shewhart Individual Chart — Defect Density (${data.n_points} panels)`
              : `EWMA Control Chart — λ = ${data.lambda_ewma}`
          }>
            {chart === "shewhart" ? <ShewhartChart data={data} /> : <EwmaChart data={data} />}
          </Card>

          {data.capability.interpretation && (
            <p className="text-slate-500 text-xs px-1">{data.capability.interpretation}</p>
          )}
        </div>
      )}
    </div>
  );
}

function ShewhartChart({ data }: { data: SpcResult }) {
  const { points, centerline } = data;
  if (!points.length) return null;

  const W = 780, H = 220;
  const ml = 58, mr = 20, mt = 16, mb = 35;
  const cw = W - ml - mr;
  const ch = H - mt - mb;
  const n = points.length;

  const ucl = points[0].ucl_shewhart;
  const lcl = Math.max(0, points[0].lcl_shewhart);
  const allVals = points.map(p => p.value);
  const vMin = Math.max(0, Math.min(lcl * 0.85, Math.min(...allVals)));
  const vMax = Math.max(ucl * 1.08, Math.max(...allVals));

  const px = (seq: number) => ml + sc(seq, 1, Math.max(n, 2), 0, cw);
  const py = (val: number) => mt + sc(val, vMax, vMin, 0, ch);

  const linePath = points
    .map((p, i) => `${i === 0 ? "M" : "L"}${px(p.sequence)},${py(p.value)}`)
    .join(" ");

  const everyN = Math.max(1, Math.floor(n / 8));

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full">
      {/* In-control band */}
      <rect x={ml} y={py(ucl)} width={cw}
        height={Math.max(py(lcl) - py(ucl), 0)}
        fill="#22c55e" opacity={0.03} />

      {/* Horizontal control lines */}
      {[
        { v: ucl,        label: "UCL", color: "#ef4444" },
        { v: centerline, label: "CL",  color: "#22c55e" },
        { v: lcl,        label: "LCL", color: "#ef4444" },
      ].map(({ v, label, color }) => (
        <g key={label}>
          <line x1={ml} x2={W - mr} y1={py(v)} y2={py(v)}
            stroke={color} strokeWidth={1}
            strokeDasharray={label === "CL" ? "6,3" : "4,3"} opacity={0.7} />
          <text x={ml - 4} y={py(v) + 4} fill={color} fontSize={8} textAnchor="end">{label}</text>
          <text x={W - mr + 4} y={py(v) + 4} fill="#475569" fontSize={7}>{v.toFixed(4)}</text>
        </g>
      ))}

      {/* Data line */}
      <path d={linePath} fill="none" stroke="#64748b" strokeWidth={1.2} />

      {/* Data points */}
      {points.map(p => {
        const alarm = p.shewhart_signal || p.we_rules.length > 0;
        return (
          <circle
            key={p.sequence}
            cx={px(p.sequence)} cy={py(p.value)}
            r={alarm ? 4.5 : 2.5}
            fill={alarm ? "#ef4444" : "#94a3b8"}
            stroke={alarm ? "#fca5a5" : "none"}
            strokeWidth={1}
          />
        );
      })}

      {/* Axes */}
      <line x1={ml} y1={mt} x2={ml} y2={mt + ch} stroke="#334155" />
      <line x1={ml} y1={mt + ch} x2={W - mr} y2={mt + ch} stroke="#334155" />

      {/* X labels */}
      {points
        .filter((_, i) => i % everyN === 0)
        .map(p => (
          <text key={p.sequence} x={px(p.sequence)} y={mt + ch + 14}
            fill="#475569" fontSize={7} textAnchor="middle">
            {p.panel_id.length > 8 ? p.panel_id.slice(-8) : p.panel_id}
          </text>
        ))}

      {/* Y ticks */}
      {[vMin, (vMin + vMax) / 2, vMax].map((v, i) => (
        <text key={i} x={ml - 5} y={py(v) + 4} fill="#475569" fontSize={7} textAnchor="end">
          {v.toFixed(4)}
        </text>
      ))}

      {/* Axis label */}
      <text x={10} y={mt + ch / 2} fill="#475569" fontSize={8} textAnchor="middle"
        transform={`rotate(-90,10,${mt + ch / 2})`}>D₀ /mm²</text>
    </svg>
  );
}

function EwmaChart({ data }: { data: SpcResult }) {
  const { points, centerline } = data;
  if (!points.length) return null;

  const W = 780, H = 220;
  const ml = 58, mr = 20, mt = 16, mb = 35;
  const cw = W - ml - mr;
  const ch = H - mt - mb;
  const n = points.length;

  const allVals = [
    ...points.map(p => p.ewma),
    ...points.map(p => p.ucl_ewma),
    ...points.map(p => Math.max(0, p.lcl_ewma)),
    ...points.map(p => p.value),
  ];
  const vMin = Math.max(0, Math.min(...allVals) * 0.9);
  const vMax = Math.max(...allVals) * 1.05;

  const px = (seq: number) => ml + sc(seq, 1, Math.max(n, 2), 0, cw);
  const py = (val: number) => mt + sc(val, vMax, vMin, 0, ch);

  const rawPath  = points.map((p, i) => `${i === 0 ? "M" : "L"}${px(p.sequence)},${py(p.value)}`).join(" ");
  const ewmaPath = points.map((p, i) => `${i === 0 ? "M" : "L"}${px(p.sequence)},${py(p.ewma)}`).join(" ");
  const uclPath  = points.map((p, i) => `${i === 0 ? "M" : "L"}${px(p.sequence)},${py(p.ucl_ewma)}`).join(" ");
  const lclPath  = points.map((p, i) => `${i === 0 ? "M" : "L"}${px(p.sequence)},${py(Math.max(0, p.lcl_ewma))}`).join(" ");

  const everyN = Math.max(1, Math.floor(n / 8));

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full">
      {/* CL */}
      <line x1={ml} x2={W - mr} y1={py(centerline)} y2={py(centerline)}
        stroke="#22c55e" strokeWidth={1} strokeDasharray="6,3" opacity={0.7} />
      <text x={ml - 4} y={py(centerline) + 4} fill="#22c55e" fontSize={8} textAnchor="end">CL</text>

      {/* UCL/LCL bands */}
      <path d={uclPath} fill="none" stroke="#ef4444" strokeWidth={1} strokeDasharray="4,3" opacity={0.7} />
      <path d={lclPath} fill="none" stroke="#ef4444" strokeWidth={1} strokeDasharray="4,3" opacity={0.7} />

      {/* Raw values (faint) */}
      <path d={rawPath} fill="none" stroke="#1e293b" strokeWidth={1.5} />
      {points.map(p => (
        <circle key={p.sequence} cx={px(p.sequence)} cy={py(p.value)} r={1.5} fill="#334155" />
      ))}

      {/* EWMA line */}
      <path d={ewmaPath} fill="none" stroke="#3b82f6" strokeWidth={2} />

      {/* Alarm points */}
      {points.filter(p => p.ewma_signal).map(p => (
        <circle key={p.sequence}
          cx={px(p.sequence)} cy={py(p.ewma)} r={4.5}
          fill="#ef4444" stroke="#fca5a5" strokeWidth={1} />
      ))}

      {/* Axes */}
      <line x1={ml} y1={mt} x2={ml} y2={mt + ch} stroke="#334155" />
      <line x1={ml} y1={mt + ch} x2={W - mr} y2={mt + ch} stroke="#334155" />

      {/* X labels */}
      {points.filter((_, i) => i % everyN === 0).map(p => (
        <text key={p.sequence} x={px(p.sequence)} y={mt + ch + 14}
          fill="#475569" fontSize={7} textAnchor="middle">
          {p.panel_id.length > 8 ? p.panel_id.slice(-8) : p.panel_id}
        </text>
      ))}

      {/* Y ticks */}
      {[vMin, (vMin + vMax) / 2, vMax].map((v, i) => (
        <text key={i} x={ml - 5} y={py(v) + 4} fill="#475569" fontSize={7} textAnchor="end">
          {v.toFixed(4)}
        </text>
      ))}

      {/* Axis label */}
      <text x={10} y={mt + ch / 2} fill="#475569" fontSize={8} textAnchor="middle"
        transform={`rotate(-90,10,${mt + ch / 2})`}>D₀ /mm²</text>

      {/* Legend */}
      <g transform={`translate(${ml + 8}, ${H - 10})`}>
        <line x1={0} y1={-4} x2={14} y2={-4} stroke="#334155" strokeWidth={1.5} />
        <text x={18} y={0} fill="#64748b" fontSize={8}>Raw</text>
        <line x1={48} y1={-4} x2={62} y2={-4} stroke="#3b82f6" strokeWidth={2} />
        <text x={66} y={0} fill="#64748b" fontSize={8}>EWMA</text>
        <line x1={100} y1={-4} x2={114} y2={-4} stroke="#ef4444" strokeWidth={1} strokeDasharray="4,3" />
        <text x={118} y={0} fill="#64748b" fontSize={8}>UCL / LCL</text>
        <circle cx={177} cy={-4} r={3.5} fill="#ef4444" />
        <text x={184} y={0} fill="#64748b" fontSize={8}>Signal</text>
      </g>
    </svg>
  );
}

// ═══════════════════════════════════════════════════════════════════════════════
// LOT TREND TAB
// ═══════════════════════════════════════════════════════════════════════════════

function TrendTab() {
  const [substrate, setSubstrate] = useState("");
  const [data, setData] = useState<TrendResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    setErr(null);
    api.analytics
      .trend(substrate || undefined)
      .then(setData)
      .catch(e => setErr(e.message))
      .finally(() => setLoading(false));
  }, [substrate]);

  if (loading) return <Spinner />;
  if (err)     return <ErrBox msg={err} />;

  const dirColor =
    data?.direction === "improving" ? "text-emerald-400" :
    data?.direction === "degrading" ? "text-red-400" :
    "text-slate-400";

  return (
    <div className="space-y-4">
      <div className="flex gap-3 items-center flex-wrap">
        <SubstrateSelect value={substrate} onChange={setSubstrate} />
        {data && data.n_lots > 0 && (
          <span className={`text-sm font-semibold capitalize ${dirColor}`}>
            {data.direction}
          </span>
        )}
      </div>

      {!data || data.n_lots === 0 ? (
        <Empty msg="No lot data found. Panels need lot_id assignments for trend analysis." />
      ) : (
        <div className="space-y-4">
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
            <StatPill label="Lots analyzed" value={String(data.n_lots)} />
            <StatPill
              label="Mean yield"
              value={data.mean_yield != null ? `${(data.mean_yield * 100).toFixed(1)}%` : "—"}
            />
            <StatPill label="R²" value={data.r_squared.toFixed(3)} />
            <StatPill label="Trend slope" value={data.slope.toFixed(5)} />
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <Card title="Yield % — Lot over Lot">
              <TrendChart data={data} metric="yield" />
            </Card>
            <Card title="Defect Density — Lot over Lot">
              <TrendChart data={data} metric="density" />
            </Card>
          </div>
        </div>
      )}
    </div>
  );
}

function TrendChart({ data, metric }: { data: TrendResult; metric: "yield" | "density" }) {
  const pts = data.data_points;
  if (!pts.length) return null;

  const W = 560, H = 220;
  const ml = 58, mr = 16, mt = 16, mb = 40;
  const cw = W - ml - mr;
  const ch = H - mt - mb;
  const n = pts.length;

  const vals = metric === "yield"
    ? pts.map(p => p.avg_yield_negbinom ?? 0)
    : pts.map(p => p.avg_defect_density);

  const vMin = Math.max(0, Math.min(...vals) * 0.92);
  const vMax = Math.max(...vals) * 1.06;

  const px = (seq: number) => ml + sc(seq, 1, Math.max(n, 2), 0, cw);
  const py = (val: number) => mt + sc(val, vMax, vMin, 0, ch);

  const linePath = pts.map((p, i) => {
    const v = metric === "yield" ? (p.avg_yield_negbinom ?? 0) : p.avg_defect_density;
    return `${i === 0 ? "M" : "L"}${px(p.sequence)},${py(v)}`;
  }).join(" ");

  // Regression line — only for yield chart
  const regY1 = data.intercept + data.slope;
  const regY2 = data.intercept + data.slope * n;
  const regColor = data.direction === "improving" ? "#22c55e" : "#ef4444";

  const yTickFmt = (v: number) =>
    metric === "yield" ? `${(v * 100).toFixed(0)}%` : v.toFixed(4);

  const everyN = Math.max(1, Math.floor(n / 6));

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full">
      {/* Grid */}
      {[vMin, (vMin + vMax) / 2, vMax].map((v, i) => (
        <g key={i}>
          <line x1={ml} x2={W - mr} y1={py(v)} y2={py(v)} stroke="#1e293b" strokeWidth={1} />
          <text x={ml - 5} y={py(v) + 4} fill="#475569" fontSize={8} textAnchor="end">
            {yTickFmt(v)}
          </text>
        </g>
      ))}

      {/* Regression line */}
      {metric === "yield" && (
        <line
          x1={px(1)} y1={py(Math.max(vMin, Math.min(vMax, regY1)))}
          x2={px(n)} y2={py(Math.max(vMin, Math.min(vMax, regY2)))}
          stroke={regColor} strokeWidth={1.5} strokeDasharray="5,3" opacity={0.65} />
      )}

      {/* Data line */}
      <path d={linePath} fill="none" stroke="#94a3b8" strokeWidth={1.5} />

      {/* Data points */}
      {pts.map((p, i) => {
        const v = metric === "yield" ? (p.avg_yield_negbinom ?? 0) : p.avg_defect_density;
        const isExcursion = p.lot_status === "excursion";
        return (
          <circle key={i}
            cx={px(p.sequence)} cy={py(v)}
            r={isExcursion ? 5.5 : 3}
            fill={isExcursion ? "#ef4444" : "#3b82f6"}
            stroke={isExcursion ? "#fca5a5" : "none"}
            strokeWidth={1} />
        );
      })}

      {/* Axes */}
      <line x1={ml} y1={mt} x2={ml} y2={mt + ch} stroke="#334155" />
      <line x1={ml} y1={mt + ch} x2={W - mr} y2={mt + ch} stroke="#334155" />

      {/* X labels */}
      {pts.filter((_, i) => i % everyN === 0).map((p, i) => (
        <text key={i} x={px(p.sequence)} y={mt + ch + 14}
          fill="#475569" fontSize={7} textAnchor="middle">
          {p.lot_id.length > 10 ? p.lot_id.slice(-10) : p.lot_id}
        </text>
      ))}

      {/* Legend */}
      <g transform={`translate(${ml + 8}, ${H - 8})`}>
        <circle cx={5} cy={-4} r={3} fill="#3b82f6" />
        <text x={12} y={0} fill="#64748b" fontSize={8}>Normal</text>
        <circle cx={58} cy={-4} r={5} fill="#ef4444" />
        <text x={67} y={0} fill="#64748b" fontSize={8}>Excursion</text>
        {metric === "yield" && (
          <>
            <line x1={126} y1={-4} x2={140} y2={-4} stroke={regColor} strokeWidth={1.5} strokeDasharray="4,3" />
            <text x={144} y={0} fill="#64748b" fontSize={8}>Regression</text>
          </>
        )}
      </g>
    </svg>
  );
}

// ═══════════════════════════════════════════════════════════════════════════════
// DEFECT SCATTER TAB
// ═══════════════════════════════════════════════════════════════════════════════

const DEFECT_COLORS: Record<string, string> = {
  particle:      "#3b82f6",
  scratch:       "#ef4444",
  void:          "#f59e0b",
  bridge:        "#a855f7",
  open:          "#06b6d4",
  contamination: "#22c55e",
  crack:         "#f97316",
  pit:           "#ec4899",
  tgv_chipping:  "#84cc16",
  tgv_sidewall_crack: "#e11d48",
  tgv_fill_void: "#fb923c",
};

function defectColor(type: string): string {
  return DEFECT_COLORS[type] ?? "#64748b";
}

function ScatterTab() {
  const [panels, setPanels] = useState<Panel[]>([]);
  const [panelId, setPanelId] = useState("");
  const [system, setSystem] = useState("system_a");
  const [defects, setDefects] = useState<Defect[]>([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => { api.panels.list().then(r => setPanels(r.results)); }, []);

  useEffect(() => {
    if (!panelId) return;
    setLoading(true);
    setErr(null);
    api.defects.list(panelId, system, 500)
      .then(r => setDefects(r.results))
      .catch(e => setErr(e.message))
      .finally(() => setLoading(false));
  }, [panelId, system]);

  const types = [...new Set(defects.map(d => d.defect_type))];

  return (
    <div className="space-y-4">
      <div className="flex gap-3 flex-wrap items-center">
        <select
          value={panelId}
          onChange={e => { setPanelId(e.target.value); setDefects([]); }}
          className="bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-200
                     focus:outline-none focus:border-emerald-500"
        >
          <option value="">Select panel…</option>
          {panels.map(p => (
            <option key={p.panel_id} value={p.panel_id}>{p.panel_id}</option>
          ))}
        </select>
        <select
          value={system}
          onChange={e => setSystem(e.target.value)}
          className="bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-200
                     focus:outline-none focus:border-emerald-500"
        >
          <option value="system_a">System A</option>
          <option value="system_b">System B</option>
        </select>
        {defects.length > 0 && (
          <span className="text-slate-500 text-sm">{defects.length} defects</span>
        )}
      </div>

      {!panelId ? (
        <Empty msg="Select a panel to view its defect spatial map." />
      ) : loading ? (
        <Spinner />
      ) : err ? (
        <ErrBox msg={err} />
      ) : defects.length === 0 ? (
        <Empty msg="No defects found for this panel / system." />
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
          <div className="lg:col-span-2">
            <Card title={`Defect Spatial Map — ${panelId}`}>
              <ScatterChart defects={defects} />
            </Card>
          </div>
          <div className="space-y-4">
            <Card title="Defect Type Legend">
              {types.map(t => {
                const count = defects.filter(d => d.defect_type === t).length;
                const pct   = ((count / defects.length) * 100).toFixed(1);
                return (
                  <div key={t} className="flex items-center gap-2 py-1">
                    <span
                      className="w-3 h-3 rounded-full shrink-0"
                      style={{ background: defectColor(t) }}
                    />
                    <span className="text-slate-300 text-xs truncate flex-1">{t}</span>
                    <span className="text-slate-500 text-xs font-mono shrink-0">
                      {count} ({pct}%)
                    </span>
                  </div>
                );
              })}
            </Card>
            <Card title="Size Distribution">
              <SizeHistogram defects={defects} />
            </Card>
            <Card title="Stats">
              <div className="space-y-1.5 text-xs">
                {[
                  ["Total", defects.length],
                  ["Types", types.length],
                  ["Avg size", `${(defects.reduce((s, d) => s + d.size, 0) / defects.length).toFixed(3)} mm`],
                  ["Max size", `${Math.max(...defects.map(d => d.size)).toFixed(3)} mm`],
                  ["Avg confidence", `${(defects.reduce((s, d) => s + d.confidence_score, 0) / defects.length * 100).toFixed(1)}%`],
                ].map(([label, value]) => (
                  <div key={String(label)} className="flex justify-between">
                    <span className="text-slate-500">{label}</span>
                    <span className="text-slate-300 font-mono">{value}</span>
                  </div>
                ))}
              </div>
            </Card>
          </div>
        </div>
      )}
    </div>
  );
}

function ScatterChart({ defects }: { defects: Defect[] }) {
  const SIZE = 460;
  const pad  = 42;
  const area = SIZE - 2 * pad;

  const xs = defects.map(d => d.x);
  const ys = defects.map(d => d.y);
  const xMin = Math.min(...xs), xMax = Math.max(...xs);
  const yMin = Math.min(...ys), yMax = Math.max(...ys);
  const xRange = xMax - xMin || 1;
  const yRange = yMax - yMin || 1;

  const px = (x: number) => pad + ((x - xMin) / xRange) * area;
  const py = (y: number) => pad + (1 - (y - yMin) / yRange) * area;

  const gridTicks = [0, 0.25, 0.5, 0.75, 1.0];

  return (
    <svg viewBox={`0 0 ${SIZE} ${SIZE}`} className="w-full aspect-square">
      <rect x={pad} y={pad} width={area} height={area} fill="#0f172a" />

      {gridTicks.map(f => {
        const gx = pad + f * area;
        const gy = pad + f * area;
        const xv = xMin + f * xRange;
        const yv = yMax - f * yRange;
        return (
          <g key={f}>
            <line x1={gx} y1={pad} x2={gx} y2={pad + area} stroke="#1e293b" strokeWidth={1} />
            <line x1={pad} y1={gy} x2={pad + area} y2={gy} stroke="#1e293b" strokeWidth={1} />
            <text x={gx} y={SIZE - 6} fill="#334155" fontSize={7} textAnchor="middle">
              {xv.toFixed(1)}
            </text>
            <text x={pad - 4} y={gy + 3} fill="#334155" fontSize={7} textAnchor="end">
              {yv.toFixed(1)}
            </text>
          </g>
        );
      })}

      <text x={SIZE / 2} y={SIZE - 0} fill="#475569" fontSize={9} textAnchor="middle">X (mm)</text>
      <text x={9} y={SIZE / 2} fill="#475569" fontSize={9} textAnchor="middle"
        transform={`rotate(-90,9,${SIZE / 2})`}>Y (mm)</text>

      {defects.map((d, i) => (
        <circle
          key={i}
          cx={px(d.x)} cy={py(d.y)}
          r={Math.min(5, Math.max(1.5, d.size * 1.5))}
          fill={defectColor(d.defect_type)}
          opacity={0.72}
        />
      ))}

      <rect x={pad} y={pad} width={area} height={area} fill="none" stroke="#334155" />
    </svg>
  );
}

function SizeHistogram({ defects }: { defects: Defect[] }) {
  const sizes   = defects.map(d => d.size);
  const maxSize = Math.max(...sizes, 0.001);
  const bins    = 10;
  const binW    = maxSize / bins;
  const counts  = Array<number>(bins).fill(0);
  sizes.forEach(s => {
    const bi = Math.min(Math.floor(s / binW), bins - 1);
    counts[bi]++;
  });
  const maxCount = Math.max(...counts, 1);

  const W = 240, H = 100;
  const ml = 30, mb = 22, mt = 5, mr = 5;
  const cw = W - ml - mr;
  const ch = H - mt - mb;
  const bw = cw / bins;

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full">
      {counts.map((c, i) => {
        const bh = (c / maxCount) * ch;
        return (
          <rect key={i}
            x={ml + i * bw + 1} y={mt + ch - bh}
            width={Math.max(bw - 2, 1)} height={bh}
            fill="#3b82f6" opacity={0.7} />
        );
      })}
      <line x1={ml} y1={mt} x2={ml} y2={mt + ch} stroke="#334155" />
      <line x1={ml} y1={mt + ch} x2={W - mr} y2={mt + ch} stroke="#334155" />
      <text x={ml - 4} y={mt + 4} fill="#475569" fontSize={7} textAnchor="end">{maxCount}</text>
      <text x={ml - 4} y={mt + ch + 4} fill="#475569" fontSize={7} textAnchor="end">0</text>
      <text x={ml} y={H - 3} fill="#475569" fontSize={7}>0</text>
      <text x={W - mr} y={H - 3} fill="#475569" fontSize={7} textAnchor="end">
        {maxSize.toFixed(2)}mm
      </text>
      <text x={(ml + W - mr) / 2} y={H - 3} fill="#475569" fontSize={7} textAnchor="middle">
        Defect size (mm)
      </text>
    </svg>
  );
}
