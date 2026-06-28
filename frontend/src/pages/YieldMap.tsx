import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../api";
import type { Panel, SpatialYield, YieldEstimate, YieldModel } from "../types";
import StatCard from "../components/StatCard";
import DieHeatmap from "../components/DieHeatmap";
import YieldChart from "../components/YieldChart";

export default function YieldMap() {
  const [params, setParams] = useSearchParams();
  const [panels, setPanels]   = useState<Panel[]>([]);
  const [panelId, setPanelId] = useState(params.get("panel") ?? "");
  const [model, setModel]     = useState<YieldModel>("negbinom");
  const [spatial, setSpatial] = useState<SpatialYield | null>(null);
  const [estimate, setEstimate] = useState<YieldEstimate | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError]     = useState<string | null>(null);

  useEffect(() => {
    api.panels.list().then(r => setPanels(r.panels));
  }, []);

  useEffect(() => {
    if (!panelId) return;
    setLoading(true);
    setError(null);
    Promise.all([api.yield.spatial(panelId), api.yield.estimate(panelId)])
      .then(([s, e]) => { setSpatial(s); setEstimate(e); })
      .catch(e => setError(e.message))
      .finally(() => setLoading(false));
  }, [panelId]);

  const spatialY  = spatial ? spatial[`spatial_yield_${model}` as keyof SpatialYield] as number : null;
  const globalY   = spatial ? spatial[`global_yield_${model}` as keyof SpatialYield]  as number : null;

  const chartData = spatial
    ? [
        { name: "Poisson",  value: spatial.spatial_yield_poisson  },
        { name: "Murphy",   value: spatial.spatial_yield_murphy   },
        { name: "NegBinom", value: spatial.spatial_yield_negbinom },
      ]
    : [];

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold text-slate-100">Yield Map</h1>

        <div className="flex gap-3">
          {/* Panel selector */}
          <select
            value={panelId}
            onChange={e => { setPanelId(e.target.value); setParams({ panel: e.target.value }); }}
            className="bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-200
                       focus:outline-none focus:border-emerald-500"
          >
            <option value="">Select panel…</option>
            {panels.map(p => (
              <option key={p.panel_id} value={p.panel_id}>{p.panel_id}</option>
            ))}
          </select>

          {/* Model selector */}
          <select
            value={model}
            onChange={e => setModel(e.target.value as YieldModel)}
            className="bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-200
                       focus:outline-none focus:border-emerald-500"
          >
            <option value="poisson">Poisson</option>
            <option value="murphy">Murphy</option>
            <option value="negbinom">Neg. Binomial</option>
          </select>
        </div>
      </div>

      {!panelId && (
        <div className="bg-slate-900 border border-slate-800 rounded-xl px-5 py-10 text-center text-slate-500">
          Select a panel above to view its spatial yield map.
        </div>
      )}

      {loading && <Spinner />}
      {error   && <ErrorBox msg={error} />}

      {spatial && estimate && (
        <>
          {/* Stats row */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
            <StatCard
              label="Spatial Yield"
              value={`${((spatialY ?? 0) * 100).toFixed(1)}%`}
              sub={`${model} model`}
              accent="green"
            />
            <StatCard
              label="Global Yield"
              value={`${((globalY ?? 0) * 100).toFixed(1)}%`}
              sub="from mean D₀"
              accent="blue"
            />
            <StatCard
              label="Yield Gain"
              value={`+${((spatial[`yield_gain_${model === "poisson" ? "poisson" : "negbinom"}` as keyof SpatialYield] as number) * 100).toFixed(2)}%`}
              sub="spatial vs global"
              accent="amber"
            />
            <StatCard
              label="CV(D₀)"
              value={spatial.cv_d0.toFixed(3)}
              sub={spatial.cv_d0 < 0.1 ? "uniform" : "non-uniform"}
              accent={spatial.cv_d0 < 0.1 ? "green" : "amber"}
            />
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            {/* Die heatmap */}
            <div className="bg-slate-900 border border-slate-800 rounded-xl p-5">
              <h2 className="text-sm font-semibold text-slate-200 mb-4">
                Per-Die Yield Heatmap
                <span className="ml-2 text-slate-500 text-xs font-normal">
                  {spatial.n_active_dies} active dies
                </span>
              </h2>
              <DieHeatmap dies={spatial.die_yields} model={model} />
            </div>

            {/* Yield model comparison */}
            <div className="bg-slate-900 border border-slate-800 rounded-xl p-5">
              <h2 className="text-sm font-semibold text-slate-200 mb-4">
                Spatial Yield by Model
              </h2>
              <YieldChart
                data={chartData}
                referenceValue={globalY ?? undefined}
                referenceLabel="Global D₀"
              />

              <div className="mt-4 space-y-2 text-xs text-slate-400">
                <Row label="Mean D₀"   value={`${estimate.D0.toFixed(4)} /mm²`} />
                <Row label="Std D₀"    value={`${spatial.std_d0.toFixed(4)} /mm²`} />
                <Row label="Active dies" value={`${spatial.n_active_dies}`} />
                <Row label="Die area"  value={`${spatial.die_area_mm2.toFixed(1)} mm²`} />
                {spatial.critical_area_fraction != null && (
                  <Row label="CA fraction" value={`${(spatial.critical_area_fraction * 100).toFixed(1)}%`} />
                )}
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between">
      <span className="text-slate-500">{label}</span>
      <span className="text-slate-300 font-mono">{value}</span>
    </div>
  );
}

function Spinner() {
  return (
    <div className="flex items-center justify-center h-32">
      <div className="w-8 h-8 border-2 border-emerald-500 border-t-transparent rounded-full animate-spin" />
    </div>
  );
}

function ErrorBox({ msg }: { msg: string }) {
  return (
    <div className="bg-red-500/10 border border-red-500/30 rounded-xl px-5 py-4 text-red-400 text-sm">{msg}</div>
  );
}
