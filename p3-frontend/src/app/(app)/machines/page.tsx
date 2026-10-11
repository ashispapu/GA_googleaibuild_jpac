"use client";

import { PageTitle, QueryError } from "@/components/common";
import { FloorMap } from "@/components/overview";
import { useAppState } from "@/lib/app-state";
import type { HealthView } from "@/lib/contract";
import { useHealthMany, usePlantLayout } from "@/lib/queries";

export default function MachinesPage() {
  const { plantId } = useAppState();
  const layout = usePlantLayout(plantId);
  const flagged = layout.lines.flatMap((l) => l.stations.flatMap((s) => s.machines)).filter((m) => m.band !== "ok");
  const healthQueries = useHealthMany(flagged.map((m) => m.id));
  const details: Record<string, HealthView | undefined> = {};
  healthQueries.forEach((q, i) => {
    if (q.data && flagged[i]) details[flagged[i].id] = q.data;
  });

  if (layout.plants.error) return <QueryError error={layout.plants.error} what="the machines" />;
  return (
    <>
      <PageTitle title="Machines" sub="Pick a machine to see how it's doing and why." />
      <FloorMap plantName={layout.plant?.name ?? plantId} lines={layout.lines} details={details} loading={layout.loading} />
    </>
  );
}
