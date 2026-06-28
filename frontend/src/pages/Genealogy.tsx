import { useState } from "react";
import { api } from "../api";
import type { LotLineage } from "../types";
import LotTree from "../components/LotTree";
import StatCard from "../components/StatCard";

export default function Genealogy() {
  const [lotId, setLotId]     = useState("");
  const [lineage, setLineage] = useState<LotLineage | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError]     = useState<string | null>(null);

  const search = () => {
    if (!lotId.trim()) return;
    setLoading(true);
    setError(null);
    setLineage(null);
    api.genealogy.lineage(lotId.trim())
      .then(setLineage)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false));
  };

  const relCounts = lineage
    ? lineage.edges.reduce<Record<string, number>>((acc, e) => {
        acc[e.relation_type] = (acc[e.relation_type] ?? 0) + 1;
        return acc;
      }, {})
    : {};

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-bold text-slate-100">Lot Genealogy</h1>

      {/* Search */}
      <div className="flex gap-3">
        <input
          type="text"
          placeholder="Enter lot ID (e.g. LOT_2024_001)"
          value={lotId}
          onChange={e => setLotId(e.target.value)}
          onKeyDown={e => e.key === "Enter" && search()}
          className="flex-1 bg-slate-800 border border-slate-700 rounded-lg px-4 py-2.5 text-sm
                     text-slate-200 placeholder-slate-600 focus:outline-none focus:border-emerald-500"
        />
        <button
          onClick={search}
          disabled={loading || !lotId.trim()}
          className="bg-emerald-600 hover:bg-emerald-500 disabled:opacity-40 text-white
                     px-5 py-2.5 rounded-lg text-sm font-medium transition-colors"
        >
          {loading ? "Loading…" : "Search"}
        </button>
      </div>

      {error && (
        <div className="bg-red-500/10 border border-red-500/30 rounded-xl px-5 py-4 text-red-400 text-sm">
          {error}
        </div>
      )}

      {lineage && (
        <>
          {/* Stats */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
            <StatCard label="Lot ID"      value={lineage.lot_id}               accent="blue" />
            <StatCard label="Depth"       value={lineage.depth}                accent="green" />
            <StatCard label="Ancestors"   value={lineage.ancestors.length}     accent="amber" />
            <StatCard label="Descendants" value={lineage.descendants.length}   accent="default" />
          </div>

          {/* Relation type breakdown */}
          {Object.keys(relCounts).length > 0 && (
            <div className="bg-slate-900 border border-slate-800 rounded-xl p-5">
              <h2 className="text-sm font-semibold text-slate-200 mb-3">Relationship Types</h2>
              <div className="flex flex-wrap gap-2">
                {Object.entries(relCounts).map(([rel, cnt]) => (
                  <span key={rel} className="bg-slate-800 border border-slate-700 rounded-full px-3 py-1 text-xs text-slate-300">
                    {rel} <span className="text-slate-500 ml-1">{cnt}</span>
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* Tree */}
          <div className="bg-slate-900 border border-slate-800 rounded-xl p-5">
            <h2 className="text-sm font-semibold text-slate-200 mb-4">Lineage Graph</h2>
            <LotTree lineage={lineage} />
          </div>

          {/* Edge table */}
          {lineage.edges.length > 0 && (
            <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
              <div className="px-5 py-3 border-b border-slate-800">
                <h2 className="text-sm font-semibold text-slate-200">Edges ({lineage.edges.length})</h2>
              </div>
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-slate-500 uppercase border-b border-slate-800">
                    {["Parent", "Child", "Relation", "Timestamp"].map(h => (
                      <th key={h} className="text-left px-5 py-2.5 font-medium">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {lineage.edges.map((e, i) => (
                    <tr key={i} className="border-b border-slate-800/50 hover:bg-slate-800/30">
                      <td className="px-5 py-2.5 font-mono text-slate-300">{e.parent_lot_id}</td>
                      <td className="px-5 py-2.5 font-mono text-slate-300">{e.child_lot_id}</td>
                      <td className="px-5 py-2.5">
                        <span className="bg-slate-800 border border-slate-700 px-2 py-0.5 rounded text-slate-400">
                          {e.relation_type}
                        </span>
                      </td>
                      <td className="px-5 py-2.5 text-slate-500">{e.timestamp.slice(0, 19).replace("T", " ")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}

      {!lineage && !loading && !error && (
        <div className="bg-slate-900 border border-slate-800 rounded-xl px-5 py-10 text-center text-slate-500">
          Enter a lot ID to explore its genealogy.
        </div>
      )}
    </div>
  );
}
