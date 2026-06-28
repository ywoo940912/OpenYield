import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer,
  Cell, ReferenceLine,
} from "recharts";

interface Entry {
  name: string;
  value: number;
}

interface Props {
  data: Entry[];
  referenceValue?: number;
  referenceLabel?: string;
  color?: string;
}

export default function YieldChart({
  data,
  referenceValue,
  referenceLabel = "Global",
  color = "#34d399",
}: Props) {
  return (
    <ResponsiveContainer width="100%" height={200}>
      <BarChart data={data} margin={{ top: 8, right: 8, left: -10, bottom: 0 }}>
        <XAxis
          dataKey="name"
          tick={{ fill: "#94a3b8", fontSize: 11 }}
          axisLine={false}
          tickLine={false}
        />
        <YAxis
          tickFormatter={v => `${(v * 100).toFixed(0)}%`}
          domain={[0, 1]}
          tick={{ fill: "#94a3b8", fontSize: 11 }}
          axisLine={false}
          tickLine={false}
        />
        <Tooltip
          formatter={(v: number) => [`${(v * 100).toFixed(2)}%`]}
          contentStyle={{
            background: "#1e293b",
            border: "1px solid #334155",
            borderRadius: 8,
            fontSize: 12,
          }}
          labelStyle={{ color: "#cbd5e1" }}
        />
        {referenceValue !== undefined && (
          <ReferenceLine
            y={referenceValue}
            stroke="#f59e0b"
            strokeDasharray="4 3"
            label={{
              value: referenceLabel,
              fill: "#f59e0b",
              fontSize: 10,
              position: "insideTopRight",
            }}
          />
        )}
        <Bar dataKey="value" radius={[4, 4, 0, 0]}>
          {data.map((_, i) => (
            <Cell key={i} fill={color} fillOpacity={0.85} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}
