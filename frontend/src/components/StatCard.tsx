interface Props {
  label: string;
  value: string | number;
  sub?: string;
  accent?: "green" | "amber" | "red" | "blue" | "default";
}

const accents: Record<string, string> = {
  green:   "text-emerald-400",
  amber:   "text-amber-400",
  red:     "text-red-400",
  blue:    "text-sky-400",
  default: "text-slate-100",
};

export default function StatCard({ label, value, sub, accent = "default" }: Props) {
  return (
    <div className="bg-slate-900 border border-slate-800 rounded-xl p-4">
      <p className="text-slate-500 text-xs uppercase tracking-wider mb-1">{label}</p>
      <p className={`text-2xl font-bold ${accents[accent]}`}>{value}</p>
      {sub && <p className="text-slate-500 text-xs mt-1">{sub}</p>}
    </div>
  );
}
