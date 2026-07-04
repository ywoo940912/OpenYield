import { useEffect, useState } from "react";
import { api } from "../api";
import type { Panel, DefectImageMeta, PanelGallery } from "../types";

const TYPE_COLORS: Record<string, string> = {
  particle:      "bg-sky-500/20 text-sky-400",
  scratch:       "bg-amber-500/20 text-amber-400",
  void:          "bg-violet-500/20 text-violet-400",
  pit:           "bg-red-500/20 text-red-400",
  contamination: "bg-orange-500/20 text-orange-400",
  mura:          "bg-teal-500/20 text-teal-400",
  pinhole:       "bg-pink-500/20 text-pink-400",
};

function typeBadge(t: string) {
  return TYPE_COLORS[t] ?? "bg-slate-700 text-slate-400";
}

// ── Enlarged defect modal ──────────────────────────────────────────────────────

function Modal({ d, onClose }: { d: DefectImageMeta; onClose: () => void }) {
  return (
    <div
      className="fixed inset-0 bg-black/70 flex items-center justify-center z-50"
      onClick={onClose}
    >
      <div
        className="bg-slate-900 border border-slate-700 rounded-2xl p-6 max-w-sm w-full mx-4"
        onClick={e => e.stopPropagation()}
      >
        <div className="flex items-start justify-between mb-4">
          <div>
            <div className="flex items-center gap-2">
              <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${typeBadge(d.defect_type)}`}>
                {d.defect_type}
              </span>
              <span className="text-xs text-slate-500">#{d.defect_id}</span>
            </div>
            <div className="text-xs text-slate-500 mt-1">
              Die ({d.component_row}, {d.component_col})
            </div>
          </div>
          <button onClick={onClose} className="text-slate-500 hover:text-slate-200 text-xl leading-none">×</button>
        </div>

        {/* Large image — 256px rendered via CSS scale */}
        <div className="flex justify-center mb-4">
          <img
            src={api.images.renderUrl(d.defect_id)}
            alt={d.defect_type}
            className="rounded-lg"
            style={{
              imageRendering: "pixelated",
              width: 256,
              height: 256,
            }}
          />
        </div>

        <div className="space-y-2 text-xs">
          {[
            ["Defect ID",   String(d.defect_id)],
            ["Type",        d.defect_type],
            ["Size (mm)",   d.size.toFixed(4)],
            ["Confidence",  `${(d.confidence_score * 100).toFixed(1)}%`],
            ["Die (row,col)", `(${d.component_row}, ${d.component_col})`],
          ].map(([label, value]) => (
            <div key={label} className="flex justify-between">
              <span className="text-slate-500">{label}</span>
              <span className="text-slate-200 font-mono">{value}</span>
            </div>
          ))}
        </div>

        <div className="mt-4 pt-4 border-t border-slate-800 text-xs text-slate-600">
          64×64 px grayscale patch · Procedurally generated · Deterministic seed
        </div>
      </div>
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function DefectGallery() {
  const [panels,  setPanels]  = useState<Panel[]>([]);
  const [panelId, setPanelId] = useState("");
  const [gallery, setGallery] = useState<PanelGallery | null>(null);
  const [loading, setLoading] = useState(false);
  const [err,     setErr]     = useState<string | null>(null);
  const [filter,  setFilter]  = useState<string>("");
  const [selected, setSelected] = useState<DefectImageMeta | null>(null);

  useEffect(() => {
    api.panels.list().then(r => setPanels(r.results)).catch(() => {});
  }, []);

  function loadGallery(pid: string) {
    if (!pid) return;
    setLoading(true);
    setErr(null);
    setGallery(null);
    api.images.gallery(pid, 200)
      .then(setGallery)
      .catch(e => setErr(e.message))
      .finally(() => setLoading(false));
  }

  const types = gallery
    ? [...new Set(gallery.defects.map(d => d.defect_type))].sort()
    : [];

  const visible = gallery
    ? (filter ? gallery.defects.filter(d => d.defect_type === filter) : gallery.defects)
    : [];

  return (
    <div className="space-y-5">
      {selected && <Modal d={selected} onClose={() => setSelected(null)} />}

      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-slate-100">Defect Gallery</h1>
          <p className="text-xs text-slate-500 mt-0.5">
            Synthetic 64×64 px grayscale defect image patches — procedurally generated, deterministic per defect ID
          </p>
        </div>
      </div>

      {/* Panel selector */}
      <div className="bg-slate-900 border border-slate-800 rounded-xl p-5">
        <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-500 mb-3">Select Panel</h2>
        <div className="flex gap-3 flex-wrap">
          <select
            value={panelId}
            onChange={e => { setPanelId(e.target.value); setFilter(""); }}
            className="bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-emerald-500 flex-1 max-w-xs"
          >
            <option value="">— Select a panel —</option>
            {panels.map(p => (
              <option key={p.panel_id} value={p.panel_id}>
                {p.panel_id} ({p.substrate_type}, {p.defect_count} defects)
              </option>
            ))}
          </select>
          <button
            onClick={() => loadGallery(panelId)}
            disabled={!panelId || loading}
            className="px-4 py-2 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-40 text-white text-sm font-medium rounded-lg transition-colors"
          >
            {loading ? "Loading…" : "Load Gallery"}
          </button>
        </div>
      </div>

      {err && (
        <div className="bg-red-500/10 border border-red-500/30 rounded-xl px-5 py-4 text-red-400 text-sm">{err}</div>
      )}

      {gallery && (
        <>
          {/* Stats + type filter */}
          <div className="flex items-center gap-3 flex-wrap">
            <span className="text-sm text-slate-300 font-medium">
              {gallery.total} defects
              {filter && ` · ${visible.length} ${filter}`}
            </span>
            <div className="flex gap-1.5 flex-wrap ml-2">
              <button
                onClick={() => setFilter("")}
                className={`text-xs px-2.5 py-1 rounded-full border transition-colors ${
                  filter === ""
                    ? "bg-emerald-500/20 border-emerald-500 text-emerald-400"
                    : "border-slate-700 text-slate-500 hover:border-slate-500"
                }`}
              >
                All
              </button>
              {types.map(t => (
                <button
                  key={t}
                  onClick={() => setFilter(f => f === t ? "" : t)}
                  className={`text-xs px-2.5 py-1 rounded-full border transition-colors ${
                    filter === t
                      ? `${typeBadge(t)} border-current`
                      : "border-slate-700 text-slate-500 hover:border-slate-500"
                  }`}
                >
                  {t}
                </button>
              ))}
            </div>
          </div>

          {/* Grid */}
          <div className="grid grid-cols-3 sm:grid-cols-5 md:grid-cols-7 lg:grid-cols-9 xl:grid-cols-12 gap-2">
            {visible.map(d => (
              <button
                key={d.defect_id}
                onClick={() => setSelected(d)}
                className="group relative rounded-lg overflow-hidden border border-slate-800 hover:border-emerald-500 transition-colors bg-slate-900"
                title={`${d.defect_type} · ${d.size.toFixed(3)} mm · die (${d.component_row},${d.component_col})`}
              >
                <img
                  src={api.images.renderUrl(d.defect_id)}
                  alt={d.defect_type}
                  className="w-full aspect-square"
                  loading="lazy"
                  style={{ imageRendering: "pixelated" }}
                />
                {/* Type label overlay on hover */}
                <div className="absolute inset-0 bg-black/0 group-hover:bg-black/40 transition-colors flex items-end">
                  <span className="w-full px-1 py-0.5 text-center text-xs text-white opacity-0 group-hover:opacity-100 transition-opacity truncate bg-black/50">
                    {d.defect_type}
                  </span>
                </div>
              </button>
            ))}
          </div>

          {visible.length === 0 && (
            <div className="text-center py-10 text-slate-500 text-sm">
              No defects of type "{filter}" on this panel.
            </div>
          )}

          {/* Type breakdown bar */}
          <div className="bg-slate-900 border border-slate-800 rounded-xl p-5">
            <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-500 mb-4">
              Defect Type Breakdown
            </h2>
            <div className="space-y-2">
              {types.map(t => {
                const count = gallery.defects.filter(d => d.defect_type === t).length;
                const frac  = count / gallery.total;
                return (
                  <div key={t} className="flex items-center gap-3">
                    <span className={`text-xs px-2 py-0.5 rounded-full font-medium w-28 text-center ${typeBadge(t)}`}>
                      {t}
                    </span>
                    <div className="flex-1 h-2 bg-slate-800 rounded-full overflow-hidden">
                      <div
                        className="h-full rounded-full bg-emerald-500"
                        style={{ width: `${frac * 100}%` }}
                      />
                    </div>
                    <span className="text-xs text-slate-400 w-16 text-right font-mono">
                      {count} ({(frac * 100).toFixed(1)}%)
                    </span>
                  </div>
                );
              })}
            </div>
          </div>
        </>
      )}

      {!gallery && !loading && (
        <div className="flex flex-col items-center justify-center py-20 text-center">
          <div className="text-5xl mb-4">🔬</div>
          <p className="text-slate-400 text-sm font-medium">Select a panel to view its defect image patches</p>
          <p className="text-slate-600 text-xs mt-1 max-w-sm">
            Each thumbnail is a 64×64 px grayscale patch generated deterministically from the defect's type and size — ready for CNN classifier training.
          </p>
        </div>
      )}
    </div>
  );
}
