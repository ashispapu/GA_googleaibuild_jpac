"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { BookOpen, Check, Clock, Loader2, MessageCircleQuestion, Package, PlayCircle, RotateCcw, Truck, Wrench } from "lucide-react";
import { useAssistant } from "@/components/assistant";
import { LockBadge, Mono, PageTitle, QueryError, Skeleton } from "@/components/common";
import { LifecycleStepper, StageBadge, Timeline } from "@/components/incidents";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardHeader, CardTitle } from "@/components/ui/card";
import { ChoiceCard, Input, Label, RadioGroup, Textarea } from "@/components/ui/form";
import { backend, errorCode } from "@/lib/backend";
import type { JobView, PartNeed } from "@/lib/contract";
import { durationText, formatDayTime, formatTime, percent } from "@/lib/format";
import { failureModeLabel } from "@/lib/labels";
import { useHealth, useJob, useJobAction } from "@/lib/queries";
import { cn } from "@/lib/utils";

export default function JobPage() {
  const { incidentId } = useParams<{ incidentId: string }>();
  const job = useJob(incidentId);
  if (job.error && !job.data) return <QueryError error={job.error} onRetry={() => void job.refetch()} what="this job" />;
  if (!job.data) return <Skeleton className="h-96" />;
  return <Job j={job.data} />;
}

function Job({ j }: { j: JobView }) {
  const inc = j.incident;
  const o = j.overview;
  const wo = j.work_order;
  const ask = useAssistant();
  return (
    <>
      <PageTitle
        title={
          <span className="flex flex-wrap items-center gap-3">
            {wo ? <>Job <Mono>{wo.id}</Mono></> : <>Issue <Mono>{inc.id}</Mono></>}
            <StageBadge stage={o.stage} />
            {inc.round > 1 && <Badge tone="warn">Analysis round {inc.round}</Badge>}
          </span>
        }
        sub={
          <>
            {o.asset_name} <Mono>{inc.asset_id}</Mono> · {inc.plant_id.replace("PLANT-", "Plant ")} · {o.line_name} · issue{" "}
            <Link href={`/investigation/${inc.id}`}>
              <Mono>{inc.id}</Mono>
            </Link>
          </>
        }
        right={
          <Button variant="soft" onClick={() => ask.open({ incidentId: inc.id, assetId: inc.asset_id })}>
            <MessageCircleQuestion /> Ask PlantGuard about this job
          </Button>
        }
      />
      <Card className="py-3.5">
        <LifecycleStepper o={o} />
      </Card>

      <div className="flex flex-wrap items-start gap-4">
        <div className="flex min-w-0 flex-[3_1_560px] flex-col gap-4">
          <WhatToDo j={j} />
          <PartsCard parts={j.parts} />
          <ManualsCard j={j} />
          <Card className="flex flex-col gap-3">
            <CardTitle>Work log</CardTitle>
            <Timeline items={j.updates} />
          </Card>
        </div>
        <aside className="flex min-w-0 flex-[2_1_340px] flex-col gap-4">
          <Actions j={j} />
          <MachineCard j={j} />
        </aside>
      </div>
    </>
  );
}

function WhatToDo({ j }: { j: JobView }) {
  const d = j.diagnosis;
  const wo = j.work_order;
  const o = j.overview;
  return (
    <Card className="flex flex-col gap-3.5">
      <CardHeader>
        <CardTitle>What was decided</CardTitle>
        {o.approval && (
          <span className="text-[13px] text-ink-4">
            Approved by <strong className="text-ink">{o.approval.name}</strong>
            {o.approved_at && <> · {formatDayTime(o.approved_at)}</>}
          </span>
        )}
      </CardHeader>
      {j.option ? (
        <div className="flex flex-col gap-1.5 rounded-xl border border-brand-border bg-brand-tint px-4 py-3">
          <div className="text-[15px] font-semibold">
            Option {j.option.id.replace("OPT-", "")}: {j.option.label}
          </div>
          <div className="text-sm leading-relaxed text-ink-3">{j.option.summary}</div>
          {j.approval_reason && <div className="text-sm text-ink-2">“{j.approval_reason}”</div>}
        </div>
      ) : (
        <p className="m-0 text-sm text-ink-4">No option approved yet.</p>
      )}
      {d && (
        <div className="flex flex-col gap-1 text-sm">
          <div>
            <span className="text-ink-4">AI diagnosis: </span>
            <strong>{d.root_cause}</strong> <span className="text-ink-4">({failureModeLabel(d.failure_mode)}, {percent(d.confidence)} sure)</span>
          </div>
          {d.alternatives.length > 0 && <div className="text-ink-4">Not ruled out: {d.alternatives.join("; ")}</div>}
          {j.incident.excluded_failure_modes.length > 0 && (
            <div className="text-warn-text">Already tried without success: {j.incident.excluded_failure_modes.map(failureModeLabel).join(", ")}</div>
          )}
          {j.incident.previous_findings && <div className="text-ink-4">Earlier findings: {j.incident.previous_findings}</div>}
        </div>
      )}
      {wo && (
        <div className="grid grid-cols-[repeat(auto-fit,minmax(160px,1fr))] gap-3 border-t border-line-soft pt-3 text-sm">
          <Fact icon={<Wrench />} label="Work order" value={<><Mono>{wo.id}</Mono> · {wo.title}</>} />
          <Fact icon={<Clock />} label="Planned" value={wo.planned_start ? formatDayTime(wo.planned_start) : "—"} />
          <Fact icon={<Clock />} label="Expected time" value={wo.duration_hours ? durationText(wo.duration_hours) : "—"} />
          <Fact icon={<Check />} label="Assigned to" value={j.overview.technician?.name ?? "—"} />
        </div>
      )}
    </Card>
  );
}

function Fact({ icon, label, value }: { icon: React.ReactNode; label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-start gap-2">
      <span className="mt-0.5 text-ink-4 [&_svg]:h-4 [&_svg]:w-4">{icon}</span>
      <span className="flex flex-col">
        <span className="text-xs text-ink-5">{label}</span>
        <span>{value}</span>
      </span>
    </div>
  );
}

function PartsCard({ parts }: { parts: PartNeed[] }) {
  return (
    <Card className="flex flex-col gap-3">
      <CardTitle>Parts</CardTitle>
      {parts.length === 0 && <p className="m-0 text-sm text-ink-4">No parts on this job.</p>}
      <ul className="m-0 flex list-none flex-col gap-2.5 p-0">
        {parts.map((p) => (
          <li key={p.sku} className="flex flex-wrap items-center gap-3 rounded-lg border border-line px-3 py-2.5">
            <Package className="h-4 w-4 text-ink-4" aria-hidden />
            <span className="flex min-w-[180px] flex-1 flex-col">
              <span className="text-sm font-semibold">
                {p.quantity}× <Mono>{p.sku}</Mono>
              </span>
              <span className="text-xs text-ink-4">{p.description}</span>
            </span>
            {p.ready ? (
              <Badge tone="ok">In stock here ({p.in_stock})</Badge>
            ) : p.transfer_status === "delivered" ? (
              <Badge tone="ok">Delivered · {p.transfer_id}</Badge>
            ) : p.purchase_order_id ? (
              <Badge tone={p.purchase_order_status === "delivered" ? "ok" : "warn"}>
                {p.purchase_order_status === "delivered" ? "Received" : "Ordered from supplier"} · {p.purchase_order_id}
                {p.purchase_order_eta && p.purchase_order_status !== "delivered" && <> · due {formatDayTime(p.purchase_order_eta)}</>}
              </Badge>
            ) : p.transfer_id ? (
              <Badge tone="warn">
                <Truck className="h-3.5 w-3.5" /> {p.transfer_status === "in_transit" ? "On the way" : "Booked"} · {p.transfer_id}
                {p.transfer_eta && <> · ETA {formatDayTime(p.transfer_eta)}</>}
              </Badge>
            ) : (
              <Badge tone="crit">Not in stock</Badge>
            )}
          </li>
        ))}
      </ul>
    </Card>
  );
}

function ManualsCard({ j }: { j: JobView }) {
  return (
    <Card className="flex flex-col gap-3">
      <CardHeader>
        <CardTitle>Manual sections for this repair</CardTitle>
        <Link href="/manuals" className="text-[13px]">All manuals</Link>
      </CardHeader>
      {j.manuals.length === 0 && <p className="m-0 text-sm text-ink-4">No manual section found.</p>}
      <ul className="m-0 flex list-none flex-col gap-2 p-0">
        {j.manuals.map((m) => (
          <li key={`${m.doc_id}-${m.section}`}>
            <Link
              href={`/manuals/${encodeURIComponent(m.doc_id)}#s-${m.section}`}
              className="flex items-center gap-3 rounded-lg border border-line px-3 py-2.5 text-ink no-underline hover:border-brand-border hover:bg-brand-tint"
            >
              <BookOpen className="h-4 w-4 text-brand" aria-hidden />
              <span className="flex flex-1 flex-col">
                <span className="text-sm font-semibold">
                  §{m.section} {m.heading}
                </span>
                <span className="text-xs text-ink-4">
                  {m.title} · page {m.page}
                </span>
              </span>
              <Badge tone={m.why === "Repair procedure" ? "brand" : "neutral"}>{m.why}</Badge>
            </Link>
          </li>
        ))}
      </ul>
    </Card>
  );
}

function MachineCard({ j }: { j: JobView }) {
  const h = useHealth(j.incident.asset_id);
  const state = j.overview.machine_state;
  return (
    <Card className="flex flex-col gap-2.5">
      <CardHeader>
        <CardTitle>Machine now</CardTitle>
        <Link href={`/machines/${j.incident.asset_id}`} className="text-[13px]">Open machine</Link>
      </CardHeader>
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <Badge tone={state === "run" ? "ok" : state === "fault" ? "crit" : "neutral"}>
          {state === "run" ? "Running" : state === "stopped" ? "Stopped for maintenance" : state === "fault" ? "Broken down" : state === "idle" ? "Waiting (line stopped)" : state}
        </Badge>
        {h.data && (
          <span>
            Breakdown risk <strong className={cn(h.data.band === "critical" ? "text-crit-text" : h.data.band === "warning" ? "text-warn-text" : "text-ok-text")}>{percent(h.data.risk)}</strong>{" "}
            <span className="text-ink-4">· {h.data.problem}</span>
          </span>
        )}
      </div>
      {h.data && h.data.reasons.length > 0 && <div className="text-[13px] text-ink-4">{h.data.reasons.slice(0, 2).map((r) => r.text).join(" · ")}</div>}
    </Card>
  );
}

function Actions({ j }: { j: JobView }) {
  const inc = j.incident;
  const id = inc.id;
  const ask = useAssistant();
  const stage = j.overview.stage;
  const start = useJobAction(id, (note: string) => backend.workStart(id, note));
  const note = useJobAction(id, (text: string) => backend.workNote(id, text));
  const complete = useJobAction(id, (b: { findings: string; parts: { sku: string; quantity: number }[] }) =>
    backend.workComplete(id, { findings: b.findings, parts_used: b.parts }));
  const close = useJobAction(id, (b: { confirmed: boolean; cause: string; notes: string }) =>
    backend.resolve(id, { confirmed: b.confirmed, actual_root_cause: b.cause, notes: b.notes || null }));
  const again = useJobAction(id, (reason: string) => backend.reanalyse(id, reason));
  const [noteText, setNoteText] = useState("");
  const [findings, setFindings] = useState("");
  const [used, setUsed] = useState<Record<string, boolean>>({});
  const [extraSku, setExtraSku] = useState("");
  const [confirmed, setConfirmed] = useState("yes");
  const [cause, setCause] = useState(j.diagnosis?.root_cause ?? "");
  const [closeNotes, setCloseNotes] = useState("");
  const [reason, setReason] = useState("");
  useEffect(() => {
    setUsed(Object.fromEntries((j.work_order?.parts ?? []).map((p) => [p.sku, true])));
  }, [j.work_order?.id, j.work_order?.parts]);
  const err = start.error || note.error || complete.error || close.error || again.error;
  const errText = err ? (["FORBIDDEN", "PLANT_SCOPE"].includes(errorCode(err) ?? "") ? null : err instanceof Error ? err.message : "Something went wrong.") : null;

  return (
    <Card id="actions" className="flex flex-col gap-4 border-2 border-brand p-[19px]">
      <div className="flex flex-col gap-1">
        <span className="text-[13px] font-semibold text-brand-text">Your next step</span>
        <h2 className="m-0 text-[19px] font-semibold">{stepTitle(stage)}</h2>
      </div>
      {!j.is_assignee && stage !== "resolved" && (
        <div className="flex flex-col items-start gap-2 rounded-lg border border-line bg-subtle p-3 text-sm">
          <LockBadge />
          <span className="text-ink-4">
            {j.overview.technician ? <>This job is assigned to <strong>{j.overview.technician.name}</strong>. Only they can start, finish and close it.</> : "No technician assigned yet."}
          </span>
        </div>
      )}

      {stage === "waiting_technician" && j.is_assignee && (
        <>
          {j.blocked_reason && <p className="m-0 rounded-lg border border-warn-border bg-warn-soft px-3 py-2 text-sm text-warn-deep">{j.blocked_reason}. You can start when it is in stock.</p>}
          <Textarea value={noteText} onChange={(e) => setNoteText(e.target.value)} placeholder="Optional note (e.g. line locked out, LOTO tag 14)" rows={2} />
          <Button size="lg" disabled={!j.can_start || start.isPending} onClick={() => start.mutate(noteText)}>
            {start.isPending ? <Loader2 className="animate-spin" /> : <PlayCircle />} Start the repair
          </Button>
          <p className="m-0 text-xs text-ink-5">Starting stops the machine for maintenance (and its line).</p>
        </>
      )}

      {stage === "technician_working" && j.is_assignee && (
        <form
          className="flex flex-col gap-3.5"
          onSubmit={(e) => {
            e.preventDefault();
            const parts = Object.entries(used).filter(([, v]) => v).map(([sku]) => ({ sku, quantity: j.work_order?.parts.find((p) => p.sku === sku)?.quantity ?? 1 }));
            if (extraSku.trim()) parts.push({ sku: extraSku.trim().toUpperCase(), quantity: 1 });
            complete.mutate({ findings, parts });
          }}
        >
          <div className="flex gap-2">
            <Input value={noteText} onChange={(e) => setNoteText(e.target.value)} placeholder="Add a note to the work log" />
            <Button type="button" variant="outline" disabled={!noteText.trim() || note.isPending} onClick={() => note.mutate(noteText, { onSuccess: () => setNoteText("") })}>
              Add
            </Button>
          </div>
          <fieldset className="m-0 flex flex-col gap-1.5 border-0 p-0">
            <legend className="mb-1 p-0 text-sm font-semibold text-ink-2">Parts you fitted</legend>
            {(j.work_order?.parts ?? []).map((p) => (
              <label key={p.sku} className="flex items-center gap-2 text-sm">
                <input type="checkbox" checked={!!used[p.sku]} onChange={(e) => setUsed((u) => ({ ...u, [p.sku]: e.target.checked }))} />
                {p.quantity}× <Mono>{p.sku}</Mono>
              </label>
            ))}
            <Input value={extraSku} onChange={(e) => setExtraSku(e.target.value)} placeholder="Other part fitted (SKU), optional" />
          </fieldset>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="findings">What did you find and do?</Label>
            <Textarea id="findings" value={findings} onChange={(e) => setFindings(e.target.value)} rows={3} required placeholder="e.g. Rear bearing outer race pitted; replaced, greased 12 g" />
          </div>
          <Button type="submit" size="lg" disabled={!findings.trim() || complete.isPending}>
            {complete.isPending ? <Loader2 className="animate-spin" /> : <Check />} Repair done, restart the machine
          </Button>
          <p className="m-0 text-xs text-ink-5">The machine restarts and is watched before you can close the issue.</p>
        </form>
      )}

      {stage === "verifying" && (
        <div className="flex items-start gap-3 rounded-lg border border-brand-border bg-brand-tint px-3 py-3 text-sm">
          <Loader2 className="mt-0.5 h-4 w-4 shrink-0 animate-spin text-brand" aria-hidden />
          <span>
            The machine is running again. PlantGuard watches the readings until{" "}
            <strong>{j.overview.verification ? formatTime(j.overview.verification.ends_at) : "the check ends"}</strong> (plant time). If they stay normal you can close the issue.
          </span>
        </div>
      )}

      {stage === "ready_to_close" && j.is_assignee && (
        <form
          className="flex flex-col gap-3.5"
          onSubmit={(e) => {
            e.preventDefault();
            close.mutate({ confirmed: confirmed === "yes", cause, notes: closeNotes });
          }}
        >
          <p className="m-0 rounded-lg border border-ok-border bg-ok-soft px-3 py-2 text-sm text-ok-deep">{j.overview.verification?.details ?? "Check passed."}</p>
          <fieldset className="m-0 flex flex-col gap-2 border-0 p-0">
            <legend className="mb-2 p-0 text-sm font-semibold text-ink-2">Was the AI’s diagnosis right?</legend>
            <RadioGroup value={confirmed} onValueChange={setConfirmed}>
              <ChoiceCard value="yes" tone="ok">Yes</ChoiceCard>
              <ChoiceCard value="no">No</ChoiceCard>
            </RadioGroup>
          </fieldset>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="cause">Actual root cause</Label>
            <Input id="cause" value={cause} onChange={(e) => setCause(e.target.value)} required />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="cnotes">Notes (optional)</Label>
            <Textarea id="cnotes" value={closeNotes} onChange={(e) => setCloseNotes(e.target.value)} rows={2} placeholder="Anything the next technician should know" />
          </div>
          <Button type="submit" size="lg" disabled={!cause.trim() || close.isPending}>
            {close.isPending ? <Loader2 className="animate-spin" /> : <Check />} Close the issue
          </Button>
          <p className="m-0 text-xs text-ink-5">Your fix is added to the maintenance log and the manual search.</p>
        </form>
      )}

      {stage === "repair_failed" && (
        <div className="flex flex-col gap-3">
          <p className="m-0 rounded-lg border border-crit-border bg-crit-soft px-3 py-2 text-sm text-crit-deep">
            {j.overview.verification?.details ?? "The readings are still abnormal after the repair."} The issue stays open.
          </p>
          <Button variant="soft" onClick={() => ask.open({ incidentId: id, assetId: inc.asset_id, question: "The repair didn't fix it. What else could it be?" })}>
            <MessageCircleQuestion /> Ask PlantGuard what else it could be
          </Button>
          {j.can_reanalyse && (
            <form
              className="flex flex-col gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                again.mutate(reason);
              }}
            >
              <Label htmlFor="reason">What did you see? (goes to the AI team)</Label>
              <Textarea id="reason" value={reason} onChange={(e) => setReason(e.target.value)} rows={2} required placeholder="e.g. new bearing fitted, vibration still high, coupling feels loose" />
              <Button type="submit" disabled={!reason.trim() || again.isPending}>
                {again.isPending ? <Loader2 className="animate-spin" /> : <RotateCcw />} Ask the AI team to analyse again
              </Button>
            </form>
          )}
        </div>
      )}

      {stage === "ai_analysing" && <p className="m-0 text-sm text-ink-4">The AI team is analysing. <Link href={`/investigation/${id}`}>Watch it work</Link>.</p>}
      {stage === "awaiting_approval" && (
        <p className="m-0 text-sm text-ink-4">
          Waiting for a Maintenance Lead to choose an option. <Link href={`/options/${id}`}>See the options</Link>.
        </p>
      )}
      {stage === "resolved" && <p className="m-0 text-sm text-ok-deep">Closed. The fix is in the maintenance log.</p>}
      {errText && <p role="alert" className="m-0 text-sm text-crit-text">{errText}</p>}
      {err && !errText && <LockBadge />}
    </Card>
  );
}

function stepTitle(stage: string): string {
  return ({
    ai_analysing: "Wait for the AI team",
    awaiting_approval: "Wait for approval",
    waiting_technician: "Start the repair",
    technician_working: "Do the repair",
    verifying: "Machine check running",
    ready_to_close: "Confirm and close",
    repair_failed: "The repair did not work",
    resolved: "Done",
    rejected: "Rejected",
  } as Record<string, string>)[stage] ?? "Next step";
}
