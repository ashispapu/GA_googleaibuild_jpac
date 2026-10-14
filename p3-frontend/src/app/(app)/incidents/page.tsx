"use client";

import { useState } from "react";
import { IncidentRows, LogRows } from "@/components/incidents";
import { SignoffInbox } from "@/components/signoff";
import { PageTitle, QueryError, Skeleton } from "@/components/common";
import { Card } from "@/components/ui/card";
import { useAppState } from "@/lib/app-state";
import { useIncidents, useMaintenanceLog } from "@/lib/queries";
import { useSession } from "@/lib/session";
import { cn } from "@/lib/utils";

type Tab = "open" | "done";

export default function IncidentsPage() {
  const { me } = useSession();
  const { plantId } = useAppState();
  const [tab, setTab] = useState<Tab>("open");
  const [scopePlant, setScopePlant] = useState<"this" | "all">("all");
  const [mine, setMine] = useState(false);
  const isTech = me?.role === "technician";
  const plantFilter = scopePlant === "this" ? plantId : undefined;
  const open = useIncidents({ scope: "open", kind: "incident", plantId: plantFilter, mine: mine || undefined });
  const done = useIncidents({ scope: "done", kind: "incident", plantId: plantFilter, mine: mine || undefined });
  const log = useMaintenanceLog();

  const logItems = (log.data ?? []).filter((e) => !plantFilter || e.plant_id === plantFilter);
  const rejected = (done.data ?? []).filter((o) => o.stage === "rejected");

  return (
    <>
      <PageTitle
        title="Issues"
        sub="Every machine issue the AI found, where it is now, and how it was fixed."
        right={
          <div className="flex flex-wrap items-center gap-2 text-[13px]">
            <Toggle on={scopePlant === "all"} onClick={() => setScopePlant("all")}>All my plants</Toggle>
            <Toggle on={scopePlant === "this"} onClick={() => setScopePlant("this")}>{plantId.replace("PLANT-", "Plant ")} only</Toggle>
            {isTech && <Toggle on={mine} onClick={() => setMine((m) => !m)}>Only my jobs</Toggle>}
          </div>
        }
      />
      <SignoffInbox />
      <div role="tablist" aria-label="Issues" className="flex gap-1 border-b border-line">
        {([
          ["open", "In progress", open.data?.length],
          ["done", "Done", (log.data ? logItems.length : done.data?.length) ?? undefined],
        ] as const).map(([key, label, n]) => (
          <button
            key={key}
            role="tab"
            type="button"
            aria-selected={tab === key}
            onClick={() => setTab(key)}
            className={cn(
              "-mb-px flex items-center gap-2 border-b-2 px-3 py-2.5 text-sm",
              tab === key ? "border-brand font-semibold text-ink" : "border-transparent text-ink-4 hover:text-ink",
            )}
          >
            {label}
            {n !== undefined && <span className="rounded-full bg-muted px-2 py-px font-mono text-xs">{n}</span>}
          </button>
        ))}
      </div>
      <Card className="py-2">
        {tab === "open" ? (
          open.error && !open.data ? (
            <QueryError error={open.error} onRetry={() => void open.refetch()} what="the issues" />
          ) : !open.data ? (
            <Skeleton className="m-3 h-40" />
          ) : (
            <IncidentRows items={open.data} empty={mine ? "No open jobs for you right now." : "No open issues right now."} />
          )
        ) : log.data ? (
          <>
            <LogRows items={logItems.filter((e) => !mine || (!!e.technician && !!me?.name.startsWith(e.technician.name)))} />
            {rejected.length > 0 && (
              <div className="border-t border-line px-1 pt-3">
                <div className="pb-1 text-[13px] font-semibold text-ink-4">Rejected</div>
                <IncidentRows items={rejected} />
              </div>
            )}
          </>
        ) : done.data ? (
          <IncidentRows items={done.data} empty="Nothing closed yet." />
        ) : (
          <Skeleton className="m-3 h-40" />
        )}
      </Card>
    </>
  );
}

function Toggle({ on, onClick, children }: { on: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      type="button"
      aria-pressed={on}
      onClick={onClick}
      className={cn("min-h-[34px] rounded-full px-3", on ? "bg-ink font-semibold text-white" : "border border-[#D6DEE7] text-ink-3 hover:bg-subtle")}
    >
      {children}
    </button>
  );
}
