"use client";

import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceArea,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { HealthView, SignalPoint } from "@/lib/contract";
import { formatDayTime, formatTime, formatWeekdayTime, hoursText, percent } from "@/lib/format";
import { RULE_LIMIT_MM_S } from "@/lib/labels";
import { cn } from "@/lib/utils";
import { Card, CardHeader, CardTitle } from "./ui/card";
import { Badge } from "./ui/badge";
import { EmptyState, StatusDot } from "./common";

const C = {
  measured: "#1D5FD1",
  grid: "#E8EDF3",
  axis: "#66727F",
  crit: "#D93025",
  critText: "#B3261E",
  amber: "#B7791F",
  amberText: "#8A5300",
};

/**
 * FE-3: live vibration chart with the risk band and the rule-vs-ML markers.
 * Shows "AI warned N h earlier" when the backend gives a rule forecast.
 */
export function VibrationChart({
  points,
  health,
  alertAt,
  leadGainHours,
  loading,
}: {
  points: SignalPoint[];
  health: HealthView | undefined;
  /** when the AI warning was raised (incident created), ms */
  alertAt: number | null;
  /** C5 metric lead_time_gain_hours: how much earlier the model warns than the 4.5 mm/s rule */
  leadGainHours: number | null;
  loading?: boolean;
}) {
  const now = Date.now();
  const latest = points[points.length - 1]?.value ?? null;
  const baseline = points.length ? median(points.slice(0, Math.max(1, Math.floor(points.length / 4))).map((p) => p.value)) : null;
  const critical = health?.band === "critical";
  // Where the old rule would have fired: the AI warning + the model's tested lead-time gain (an estimate).
  const ruleAt = alertAt && leadGainHours && !health?.ruleBreached ? alertAt + leadGainHours * 3_600_000 : null;
  const earlierBy = leadGainHours ? Math.round(leadGainHours) : null;
  const start = points[0]?.t ?? now - 48 * 3_600_000;
  const end = Math.max(now, ruleAt ?? 0) + 2 * 3_600_000;
  const yMax = Math.max(6, Math.ceil(Math.max(...points.map((p) => p.value), RULE_LIMIT_MM_S) * 1.2));

  return (
    <Card className="flex flex-col gap-3.5">
      <CardHeader className="items-start">
        <div className="flex flex-col gap-1">
          <CardTitle>How much the machine is shaking</CardTitle>
          <div className="flex flex-wrap items-baseline gap-2.5">
            <span className="font-mono text-[28px] font-medium">{latest === null ? "—" : `${latest.toFixed(1)} mm/s`}</span>
            {baseline !== null && <span className="text-sm text-ink-4">now · normal is about {baseline.toFixed(1)}</span>}
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {earlierBy !== null && earlierBy > 0 && alertAt && <Badge tone="brand">AI warned ~{earlierBy} h earlier</Badge>}
          <Badge tone={health?.ruleBreached ? "crit" : "neutral"}>
            <StatusDot band={health?.ruleBreached ? "critical" : "ok"} /> Old alarm: {health?.ruleBreached ? "triggered" : "not triggered"}
          </Badge>
        </div>
      </CardHeader>

      {loading && points.length === 0 ? (
        <div className="h-[300px] animate-pulse rounded-md bg-muted" />
      ) : points.length === 0 ? (
        <EmptyState title="No vibration data for this machine yet." />
      ) : (
        <div className="h-[300px] w-full" role="img" aria-label={`Vibration over the last two days. Now ${latest?.toFixed(1)} mm/s; old alarm limit ${RULE_LIMIT_MM_S} mm/s.`}>
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={points} margin={{ top: 28, right: 16, bottom: 4, left: 0 }}>
              <CartesianGrid stroke={C.grid} vertical={false} />
              <XAxis
                dataKey="t"
                type="number"
                scale="time"
                domain={[start, end]}
                tickFormatter={(t: number) => formatWeekdayTime(t)}
                tick={{ fill: C.axis, fontSize: 11 }}
                stroke="#C9D2DD"
                minTickGap={40}
              />
              <YAxis
                domain={[0, yMax]}
                tick={{ fill: C.axis, fontSize: 11, fontFamily: "IBM Plex Mono" }}
                stroke="#C9D2DD"
                width={40}
                unit=""
              />
              <Tooltip
                labelFormatter={(t) => formatDayTime(Number(t))}
                formatter={(v) => [`${Number(v).toFixed(2)} mm/s`, "Shaking"]}
                contentStyle={{ borderRadius: 8, border: "1px solid #E1E6ED", fontSize: 13 }}
              />
              {critical && alertAt && <ReferenceArea x1={alertAt} x2={end} fill={C.crit} fillOpacity={0.06} ifOverflow="extendDomain" />}
              <ReferenceLine
                y={RULE_LIMIT_MM_S}
                stroke={C.amber}
                strokeDasharray="6 5"
                label={{ value: `Old alarm limit · ${RULE_LIMIT_MM_S} mm/s for 30 min`, position: "insideTopLeft", fill: C.amberText, fontSize: 12 }}
              />
              {alertAt && (
                <ReferenceLine
                  x={alertAt}
                  stroke={C.crit}
                  strokeWidth={2}
                  label={{ value: `AI warning · ${formatTime(alertAt)}`, position: "top", fill: C.critText, fontSize: 12, fontWeight: 600 }}
                />
              )}
              {ruleAt && (
                <ReferenceLine
                  x={ruleAt}
                  stroke={C.amber}
                  strokeDasharray="4 4"
                  label={{ value: "Old alarm would go off (estimate)", position: "insideTopRight", fill: C.amberText, fontSize: 12, offset: 8 }}
                />
              )}
              <Line type="monotone" dataKey="value" stroke={C.measured} strokeWidth={2.4} dot={false} isAnimationActive={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}

      <div className="flex flex-wrap gap-4 text-[13px] text-ink-4">
        <span className="flex items-center gap-1.5"><span className="h-[2.5px] w-[18px] bg-brand" />Measured</span>
        <span className="flex items-center gap-1.5"><span className="w-[18px] border-t-2 border-dashed border-[#B7791F]" />Old alarm limit</span>
        {critical && <span className="flex items-center gap-1.5"><span className="h-2.5 w-3.5 rounded-sm bg-crit/15" />High-risk zone (70% or more)</span>}
      </div>
    </Card>
  );
}

function median(xs: number[]) {
  const s = [...xs].sort((a, b) => a - b);
  return s.length ? s[Math.floor(s.length / 2)]! : 0;
}

/** Small tile with a sparkline, e.g. "Bearing temperature 57 °C · 9 °C warmer". */
export function SignalTile({
  title,
  points,
  mode,
  unit = "",
}: {
  title: string;
  points: SignalPoint[];
  /** delta = show change vs normal; ratio = show "1.7× normal"; level = show value only */
  mode: "delta" | "ratio" | "level";
  unit?: string;
}) {
  if (points.length < 2) return null;
  const values = points.map((p) => p.value);
  const latest = values[values.length - 1]!;
  const base = median(values.slice(0, Math.max(1, Math.floor(values.length / 4))));
  const ratio = base ? latest / base : 1;
  const rising = mode === "delta" ? latest - base > 2 : ratio > 1.3;
  let main = `${latest.toFixed(mode === "level" ? 0 : 1)}${unit}`;
  let sub = "normal";
  if (mode === "delta") sub = `${latest - base >= 0 ? "+" : ""}${(latest - base).toFixed(0)}${unit} vs normal`;
  if (mode === "ratio") {
    main = `${ratio.toFixed(1)}×`;
    sub = "normal";
  }
  if (mode === "level") sub = "";

  // Keep tiny wobbles flat: never stretch less than 5% of the level over the full height.
  const min = Math.min(...values);
  const max = Math.max(...values);
  const floor = Math.abs(base) * 0.05;
  const span = Math.max(max - min, floor) || 1;
  const offset = (span - (max - min)) / 2;
  const path = values
    .map((v, i) => `${((i / (values.length - 1)) * 200).toFixed(1)},${(40 - ((v - min + offset) / span) * 32).toFixed(1)}`)
    .join(" ");

  return (
    <div className="flex flex-col gap-1.5 rounded-lg border border-line bg-surface p-3.5">
      <div className="text-[13px] text-ink-4">{title}</div>
      <div className="flex flex-wrap items-baseline gap-2">
        <span className="font-mono text-xl">{main}</span>
        {sub && <span className={cn("text-[13px]", rising ? (mode === "delta" ? "text-warn-text" : "text-crit-text") : "text-ink-4")}>{sub}</span>}
      </div>
      <svg viewBox="0 0 200 44" aria-hidden className="block h-9 w-full">
        <polyline points={path} fill="none" stroke={rising ? (mode === "delta" ? "#B7791F" : "#D93025") : "#8795A6"} strokeWidth="2" />
      </svg>
    </div>
  );
}

export function RiskCard({ health }: { health: HealthView }) {
  const critical = health.band === "critical";
  const dash = 351.9 * health.risk;
  const ring = critical ? "#D93025" : health.band === "warning" ? "#E0A100" : "#1F9D55";
  const track = critical ? "#F6D5D1" : health.band === "warning" ? "#F6E3B4" : "#CDEBD9";
  return (
    <Card className={cn("flex flex-col gap-3.5", critical && "border-2 border-crit p-[17px]")}>
      <CardTitle>Chance of breakdown</CardTitle>
      <div className="flex flex-wrap items-center gap-[18px]">
        <svg width="130" height="130" viewBox="0 0 140 140" role="img" aria-label={`${percent(health.risk)} chance of breakdown in the next 3 days`} className="shrink-0">
          <circle cx="70" cy="70" r="56" fill="none" stroke={track} strokeWidth="12" />
          <circle cx="70" cy="70" r="56" fill="none" stroke={ring} strokeWidth="12" strokeLinecap="round" strokeDasharray={`${dash} 351.9`} transform="rotate(-90 70 70)" />
          <text x="70" y="75" textAnchor="middle" fill={critical ? "#B3261E" : "#16202C"} fontSize="30" fontFamily="IBM Plex Mono, monospace" fontWeight="500">
            {percent(health.risk)}
          </text>
          <text x="70" y="95" textAnchor="middle" fill="#4B5767" fontSize="11">
            next 3 days
          </text>
        </svg>
        <div className="flex flex-[1_1_120px] flex-col gap-3">
          <div className="flex flex-col gap-0.5">
            <span className="text-[13px] text-ink-4">Likely time left</span>
            <span className="font-mono text-2xl">{hoursText(health.hoursLeft)}</span>
          </div>
          <div className="flex flex-col gap-0.5">
            <span className="text-[13px] text-ink-4">Most likely problem</span>
            <span className="text-base font-semibold">{health.problem}</span>
          </div>
        </div>
      </div>
    </Card>
  );
}

const STRENGTH_TONE: Record<string, { bar: string; text: string }> = {
  Strong: { bar: "bg-crit", text: "text-crit-text" },
  Medium: { bar: "bg-warn", text: "text-warn-text" },
  Small: { bar: "bg-[#8795A6]", text: "text-ink-4" },
};

/** FE-3 "SHAP bars", in plain words: the reasons, strongest first. */
export function WhyList({ health }: { health: HealthView }) {
  return (
    <Card className="flex flex-col gap-3.5">
      <CardTitle>Why the AI is {health.band === "ok" ? "calm" : "worried"}</CardTitle>
      {health.reasons.length === 0 && <p className="m-0 text-sm text-ink-4">No warning signs right now.</p>}
      {health.reasons.map((r) => (
        <div key={r.text} className="flex flex-col gap-1.5">
          <div className="flex justify-between gap-2 text-sm">
            <span>{r.text}</span>
            <span className={cn("text-xs font-semibold", STRENGTH_TONE[r.strength]!.text)}>{r.strength}</span>
          </div>
          <div className="h-2 rounded bg-line-soft">
            <div className={cn("h-2 rounded", STRENGTH_TONE[r.strength]!.bar)} style={{ width: `${Math.max(6, r.weight * 100)}%` }} />
          </div>
        </div>
      ))}
    </Card>
  );
}
