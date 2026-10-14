"use client";

import Link from "next/link";
import { motion } from "framer-motion";
import { CircleCheck, TriangleAlert } from "lucide-react";
import type { HealthView, LineView, MachineView } from "@/lib/contract";
import { count, formatDayTime, hoursText, percent } from "@/lib/format";
import { BAND_LABEL, type Band } from "@/lib/labels";
import { cn } from "@/lib/utils";
import { Card, CardHeader, CardTitle } from "./ui/card";
import { EmptyState, Mono, StatusDot } from "./common";
import { buttonVariants } from "./ui/button";

/** OEE may arrive as 0..1 or as a percentage; show it as a percentage either way. */
export const ratioText = (v: number | null | undefined) => (v === null || v === undefined ? "—" : v > 1 ? `${Math.round(v)}%` : percent(v));

export function CalmBanner() {
  return (
    <div role="status" className="flex flex-wrap items-center gap-3 rounded-lg border border-ok-border bg-ok-soft px-[18px] py-3.5">
      <CircleCheck className="h-[22px] w-[22px] text-ok-text" aria-hidden />
      <span className="text-[15px] font-semibold text-ok-text">All machines are running normally.</span>
      <span className="text-sm text-[#2F5E43]">No machine is above 30% breakdown risk.</span>
    </div>
  );
}

export function AlertBanner({ machine, risk, health, incidentId }: { machine: MachineView; risk: number; health?: HealthView; incidentId: string | null }) {
  return (
    <div role="alert" className="flex flex-wrap items-start gap-3.5 rounded-lg border border-crit-border bg-crit-soft px-[18px] py-4">
      <TriangleAlert className="mt-0.5 h-6 w-6 shrink-0 text-crit-text" aria-hidden />
      <div className="flex flex-[1_1_420px] flex-col gap-1">
        <div className="text-base font-semibold text-crit-deep">
          {machine.name} <Mono>{machine.id}</Mono> has a {percent(risk)} chance of breaking down in the next 3 days
        </div>
        <div className="text-sm leading-relaxed text-[#6B2A25]">
          {health?.ruleBreached ? "The normal vibration alarm has gone off too." : health ? "The normal vibration alarm has not gone off yet." : null}
          {incidentId && (
            <>
              {" "}
              Issue <Mono>{incidentId}</Mono> is open and the AI team is looking into it.
            </>
          )}
        </div>
      </div>
      <div className="flex flex-wrap gap-2.5">
        <Link href={`/machines/${machine.id}`} className={cn(buttonVariants({ variant: "danger" }), "no-underline")}>
          See the machine
        </Link>
        {incidentId && (
          <Link href={`/investigation/${incidentId}`} className={cn(buttonVariants(), "text-white no-underline hover:text-white")}>
            See what the AI found
          </Link>
        )}
      </div>
    </div>
  );
}

export function KpiTile({
  label,
  value,
  sub,
  band,
  href,
  children,
}: {
  label: React.ReactNode;
  value: React.ReactNode;
  sub?: React.ReactNode;
  band?: Band;
  href?: string;
  children?: React.ReactNode;
}) {
  const critical = band === "critical";
  const body = (
    <>
      <div className={cn("text-[13px] font-semibold", critical ? "text-crit-deep" : "text-ink-4")}>{label}</div>
      <div className={cn("font-mono text-[34px] font-medium leading-tight", critical && "text-crit-text")}>{value}</div>
      {children}
      {sub && <div className={cn("text-[13px]", critical ? "text-[#6B2A25]" : "text-ink-5")}>{sub}</div>}
    </>
  );
  const cls = cn(
    "flex flex-col gap-1.5 rounded-lg bg-surface px-[18px] py-4 text-ink no-underline",
    critical ? "border-2 border-crit px-[17px] py-[15px]" : "border border-line",
  );
  return href ? (
    <Link href={href} className={cn(cls, "hover:border-brand-border")}>
      {body}
    </Link>
  ) : (
    <div className={cls}>{body}</div>
  );
}

export function OrderTile({ id, produced, quantity, dueAt }: { id: string; produced: number; quantity: number; dueAt: string }) {
  const pct = quantity ? Math.round((produced / quantity) * 100) : 0;
  return (
    <div className="flex flex-col gap-2 rounded-lg border border-line bg-surface px-[18px] py-4">
      <div className="text-[13px] font-semibold text-ink-4">
        Order <Mono>{id}</Mono>
      </div>
      <div className="font-mono text-[28px] font-medium">
        {count(produced)} <span className="text-base text-ink-5">of {count(quantity)}</span>
      </div>
      <div role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100} aria-label="Order progress" className="h-2 rounded bg-[#E8EDF3]">
        <div className="h-2 rounded bg-brand" style={{ width: `${pct}%` }} />
      </div>
      <div className="text-[13px] text-ink-5">due {formatDayTime(dueAt)}</div>
    </div>
  );
}

// ---- Floor map ---------------------------------------------------------------------

export function FloorMap({
  plantName,
  lines,
  details,
  orderLineId,
  orderId,
  loading,
}: {
  plantName: string;
  lines: LineView[];
  details: Record<string, HealthView | undefined>;
  orderLineId?: string;
  orderId?: string;
  loading?: boolean;
}) {
  return (
    <Card className="flex min-w-0 flex-[3_1_640px] flex-col gap-[18px]">
      <CardHeader>
        <CardTitle>{plantName} floor</CardTitle>
        <div className="flex flex-wrap items-center gap-3.5 text-[13px] text-ink-4">
          {(["ok", "warning", "critical"] as Band[]).map((b) => (
            <span key={b} className="flex items-center gap-1.5">
              <StatusDot band={b} /> {BAND_LABEL[b]}
            </span>
          ))}
        </div>
      </CardHeader>
      {loading && lines.length === 0 && <div className="h-40 animate-pulse rounded-md bg-muted" />}
      {!loading && lines.length === 0 && <EmptyState title="No lines to show for this plant." />}
      {lines.map((line) => (
        <div key={line.id} className="flex flex-col gap-2">
          <div className="text-sm text-ink-4">
            <span className="font-semibold text-ink">Line {line.id}</span> · {line.name}
            {orderId && line.id === orderLineId && (
              <>
                {" "}
                · making order <Mono>{orderId}</Mono>
              </>
            )}
          </div>
          <div className="flex flex-wrap items-center gap-1.5">
            {line.stations
              .flatMap((s) => s.machines)
              .map((m, i, all) => (
                <MachineWithConnector key={m.id} machine={m} detail={details[m.id]} last={i === all.length - 1} index={i} />
              ))}
          </div>
        </div>
      ))}
    </Card>
  );
}

function MachineWithConnector({ machine, detail, last, index }: { machine: MachineView; detail?: HealthView; last: boolean; index: number }) {
  return (
    <>
      <MachineTile machine={machine} detail={detail} />
      {!last && (
        <div aria-hidden className="relative hidden h-0.5 min-w-4 flex-[1_1_20px] bg-[#D6DEE7] sm:block">
          <motion.span
            className="absolute -top-[3px] h-2 w-2 rounded-[2px] bg-brand-light"
            initial={{ left: "0%" }}
            animate={{ left: ["0%", "100%"] }}
            transition={{ duration: 5, repeat: Infinity, ease: "linear", delay: (index * 0.9) % 5 }}
          />
        </div>
      )}
    </>
  );
}

export function MachineTile({ machine, detail }: { machine: MachineView; detail?: HealthView }) {
  const band = machine.band;
  const critical = band === "critical";
  return (
    <Link
      href={`/machines/${machine.id}`}
      className={cn(
        "flex min-w-[120px] flex-[0_1_150px] flex-col gap-1 rounded-[10px] bg-surface px-3 py-2.5 text-ink no-underline hover:text-ink",
        critical ? "min-w-[140px] flex-[0_1_170px] border-2 border-crit bg-crit-soft px-[11px] py-[9px] animate-pulse-ring" : band === "warning" ? "border border-warn-border bg-warn-soft" : "border border-[#D6DEE7] hover:border-brand-border",
      )}
    >
      <div className="flex items-center justify-between">
        <Mono className="text-[13px] font-semibold">{machine.id}</Mono>
        <StatusDot band={band} label={BAND_LABEL[band]} />
      </div>
      <div className={cn("text-[13px]", critical ? "text-[#6B2A25]" : "text-ink-4")}>{machine.name}</div>
      {band !== "ok" && (
        <div className={cn("text-[13px] font-semibold", critical ? "text-crit-text" : "text-warn-text")}>
          {percent(machine.risk)} risk{detail?.hoursLeft ? ` · ${hoursText(detail.hoursLeft)} left` : ""}
        </div>
      )}
    </Link>
  );
}

export function LinePanel({
  lineId,
  running,
  speedPct,
  efficiency,
  alarms,
  aiWarnings,
}: {
  lineId: string;
  running: boolean | null;
  speedPct: number | null;
  efficiency: string;
  alarms: number;
  aiWarnings: number;
}) {
  const row = "flex justify-between text-sm";
  return (
    <Card className="flex flex-col gap-3">
      <CardTitle>Line {lineId} right now</CardTitle>
      <div className={row}>
        <span className="text-ink-4">Status</span>
        <span className="flex items-center gap-1.5 font-medium">
          {running === null ? (
            "—"
          ) : (
            <>
              <StatusDot band={running ? "ok" : "critical"} /> {running ? "Running" : "Stopped"}
            </>
          )}
        </span>
      </div>
      <div className={row}>
        <span className="text-ink-4">Speed</span>
        <Mono>{speedPct === null ? "—" : `${Math.round(speedPct)}%`}</Mono>
      </div>
      <div className={row}>
        <span className="text-ink-4">Efficiency</span>
        <Mono>{efficiency}</Mono>
      </div>
      <div className={row}>
        <span className="text-ink-4">Machine alarms</span>
        <Mono>{alarms}</Mono>
      </div>
      <div className={row}>
        <span className="text-ink-4">AI warnings</span>
        <Mono className={aiWarnings ? "font-semibold text-crit-text" : undefined}>{aiWarnings}</Mono>
      </div>
    </Card>
  );
}

export function WatchList({ items }: { items: { machine: MachineView; detail?: HealthView }[] }) {
  return (
    <Card className="flex flex-col gap-2.5">
      <CardTitle>Machines to watch</CardTitle>
      {items.length === 0 ? (
        <EmptyState title="Nothing to watch right now." />
      ) : (
        items.map(({ machine, detail }) => (
          <Link
            key={machine.id}
            href={`/machines/${machine.id}`}
            className={cn(
              "flex flex-col gap-1 rounded-[10px] border p-3 text-ink no-underline hover:text-ink",
              machine.band === "critical" ? "border-crit-border bg-crit-soft" : "border-warn-border bg-warn-soft",
            )}
          >
            <span className="flex justify-between gap-2">
              <Mono className="font-semibold">{machine.id}</Mono>
              <Mono className={cn("font-semibold", machine.band === "critical" ? "text-crit-text" : "text-warn-text")}>{percent(machine.risk)}</Mono>
            </span>
            <span className="text-[13px] text-ink-3">
              {machine.name}
              {detail && ` · ${detail.reasons[0]?.text ?? detail.problem}`}
            </span>
          </Link>
        ))
      )}
    </Card>
  );
}
