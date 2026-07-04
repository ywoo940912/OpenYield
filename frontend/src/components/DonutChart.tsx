interface Slice {
  label: string;
  value: number;
  color: string;
}

interface Props {
  slices: Slice[];
  size?: number;
  thickness?: number;
  label?: string;
  sublabel?: string;
}

function polarXY(cx: number, cy: number, r: number, deg: number) {
  const rad = ((deg - 90) * Math.PI) / 180;
  return { x: cx + r * Math.cos(rad), y: cy + r * Math.sin(rad) };
}

function arcPath(cx: number, cy: number, ro: number, ri: number, start: number, end: number) {
  if (end - start >= 360) end = start + 359.999;
  const o1 = polarXY(cx, cy, ro, start);
  const o2 = polarXY(cx, cy, ro, end);
  const i1 = polarXY(cx, cy, ri, end);
  const i2 = polarXY(cx, cy, ri, start);
  const large = end - start > 180 ? 1 : 0;
  return [
    `M ${o1.x} ${o1.y}`,
    `A ${ro} ${ro} 0 ${large} 1 ${o2.x} ${o2.y}`,
    `L ${i1.x} ${i1.y}`,
    `A ${ri} ${ri} 0 ${large} 0 ${i2.x} ${i2.y}`,
    "Z",
  ].join(" ");
}

export default function DonutChart({ slices, size = 120, thickness = 22, label, sublabel }: Props) {
  const cx = size / 2;
  const cy = size / 2;
  const ro = size / 2 - 2;
  const ri = ro - thickness;

  const total = slices.reduce((s, sl) => s + sl.value, 0);
  if (total === 0) return null;

  let cursor = 0;
  const paths = slices.map(sl => {
    const start = cursor;
    const sweep = (sl.value / total) * 360;
    cursor += sweep;
    return { ...sl, start, end: cursor };
  });

  return (
    <div className="flex items-center gap-4">
      <svg width={size} height={size} className="shrink-0">
        {paths.map((sl, i) => (
          <path key={i} d={arcPath(cx, cy, ro, ri, sl.start, sl.end)} fill={sl.color} />
        ))}
        {label && (
          <text x={cx} y={cy - 4} textAnchor="middle" fontSize={13} fontWeight="700" fill="#f1f5f9">
            {label}
          </text>
        )}
        {sublabel && (
          <text x={cx} y={cy + 12} textAnchor="middle" fontSize={9} fill="#64748b">
            {sublabel}
          </text>
        )}
      </svg>

      <div className="flex flex-col gap-1.5">
        {slices.map((sl, i) => (
          <div key={i} className="flex items-center gap-2 text-xs">
            <span className="w-2 h-2 rounded-sm shrink-0" style={{ backgroundColor: sl.color }} />
            <span className="text-slate-400">{sl.label}</span>
            <span className="text-slate-200 font-semibold ml-auto pl-3">{sl.value}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
