"use client";

import Link from "next/link";
import { Check, CircleAlert, Wrench } from "lucide-react";
import type { IncidentOverview, MaintenanceLogEntry, WorkUpdate } from "@/lib/contract";
import { formatDayTime, formatTime } from "@/lib/format";
import { LIFECYCLE, lifecycleIndex, STAGE, failureModeLabel } from "@/lib/labels";
import { cn } from "@/lib/utils";
import { Badge } from "./ui/badge";
import { EmptyState, Mono } from "./common";

const MOVING = new Set(["ai_analysing", "technician_working", "verifying"]);

export function StageBadge({ stage, className }: { stage: string; className?: string }) {
  const s = STAGE[stage] ?? { label: stage, tone: "neutral" as const };
  return (
    <Badge tone={s.tone} className={className}>
      {MOVING.has(stage) && <span className="h-2 w-2 animate-pulse rounded-full bg-current" aria-hidden />}
      {stage === "repair_failed" && <CircleAlert className="h-3.5 w-3.5" aria-hidden />}
      {(stage === "resolved" || stage === "ready_to_close") && <Check className="h-3.5 w-3.5" aria-hidden />}
      {s.label}
    </Badge>
  );
}

export function StatusTag({ tag }: { tag: "in_progress" | "done" }) {
  return tag === "done" ? (
    <span className="rounded-sm bg-ok-soft px-1.5 py-0.5 text-[11px] font-semibold uppercase tracking-wide text-ok-text">Done</span>
  ) : (
    <span className="rounded-sm bg-brand-soft px-1.5 py-0.5 text-[11px] font-semibold uppercase tracking-wide text-brand-text">In progress</span>
  );
}

/** AI analysis → Approval → Assigned → Repair → Machine check → Closed */
export function LifecycleStepper({ o }: { o: IncidentOverview }) {
  const at = lifecycleIndex(o.stage);
  const failed = o.stage === "repair_failed";
  const done = o.stage === "resolved";
  return (
    <ol className="m-0 flex list-none flex-wrap items-center gap-y-2 p-0" aria-label="Where this issue is">
      {LIFECYCLE.map((step, i) => {
        const complete = i < at || done;
        const current = i === at && !done;
        const bad = failed && i === at;
        return (
          <li key={step.key} className="flex items-center">
            <span className="flex items-center gap-2">
              <span
                className={cn(
                  "flex h-6 w-6 items-center justify-center rounded-full border text-[11px] font-semibold",
                  bad ? "border-crit bg-crit text-white" : complete ? "border-ok bg-ok text-white" : current ? "border-brand bg-brand text-white" : "border-line-strong bg-surface text-ink-5",
                )}
              >
                {complete ? <Check className="h-3.5 w-3.5" /> : bad ? "!" : i + 1}
              </span>
              <span className={cn("text-[13px]", current || bad ? "font-semibold text-ink" : complete ? "text-ink-3" : "text-ink-5")}>
                {step.label}
                {current && MOVING.has(o.stage) && <span className="ml-1.5 inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-brand align-middle" />}
              </span>
            </span>
            {i < LIFECYCLE.length - 1 && <span className={cn("mx-2.5 h-px w-6 sm:w-10", i < at || done ? "bg-ok" : "bg-line-strong")} aria-hidden />}
          </li>
        );
      })}
    </ol>
  );
}

/** One line under the stage: who did what (approved by, technician, check result). */
export function StageDetail({ o }: { o: IncidentOverview }) {
  const bits: React.ReactNode[] = [];
  if (o.ai_working) bits.push(o.incident.round > 1 ? `Analysis round ${o.incident.round}` : "Agents are reading the plant data");
  if (o.approval && o.option_id) bits.push(<>Option {o.option_id.replace("OPT-", "")} approved by {o.approval.name.replace(/ \(.*\)$/, "")}</>);
  if (o.technician) bits.push(<>Technician {o.technician.name}</>);
  if (o.stage === "waiting_technician" && o.planned_start) bits.push(<>planned {formatDayTime(o.planned_start)}</>);
  if (o.verification && o.stage !== "resolved") bits.push(o.verification.details ?? `check ${o.verification.result}`);
  if (o.machine_state === "fault") bits.push(<span className="font-semibold text-crit-text">machine broken down</span>);
  if (o.resolution && o.stage === "resolved") bits.push(o.resolution);
  return (
    <span className="text-[13px] text-ink-4">
      {bits.map((b, i) => (
        <span key={i}>
          {i > 0 && " · "}
          {b}
        </span>
      ))}
    </span>
  );
}

export function IncidentRows({ items, hrefFor, empty }: { items: IncidentOverview[]; hrefFor?: (o: IncidentOverview) => string; empty?: string }) {
  if (!items.length) return <EmptyState title={empty ?? "Nothing here."} />;
  return (
    <ul className="m-0 flex list-none flex-col divide-y divide-line-soft p-0">
      {items.map((o) => {
        const inc = o.incident;
        const href = hrefFor ? hrefFor(o) : `/investigation/${inc.id}`;
        return (
          <li key={inc.id}>
            <Link href={href} className="flex flex-wrap items-center gap-x-4 gap-y-1.5 px-1 py-3 text-ink no-underline hover:bg-subtle">
              <span className="flex w-[118px] shrink-0 flex-col">
                <Mono className="text-[15px] font-medium">{inc.id}</Mono>
                <span className="text-xs text-ink-5">{inc.trigger === "alarm" ? "from an alarm" : inc.trigger === "ml_alert" ? "AI early warning" : "question"}</span>
              </span>
              <span className="flex min-w-[180px] flex-[1_1_200px] flex-col">
                <span className="text-sm font-semibold">
                  {o.asset_name} <Mono className="font-normal text-ink-4">{inc.asset_id}</Mono>
                </span>
                <span className="text-xs text-ink-5">
                  {inc.plant_id.replace("PLANT-", "Plant ")} · {o.line_name}
                  {inc.diagnosis && <> · {failureModeLabel(inc.diagnosis.failure_mode)}</>}
                </span>
              </span>
              <span className="flex min-w-[240px] flex-[2_1_300px] flex-col gap-1">
                <span className="flex flex-wrap items-center gap-2">
                  <StatusTag tag={o.status_tag} />
                  <StageBadge stage={o.stage} />
                  {inc.round > 1 && <Badge tone="warn">Round {inc.round}</Badge>}
                  {o.questions > 0 && <span className="text-xs text-ink-5">{o.questions} question{o.questions > 1 ? "s" : ""}</span>}
                </span>
                <StageDetail o={o} />
              </span>
              <span className="ml-auto whitespace-nowrap font-mono text-xs text-ink-5">{formatDayTime(inc.updated_at)}</span>
            </Link>
          </li>
        );
      })}
    </ul>
  );
}

export function LogRows({ items }: { items: MaintenanceLogEntry[] }) {
  if (!items.length) return <EmptyState title="Nothing closed yet.">Closed issues and the fix that worked appear here.</EmptyState>;
  return (
    <ul className="m-0 flex list-none flex-col divide-y divide-line-soft p-0">
      {items.map((e) => (
        <li key={e.incident_id}>
          <Link href={`/investigation/${e.incident_id}`} className="flex flex-wrap gap-x-5 gap-y-2 px-1 py-3.5 text-ink no-underline hover:bg-subtle">
            <span className="flex w-[118px] shrink-0 flex-col">
              <Mono className="text-[15px] font-medium">{e.incident_id}</Mono>
              <span className="text-xs text-ink-5">closed {formatDayTime(e.resolved_at)}</span>
            </span>
            <span className="flex min-w-[180px] flex-[1_1_200px] flex-col">
              <span className="text-sm font-semibold">
                {e.asset_name} <Mono className="font-normal text-ink-4">{e.asset_id}</Mono>
              </span>
              <span className="text-xs text-ink-5">
                {e.plant_id.replace("PLANT-", "Plant ")} · Line {e.line_id.replace("L", "")}
                {e.rounds > 1 && ` · ${e.rounds} analysis rounds`}
              </span>
            </span>
            <span className="flex min-w-[260px] flex-[3_1_380px] flex-col gap-1 text-sm">
              <span>
                <Wrench className="mr-1.5 inline h-3.5 w-3.5 text-ink-4" aria-hidden />
                {e.solution}
              </span>
              <span className="text-[13px] text-ink-4">
                Root cause: {e.actual_root_cause ?? "—"}
                {e.ai_confirmed === true && <span className="text-ok-text"> · AI was right</span>}
                {e.ai_confirmed === false && <span className="text-warn-text"> · AI said: {e.ai_root_cause}</span>}
              </span>
              <span className="text-xs text-ink-5">
                {e.parts.length ? e.parts.map((p) => `${p.quantity}× ${p.sku}`).join(", ") : "no parts"} · {e.technician?.name ?? "—"}
                {e.approved_by && ` · approved by ${e.approved_by.name.replace(/ \(.*\)$/, "")}`} · fixed in {Math.round(e.time_to_fix_hours)} h
                {e.downtime_hours !== null && e.downtime_hours !== undefined && ` (machine down ${e.downtime_hours} h)`}
              </span>
            </span>
          </Link>
        </li>
      ))}
    </ul>
  );
}

const KIND_ICON: Record<string, string> = {
  opened: "bg-brand", options: "bg-brand", approve: "bg-ok", reject: "bg-crit", created: "bg-ink-5", assigned: "bg-brand",
  parts: "bg-ink-5", started: "bg-brand", note: "bg-ink-5", completed: "bg-brand", verification: "bg-warn",
  reanalysis: "bg-crit", closed: "bg-ok", resolved: "bg-ok",
};

export function Timeline({ items, compact = false }: { items: WorkUpdate[]; compact?: boolean }) {
  if (!items.length) return <p className="m-0 text-sm text-ink-4">Nothing recorded yet.</p>;
  return (
    <ol className="m-0 flex list-none flex-col p-0">
      {items.map((u, i) => (
        <li key={i} className="relative flex gap-3 pb-3 last:pb-0">
          <span className="relative flex w-3 shrink-0 justify-center">
            <span className={cn("mt-1.5 h-2.5 w-2.5 rounded-full", KIND_ICON[u.kind] ?? "bg-ink-5")} />
            {i < items.length - 1 && <span className="absolute top-4 h-[calc(100%-8px)] w-px bg-line" aria-hidden />}
          </span>
          <span className="flex min-w-0 flex-1 flex-col">
            <span className="text-sm text-ink">{u.note}</span>
            <span className="text-xs text-ink-5">
              <Mono>{compact ? formatTime(u.ts) : formatDayTime(u.ts)}</Mono>
              {u.author && ` · ${u.author.name}`}
            </span>
          </span>
        </li>
      ))}
    </ol>
  );
}
