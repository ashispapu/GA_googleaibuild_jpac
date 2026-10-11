"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { ArrowRight } from "lucide-react";
import { Mono, QueryError, Skeleton } from "@/components/common";
import { RiskCard, SignalTile, VibrationChart, WhyList } from "@/components/machine";
import { Badge } from "@/components/ui/badge";
import { buttonVariants } from "@/components/ui/button";
import { Card, CardTitle } from "@/components/ui/card";
import { useAppState } from "@/lib/app-state";
import { SIGNALS, seriesPoints } from "@/lib/contract";
import { BAND_LABEL } from "@/lib/labels";
import { useAccuracy, useHealth, useIncident, usePlantLayout, useSignals } from "@/lib/queries";
import { useTelemetry } from "@/lib/stream";
import { cn } from "@/lib/utils";

const MINUTES = 48 * 60;
const WANTED = [SIGNALS.vibration, SIGNALS.bearingTemp, SIGNALS.knocks, SIGNALS.power, SIGNALS.rpm];

export default function MachinePage() {
  const { assetId } = useParams<{ assetId: string }>();
  const { plantId, currentIncidentId } = useAppState();
  const layout = usePlantLayout(plantId);
  const where = layout.find(assetId);
  const health = useHealth(assetId);
  const incident = useIncident(currentIncidentId);
  const incidentHere = incident.data?.asset_id === assetId ? incident.data : null;
  const accuracy = useAccuracy();
  const signals = useSignals(assetId, WANTED, MINUTES);
  const live = useTelemetry(where?.line.id ?? null, assetId);

  const series = signals.data ?? [];
  const history = seriesPoints(series, SIGNALS.vibration);
  // history + live points (C2 telemetry), without duplicates
  const lastHist = history[history.length - 1]?.t ?? 0;
  const points = [...history, ...(live.series[SIGNALS.vibration] ?? []).filter((p) => p.t > lastHist)];

  if (health.error && !health.data) return <QueryError error={health.error} onRetry={() => void health.refetch()} what="this machine" />;
  const h = health.data;
  const alertAt = incidentHere ? new Date(incidentHere.created_at).getTime() : h && h.band === "critical" ? new Date(h.scoredAt).getTime() : null;

  return (
    <>
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div className="flex flex-col gap-2">
          <nav aria-label="Breadcrumb" className="flex flex-wrap gap-1.5 text-[13px] text-ink-5">
            <Link href="/" className="text-ink-4 no-underline">
              {layout.plant?.name ?? plantId}
            </Link>
            {where && (
              <>
                <span aria-hidden>›</span>
                <span>Line {where.line.id}</span>
                <span aria-hidden>›</span>
                <span>{where.station.name}</span>
              </>
            )}
            <span aria-hidden>›</span>
            <span className="text-ink">{assetId}</span>
          </nav>
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="m-0 text-[26px] font-semibold tracking-tight">
              {where?.machine.name ?? "Machine"} <Mono className="text-[22px] text-ink-4">{assetId}</Mono>
            </h1>
            {h && <Badge tone={h.band === "critical" ? "crit" : h.band === "warning" ? "warn" : "ok"}>{BAND_LABEL[h.band]}</Badge>}
          </div>
          {where && (
            <div className="text-sm text-ink-4">
              {where.line.name} · making {where.line.product}
            </div>
          )}
        </div>
        <div className="flex flex-wrap gap-2.5">
          <Link href="/" className={cn(buttonVariants({ variant: "outline" }), "no-underline")}>
            Back to overview
          </Link>
          {incidentHere && (
            <Link href={`/investigation/${incidentHere.id}`} className={cn(buttonVariants(), "text-white no-underline hover:text-white")}>
              See what the AI found <ArrowRight />
            </Link>
          )}
        </div>
      </div>

      <div className="flex flex-wrap items-start gap-4">
        <div className="flex min-w-0 flex-[3_1_640px] flex-col gap-4">
          <VibrationChart points={points} health={h} alertAt={alertAt} leadGainHours={accuracy.data?.leadTimeGainHours ?? null} loading={signals.isLoading} />
          <div className="grid grid-cols-[repeat(auto-fit,minmax(170px,1fr))] gap-3">
            <SignalTile title="Bearing temperature" points={seriesPoints(series, SIGNALS.bearingTemp)} mode="delta" unit=" °C" />
            <SignalTile title="Sharp knocks in vibration" points={seriesPoints(series, SIGNALS.knocks)} mode="ratio" />
            <SignalTile title="Power use" points={seriesPoints(series, SIGNALS.power)} mode="ratio" />
            <SignalTile title="Spindle speed (rpm)" points={seriesPoints(series, SIGNALS.rpm)} mode="level" />
          </div>
        </div>
        <aside className="flex min-w-0 flex-[1_1_320px] flex-col gap-4">
          {h ? (
            <>
              <RiskCard health={h} />
              <WhyList health={h} />
            </>
          ) : (
            <Skeleton className="h-64" />
          )}
          {where && (
            <Card className="flex flex-col gap-2.5 text-sm">
              <CardTitle>About this machine</CardTitle>
              <div className="flex justify-between gap-2">
                <span className="text-ink-4">Where</span>
                <span>
                  Line {where.line.id} · {where.station.name}
                </span>
              </div>
              <div className="flex justify-between gap-2">
                <span className="text-ink-4">Type</span>
                <span className="capitalize">{where.machine.type.replace(/_/g, " ")}</span>
              </div>
              <div className="flex justify-between gap-2">
                <span className="text-ink-4">Right now</span>
                <span className="capitalize">{where.machine.state === "run" ? "running" : where.machine.state}</span>
              </div>
            </Card>
          )}
        </aside>
      </div>
    </>
  );
}
