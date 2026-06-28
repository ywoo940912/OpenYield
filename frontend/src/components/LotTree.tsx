import type { LotLineage, LotNodeItem, LineageEdge } from "../types";

interface Props {
  lineage: LotLineage;
}

const RELATION_COLORS: Record<string, string> = {
  split:   "#34d399",
  merge:   "#60a5fa",
  rework:  "#f59e0b",
  convert: "#a78bfa",
  inspect: "#94a3b8",
};

function NodeBox({ node, isFocal }: { node: LotNodeItem; isFocal?: boolean }) {
  return (
    <div
      className={`rounded-lg border px-3 py-2 text-xs min-w-[120px] max-w-[160px] ${
        isFocal
          ? "border-emerald-500 bg-emerald-500/10 text-emerald-300"
          : "border-slate-700 bg-slate-800 text-slate-300"
      }`}
    >
      <p className="font-semibold truncate">{node.lot_id}</p>
      <p className="text-slate-500 mt-0.5">{node.substrate_type}</p>
      {node.process_step && (
        <p className="text-slate-500">{node.process_step}</p>
      )}
    </div>
  );
}

function edgeLabel(edges: LineageEdge[], parent: string, child: string): string {
  return edges.find(e => e.parent_lot_id === parent && e.child_lot_id === child)
    ?.relation_type ?? "";
}

export default function LotTree({ lineage }: Props) {
  const focalNode: LotNodeItem = {
    lot_id:         lineage.lot_id,
    substrate_type: "",
    process_step:   "",
    lot_size:       0,
    created_at:     "",
  };

  const RelBadge = ({ rel }: { rel: string }) =>
    rel ? (
      <span
        className="text-[10px] px-1.5 py-0.5 rounded font-medium"
        style={{ color: RELATION_COLORS[rel] ?? "#94a3b8", background: "#1e293b" }}
      >
        {rel}
      </span>
    ) : null;

  return (
    <div className="flex flex-col items-center gap-4 py-4 overflow-x-auto">
      {/* Ancestors */}
      {lineage.ancestors.length > 0 && (
        <>
          <div className="flex flex-wrap justify-center gap-3">
            {lineage.ancestors.map(n => (
              <NodeBox key={n.lot_id} node={n} />
            ))}
          </div>
          <div className="flex flex-wrap justify-center gap-3">
            {lineage.ancestors.map(n => {
              const rel = edgeLabel(lineage.edges, n.lot_id, lineage.lot_id);
              return rel ? (
                <div key={n.lot_id} className="flex flex-col items-center gap-1">
                  <RelBadge rel={rel} />
                  <div className="w-px h-4 bg-slate-700" />
                </div>
              ) : null;
            })}
          </div>
        </>
      )}

      {/* Focal lot */}
      <NodeBox node={focalNode} isFocal />
      <p className="text-slate-600 text-xs">depth {lineage.depth}</p>

      {/* Descendants */}
      {lineage.descendants.length > 0 && (
        <>
          <div className="flex flex-wrap justify-center gap-3">
            {lineage.descendants.map(n => {
              const rel = edgeLabel(lineage.edges, lineage.lot_id, n.lot_id);
              return (
                <div key={n.lot_id} className="flex flex-col items-center gap-1">
                  <div className="w-px h-4 bg-slate-700" />
                  {rel && <RelBadge rel={rel} />}
                </div>
              );
            })}
          </div>
          <div className="flex flex-wrap justify-center gap-3">
            {lineage.descendants.map(n => (
              <NodeBox key={n.lot_id} node={n} />
            ))}
          </div>
        </>
      )}

      {lineage.ancestors.length === 0 && lineage.descendants.length === 0 && (
        <p className="text-slate-500 text-sm">No genealogy edges recorded for this lot.</p>
      )}
    </div>
  );
}
