"use client";

import { IncidentRows } from "@/components/incidents";
import { PageTitle, QueryError, Skeleton } from "@/components/common";
import { Card, CardTitle } from "@/components/ui/card";
import { useIncidents } from "@/lib/queries";
import { useSession } from "@/lib/session";

const REPAIR_STAGES = new Set(["waiting_technician", "technician_working", "verifying", "ready_to_close", "repair_failed"]);

/** Technician: the jobs assigned to me. Everyone else: every job in the repair stages. */
export default function JobsPage() {
  const { me } = useSession();
  const isTech = me?.role === "technician";
  const open = useIncidents({ scope: "open", kind: "incident", mine: isTech || undefined });
  const done = useIncidents({ scope: "done", kind: "incident", mine: isTech || undefined }, 15_000);

  const jobs = (open.data ?? []).filter((o) => REPAIR_STAGES.has(o.stage));
  const next = (open.data ?? []).filter((o) => !REPAIR_STAGES.has(o.stage));
  const order = ["repair_failed", "ready_to_close", "technician_working", "verifying", "waiting_technician"];
  jobs.sort((a, b) => order.indexOf(a.stage) - order.indexOf(b.stage));
  const closed = (done.data ?? []).filter((o) => o.stage === "resolved").slice(0, 10);

  return (
    <>
      <PageTitle
        title={isTech ? "My jobs" : "Repair jobs"}
        sub={
          isTech
            ? "Repairs assigned to you. Open one to see the approved option, the parts, the manual pages, and to update its status."
            : "Every approved repair and where it stands. Only the assigned technician can start, finish and close a job."
        }
      />
      <Card className="flex flex-col gap-2 py-3">
        <CardTitle className="px-1">To do</CardTitle>
        {open.error && !open.data ? (
          <QueryError error={open.error} onRetry={() => void open.refetch()} what="the jobs" />
        ) : !open.data ? (
          <Skeleton className="m-3 h-32" />
        ) : (
          <IncidentRows items={jobs} hrefFor={(o) => `/jobs/${o.incident.id}`} empty={isTech ? "No jobs for you right now." : "No repairs in progress."} />
        )}
      </Card>
      {isTech && next.length > 0 && (
        <Card className="flex flex-col gap-2 py-3">
          <CardTitle className="px-1">Coming up</CardTitle>
          <p className="m-0 px-1 text-[13px] text-ink-4">Assigned to you but still being analysed or waiting for approval.</p>
          <IncidentRows items={next} hrefFor={(o) => `/jobs/${o.incident.id}`} />
        </Card>
      )}
      <Card className="flex flex-col gap-2 py-3">
        <CardTitle className="px-1">Recently closed</CardTitle>
        {done.data ? <IncidentRows items={closed} hrefFor={(o) => `/jobs/${o.incident.id}`} empty="Nothing closed yet." /> : <Skeleton className="m-3 h-20" />}
      </Card>
    </>
  );
}
