"use client";

import { useParams, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { IncidentGone, Mono, PageTitle, QueryError, Skeleton } from "@/components/common";
import { ApprovalDialog, LockedDialog, OptionCard, OptionsTimeline, ReviewBanner } from "@/components/options";
import { OptionsChat, SignoffPanel } from "@/components/signoff";
import { useAppState } from "@/lib/app-state";
import { errorCode } from "@/lib/backend";
import { formatTime } from "@/lib/format";
import { roleLabel } from "@/lib/labels";
import { can } from "@/lib/permissions";
import { useApprove, useEvents, useHealth, useIncident, useOptions, useOrders } from "@/lib/queries";
import { useSession } from "@/lib/session";

export default function OptionsPage() {
  return (
    <Suspense fallback={null}>
      <Options />
    </Suspense>
  );
}

function Options() {
  const { incidentId } = useParams<{ incidentId: string }>();
  const params = useSearchParams();
  const router = useRouter();
  const { me } = useSession();
  const { simMode, plantId } = useAppState();
  const incident = useIncident(incidentId);
  const options = useOptions(incidentId);
  const events = useEvents(incidentId);
  const health = useHealth(incident.data?.asset_id ?? null);
  const orders = useOrders(incident.data?.plant_id ?? plantId);
  const approve = useApprove(incidentId);

  const [chosenId, setChosenId] = useState<string | null>(null);
  const [lockedOpen, setLockedOpen] = useState(false);
  const [approveOpen, setApproveOpen] = useState(false);
  const [rejected, setRejected] = useState<string | null>(null);
  const [signed, setSigned] = useState<string | null>(null);

  const role = me?.role ?? null;
  const canApprove = can(me, "approve");
  const canSeeCosts = can(me, "view_costs");
  const view = options.data;
  const chosen = view?.options.find((o) => o.id === chosenId) ?? null;

  const choose = (id: string) => {
    setChosenId(id);
    approve.reset();
    if (canApprove) setApproveOpen(true);
    else setLockedOpen(true);
  };

  // Presenter step 6 opens the approval for the recommended option (?approve=1)
  useEffect(() => {
    if (params.get("approve") === "1" && view) {
      const rec = view.options.find((o) => o.recommended) ?? view.options[0];
      if (rec) choose(rec.id);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params, view]);

  // C1: FORBIDDEN from the server always wins → show the lock
  useEffect(() => {
    const code = errorCode(approve.error);
    if (code === "FORBIDDEN" || code === "PLANT_SCOPE") {
      setApproveOpen(false);
      setLockedOpen(true);
    }
  }, [approve.error]);

  const inc = incident.data ?? null;
  const signing = inc?.status === "awaiting_signoff" && inc.signoff?.status === "in_progress" ? inc.signoff : null;
  const decided = !!inc && !["awaiting_approval", "awaiting_signoff", "analysing", "open"].includes(inc.status);
  const lockFor = (optionId: string) =>
    signing && signing.option_id !== optionId
      ? `Option ${signing.option_id.replace("OPT-", "")} is being signed off`
      : decided
        ? "This issue has been decided"
        : null;
  const chatLock = signing
    ? `Option ${signing.option_id.replace("OPT-", "")} is being signed off. The options can change again if someone declines it.`
    : decided
      ? "This issue has been decided; the options are final."
      : null;
  if (incident.data === null) return <IncidentGone id={incidentId} />;
  if (options.error && !view) return <QueryError error={options.error} onRetry={() => void options.refetch()} what="the options" />;

  const order = orders.data?.find((o) => o.line_id === incident.data?.line_id) ?? orders.data?.[0];
  const seen = new Set<string>();
  const evidence = (events.data ?? []).flatMap((e) => e.evidence).filter((ev) => (seen.has(ev.link) ? false : (seen.add(ev.link), true)));

  return (
    <>
      <PageTitle
        title={
          <>
            What should we do about <Mono>{incident.data?.asset_id ?? "this machine"}</Mono>?
          </>
        }
        sub={
          <>
            The AI team compared {view?.options.length ?? "the"} options{incident.data && ` for issue `}
            {incident.data && <Mono>{incidentId}</Mono>}
            {incident.data && ` · opened ${formatTime(incident.data.created_at)}`}
          </>
        }
      />
      {view && <ReviewBanner view={view} />}
      {inc?.signoff && <SignoffPanel incident={inc} view={view} me={me} onFinal={() => router.push(`/fixed/${incidentId}`)} />}
      {signed && signing && (
        <p role="status" className="m-0 rounded-lg border border-ok-border bg-ok-soft px-4 py-3 text-sm text-ok-deep">
          You signed option {signed}. It runs as soon as the others above have signed; they see it in their “Waiting for your signature” list.
        </p>
      )}
      {rejected && (
        <p role="status" className="m-0 rounded-lg border border-line bg-surface px-4 py-3 text-sm text-ink-3">
          You rejected option {rejected}. It is saved with your reason; the other options are still open.
        </p>
      )}
      {!view ? (
        <div className="grid grid-cols-[repeat(auto-fit,minmax(290px,1fr))] gap-4">
          <Skeleton className="h-96" />
          <Skeleton className="h-96" />
          <Skeleton className="h-96" />
        </div>
      ) : (
        <div className="grid grid-cols-[repeat(auto-fit,minmax(290px,1fr))] items-stretch gap-4">
          {view.options.map((o) => (
            <OptionCard key={o.id} option={o} canSeeCosts={canSeeCosts} onChoose={() => choose(o.id)} lockedReason={lockFor(o.id)} />
          ))}
        </div>
      )}
      {view && simMode && <OptionsChat incidentId={incidentId} canChat={canApprove} locked={chatLock} />}
      {view && <OptionsTimeline options={view.options} dueAt={order?.due_at ?? null} hoursLeft={health.data?.hoursLeft ?? null} />}

      {view && (
        <ApprovalDialog
          open={approveOpen}
          onOpenChange={setApproveOpen}
          option={chosen}
          view={view}
          incidentId={incidentId}
          approverLabel={me ? `${me.name} · ${roleLabel(me.role)}` : ""}
          canSeeCosts={canSeeCosts}
          evidence={evidence}
          busy={approve.isPending}
          error={approve.error}
          onDecide={(decision, reason) =>
            chosen &&
            approve.mutate(
              { optionId: chosen.id, decision, reason },
              {
                onSuccess: (a) => {
                  setApproveOpen(false);
                  // v1.2: with an approval chain, the first signature does not run anything yet
                  if (decision === "approve" && a.approval_token) router.push(`/fixed/${incidentId}`);
                  else if (decision === "approve") setSigned(chosen.letter);
                  else setRejected(chosen.letter);
                },
              },
            )
          }
        />
      )}
      <LockedDialog open={lockedOpen} onOpenChange={setLockedOpen} option={chosen} incidentId={incidentId} roleText={me ? `${me.name} · ${roleLabel(me.role)}` : ""} />
    </>
  );
}
