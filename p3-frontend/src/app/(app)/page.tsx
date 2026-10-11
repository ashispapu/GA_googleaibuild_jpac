"use client";

import { AlertBanner, CalmBanner, FloorMap, KpiTile, LinePanel, OrderTile, ratioText, WatchList } from "@/components/overview";
import { Mono, QueryError, StatusDot } from "@/components/common";
import { useAppState } from "@/lib/app-state";
import { useAlarms, useHealthMany, useOee, useOrders, usePlantLayout } from "@/lib/queries";
import type { HealthView, MachineView } from "@/lib/contract";
import { percent } from "@/lib/format";

export default function OverviewPage() {
  const { plantId, activeIncidentId } = useAppState();
  const layout = usePlantLayout(plantId);
  const lines = layout.lines;
  const machines: MachineView[] = lines.flatMap((l) => l.stations.flatMap((s) => s.machines));

  // GET /plants already gives each machine's health band and risk; ask for details
  // (time left, reasons) only for machines that are not "ok".
  const flagged = machines.filter((m) => m.band !== "ok");
  const healthQueries = useHealthMany(flagged.map((m) => m.id));
  const details: Record<string, HealthView | undefined> = {};
  healthQueries.forEach((q, i) => {
    if (q.data && flagged[i]) details[flagged[i].id] = q.data;
  });

  const orders = useOrders();
  const focus = lines.find((l) => l.currentOrderId) ?? lines[lines.length - 1];
  const order = orders.data?.find((o) => o.id === focus?.currentOrderId) ?? orders.data?.find((o) => o.line_id === focus?.id);
  const oee = useOee(focus?.id ?? null);
  const alarms = useAlarms(plantId);

  const ranked = [...machines].sort((a, b) => b.risk - a.risk);
  const top = ranked[0];
  const topDetail = top ? details[top.id] : undefined;
  const watch = ranked.filter((m) => m.band !== "ok");
  const focusIds = new Set(focus?.stations.flatMap((s) => s.machines.map((m) => m.id)) ?? []);
  const lineAlarms = (alarms.data?.items ?? []).filter((a) => focusIds.has(a.asset_id) && !a.acknowledged).length;

  if (layout.plants.error) return <QueryError error={layout.plants.error} onRetry={() => void layout.plants.refetch()} what="the plant" />;

  return (
    <>
      {top && top.band === "critical" ? (
        <AlertBanner machine={top} risk={top.risk} health={topDetail} incidentId={activeIncidentId} />
      ) : machines.length > 0 && watch.length === 0 ? (
        <CalmBanner />
      ) : null}

      <div className="grid grid-cols-[repeat(auto-fit,minmax(220px,1fr))] gap-4">
        <KpiTile label={`Line ${focus?.id ?? ""} efficiency`} value={ratioText(oee.data?.oee ?? focus?.oee)} sub="this shift" />
        <KpiTile
          label="Highest breakdown risk"
          value={top ? percent(top.risk) : "—"}
          band={top?.band}
          sub={
            top && (
              <span className="flex items-center gap-1.5">
                <StatusDot band={top.band} /> <Mono>{top.id}</Mono> · next 3 days
              </span>
            )
          }
        />
        <KpiTile
          label="Open issues"
          value={activeIncidentId ? 1 : 0}
          href={activeIncidentId ? `/investigation/${activeIncidentId}` : undefined}
          sub={
            activeIncidentId ? (
              <span className="text-brand">
                <Mono>{activeIncidentId}</Mono> · AI team working on it
              </span>
            ) : (
              "nothing needs attention"
            )
          }
        />
        {order && <OrderTile id={order.id} produced={order.completed} quantity={order.quantity} dueAt={order.due_at} />}
      </div>

      <div className="flex flex-wrap items-start gap-4">
        <FloorMap
          plantName={layout.plant?.name ?? plantId}
          lines={lines}
          details={details}
          orderLineId={order?.line_id}
          orderId={order?.id}
          loading={layout.loading}
        />
        <aside className="flex min-w-0 flex-[1_1_280px] flex-col gap-4">
          {focus && (
            <LinePanel
              lineId={focus.id}
              running={focus.state === "run"}
              speedPct={focus.speedPct}
              efficiency={ratioText(oee.data?.oee ?? focus.oee)}
              alarms={lineAlarms}
              aiWarnings={watch.filter((m) => m.band === "critical" && focusIds.has(m.id)).length}
            />
          )}
          <WatchList items={watch.map((m) => ({ machine: m, detail: details[m.id] }))} />
        </aside>
      </div>
    </>
  );
}
