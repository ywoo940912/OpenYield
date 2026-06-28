import { useState } from "react";
import type { DieYield, YieldModel } from "../types";

interface Props {
  dies: DieYield[];
  model: YieldModel;
  cellSize?: number;
}

function yieldColor(y: number, active: boolean): string {
  if (!active) return "#1e293b";
  const r = Math.round(239 + (34  - 239) * y);
  const g = Math.round( 68 + (197 -  68) * y);
  const b = Math.round( 68 + ( 94 -  68) * y);
  return `rgb(${r},${g},${b})`;
}

const modelKey: Record<YieldModel, keyof DieYield> = {
  poisson:  "yield_poisson",
  murphy:   "yield_murphy",
  negbinom: "yield_negbinom",
};

export default function DieHeatmap({ dies, model, cellSize = 18 }: Props) {
  const [tooltip, setTooltip] = useState<{ die: DieYield; x: number; y: number } | null>(null);

  if (!dies.length) return <p className="text-slate-500 text-sm">No die data.</p>;

  const maxRow = Math.max(...dies.map(d => d.row));
  const maxCol = Math.max(...dies.map(d => d.col));
  const byPos  = new Map(dies.map(d => [`${d.row},${d.col}`, d]));

  const W = (maxCol + 1) * (cellSize + 1) + 1;
  const H = (maxRow + 1) * (cellSize + 1) + 1;

  return (
    <div className="relative inline-block">
      <svg
        width={W}
        height={H}
        className="block"
        onMouseLeave={() => setTooltip(null)}
      >
        {Array.from({ length: maxRow + 1 }, (_, r) =>
          Array.from({ length: maxCol + 1 }, (_, c) => {
            const die = byPos.get(`${r},${c}`);
            if (!die) return null;
            const yv = die[modelKey[model]] as number;
            const x  = c * (cellSize + 1) + 1;
            const y  = r * (cellSize + 1) + 1;
            return (
              <rect
                key={`${r}-${c}`}
                x={x} y={y}
                width={cellSize} height={cellSize}
                fill={yieldColor(yv, die.active)}
                rx={2}
                className="cursor-pointer transition-opacity hover:opacity-80"
                onMouseEnter={e => setTooltip({ die, x: e.clientX, y: e.clientY })}
              />
            );
          })
        )}
      </svg>

      {/* Colour scale */}
      <div className="flex items-center gap-2 mt-2">
        <span className="text-slate-500 text-xs">0%</span>
        <div className="flex-1 h-2 rounded" style={{
          background: "linear-gradient(to right, #ef4444, #f59e0b, #22c55e)"
        }} />
        <span className="text-slate-500 text-xs">100%</span>
      </div>

      {/* Tooltip */}
      {tooltip && (
        <div
          className="fixed z-50 pointer-events-none bg-slate-800 border border-slate-700
                     rounded-lg px-3 py-2 text-xs shadow-xl"
          style={{ left: tooltip.x + 12, top: tooltip.y - 10 }}
        >
          <p className="font-medium text-slate-200">
            Die ({tooltip.die.row}, {tooltip.die.col})
          </p>
          <p className="text-slate-400">
            Yield: <span className="text-emerald-400">
              {((tooltip.die[modelKey[model]] as number) * 100).toFixed(1)}%
            </span>
          </p>
          <p className="text-slate-400">Defects: {tooltip.die.defect_count}</p>
          <p className="text-slate-400">
            D₀: {tooltip.die.d0.toFixed(4)} /mm²
          </p>
          {!tooltip.die.active && (
            <p className="text-amber-400">Edge excluded</p>
          )}
        </div>
      )}
    </div>
  );
}
