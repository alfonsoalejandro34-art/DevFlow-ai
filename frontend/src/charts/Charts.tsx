import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

export interface Series {
  key: string;
  label: string;
  color: string; // a CSS variable reference, e.g. "var(--series-1)"
}

type Datum = Record<string, string | number | null>;
type Formatter = (value: number) => string;

const AXIS = { stroke: "var(--axis)", tick: { fill: "var(--text-muted)", fontSize: 11 }, tickLine: false };

interface TooltipProps {
  active?: boolean;
  label?: string | number;
  payload?: { dataKey?: string | number; value?: number | string | null }[];
}

function ChartTooltip({ active, label, payload, series, format, labelFormat }: TooltipProps & {
  series: Series[];
  format: Formatter;
  labelFormat?: (label: string | number) => string;
}) {
  if (!active || !payload?.length) return null;
  return (
    <div className="chart-tooltip">
      <div className="chart-tooltip-title">{labelFormat && label !== undefined ? labelFormat(label) : label}</div>
      {series.map((s) => {
        const entry = payload.find((p) => p.dataKey === s.key);
        if (!entry || entry.value === null || entry.value === undefined) return null;
        return (
          <div className="chart-tooltip-row" key={s.key}>
            <i style={{ background: s.color }} />
            {s.label}
            <b>{format(Number(entry.value))}</b>
          </div>
        );
      })}
    </div>
  );
}

/** A legend is shown only for two or more series; a single series is named by the card title. */
export function Legend({ series }: { series: Series[] }) {
  if (series.length < 2) return null;
  return (
    <div className="chart-legend" aria-hidden="true">
      {series.map((s) => (
        <span key={s.key}>
          <i style={{ background: s.color }} />
          {s.label}
        </span>
      ))}
    </div>
  );
}

const shortDate = (value: string | number) =>
  new Date(`${value}T00:00:00`).toLocaleDateString(undefined, { month: "short", day: "numeric" });

export function TrendLine({ data, series, format, height = 220, domain, xKey = "date" }: {
  data: Datum[];
  series: Series[];
  format: Formatter;
  height?: number;
  domain?: [number, number];
  xKey?: string;
}) {
  const isDate = xKey === "date";
  return (
    <div className="stack" style={{ gap: 8 }}>
      <Legend series={series} />
      <ResponsiveContainer width="100%" height={height}>
        <LineChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
          <CartesianGrid vertical={false} stroke="var(--grid)" />
          <XAxis dataKey={xKey} {...AXIS} tickFormatter={isDate ? shortDate : (v) => `#${v}`} minTickGap={24} />
          <YAxis {...AXIS} axisLine={false} width={52} tickFormatter={format} domain={domain ?? [0, "auto"]} />
          <Tooltip
            cursor={{ stroke: "var(--axis)", strokeWidth: 1 }}
            content={<ChartTooltip series={series} format={format} labelFormat={isDate ? shortDate : (v) => `Run #${v}`} />}
          />
          {series.map((s) => (
            <Line key={s.key} type="monotone" dataKey={s.key} stroke={s.color} strokeWidth={2}
              dot={data.length <= 12 ? { r: 4, strokeWidth: 2, stroke: "var(--surface)", fill: s.color } : false}
              activeDot={{ r: 5, strokeWidth: 2, stroke: "var(--surface)" }} connectNulls isAnimationActive={false} />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

export function StackedBars({ data, series, format, height = 220, xKey = "date" }: {
  data: Datum[];
  series: Series[];
  format: Formatter;
  height?: number;
  xKey?: string;
}) {
  const isDate = xKey === "date";
  const last = series.length - 1;
  return (
    <div className="stack" style={{ gap: 8 }}>
      <Legend series={series} />
      <ResponsiveContainer width="100%" height={height}>
        <BarChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: 0 }} barCategoryGap="28%">
          <CartesianGrid vertical={false} stroke="var(--grid)" />
          <XAxis dataKey={xKey} {...AXIS} tickFormatter={isDate ? shortDate : (v) => `#${v}`} minTickGap={16} />
          <YAxis {...AXIS} axisLine={false} width={52} tickFormatter={format} allowDecimals={false} />
          <Tooltip
            cursor={{ fill: "var(--surface-2)" }}
            content={<ChartTooltip series={series} format={format} labelFormat={isDate ? shortDate : (v) => `Run #${v}`} />}
          />
          {series.map((s, i) => (
            <Bar key={s.key} dataKey={s.key} stackId="a" fill={s.color} stroke="var(--surface)" strokeWidth={1}
              radius={i === last ? [4, 4, 0, 0] : 0} maxBarSize={36} isAnimationActive={false} />
          ))}
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

export function HorizontalBars({ data, color, format, height }: {
  data: { label: string; value: number }[];
  color: string;
  format: Formatter;
  height?: number;
}) {
  const series = [{ key: "value", label: "Count", color }];
  return (
    <ResponsiveContainer width="100%" height={height ?? Math.max(120, data.length * 34 + 30)}>
      <BarChart data={data} layout="vertical" margin={{ top: 4, right: 24, bottom: 0, left: 8 }} barCategoryGap="30%">
        <CartesianGrid horizontal={false} stroke="var(--grid)" />
        <XAxis type="number" {...AXIS} allowDecimals={false} tickFormatter={format} />
        <YAxis type="category" dataKey="label" {...AXIS} axisLine={false} width={130} />
        <Tooltip cursor={{ fill: "var(--surface-2)" }} content={<ChartTooltip series={series} format={format} />} />
        <Bar dataKey="value" fill={color} radius={[0, 4, 4, 0]} maxBarSize={22} isAnimationActive={false} />
      </BarChart>
    </ResponsiveContainer>
  );
}
