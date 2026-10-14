"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { Check, Loader2, TrendingUp } from "lucide-react";
import { EmptyState, LockBadge, Mono, PageTitle } from "@/components/common";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardHeader, CardTitle } from "@/components/ui/card";
import { ChoiceCard, Input, Label, RadioGroup, Textarea } from "@/components/ui/form";
import { diagnosisConfidence, receiptsView, userShortName, type OptionView } from "@/lib/contract";
import { elapsedText, formatTime, money, percent } from "@/lib/format";
import { agentName, auditLabel, MANUAL_PLANNING_TIME, roleLabel } from "@/lib/labels";
import { can } from "@/lib/permissions";
import { useApproval, useAudit, useAuditVerify, useEvents, useFeedback, useFeedbackResult, useIncident, useOptions, useOverview } from "@/lib/queries";
import { useAppState } from "@/lib/app-state";
import { LifecycleStepper, StageBadge, StageDetail } from "@/components/incidents";
import type { IncidentOverview } from "@/lib/contract";
import { Wrench } from "lucide-react";
import { useSession } from "@/lib/session";
import { cn } from "@/lib/utils";
import { errorCode } from "@/lib/backend";

const DONE_STATUSES = ["approved", "executed", "resolved"];

export default function FixedPage() {
  const { incidentId } = useParams<{ incidentId: string }>();
  const { me } = useSession();
  const incident = useIncident(incidentId);
  const approval = useApproval(incidentId).data ?? null;
  const options = useOptions(incidentId);
  const events = useEvents(incidentId);
  const canAudit = can(me, "view_audit");
  const audit = useAudit(canAudit, incidentId);
  const verify = useAuditVerify(canAudit);
  const { simMode } = useAppState();
  const overview = useOverview(simMode ? incidentId : null);

  const inc = incident.data;
  const chosenId = approval?.option_id ?? inc?.selected_option_id ?? null;
  const chosen = options.data?.options.find((o) => o.id === chosenId) ?? null;
  const without = pickWithout(options.data?.options ?? [], chosen);
  const done = !!approval || (inc ? DONE_STATUSES.includes(inc.status) : false);

  if (!done) {
    return (
      <>
        <PageTitle title="Fixed" />
        <EmptyState title={incident.isLoading ? "Loading…" : "Nothing has been approved for this issue yet."}>
          {!incident.isLoading && (
            <>
              <Link href={`/options/${incidentId}`}>Go to the options</Link> to choose what to do.
            </>
          )}
        </EmptyState>
      </>
    );
  }

  const receipts = approval ? receiptsView(approval) : [];
  // simulated plant: the overview carries the approver's real name
  const chain = (approval?.signoffs ?? []).filter((s) => s.decision === "approved" && s.signed_name);
  const approver = approval
    ? chain.length > 1
      ? chain.map((s) => `${s.signed_name!.replace(/\s*\(.*\)$/, "")} (${s.label})`).join(", ")
      : (overview.data?.approval?.name ?? `${userShortName(approval.approver_id)} (${roleLabel(approval.approver_role)})`)
    : null;
  const letter = chosen?.letter ?? chosenId?.replace(/^OPT-/, "") ?? "";
  const decisionAt = (events.data ?? []).find((e) => e.agent === "decision" && e.kind === "result")?.ts;
  const okCount = receipts.filter((r) => r.ok).length;

  // "Activity record": from the audit log when allowed (view_audit), otherwise built from the incident itself.
  const timeline: { at: string; who: string; what: React.ReactNode }[] = canAudit
    ? (audit.data?.items ?? []).slice(-8).map((a) => ({ at: a.ts, who: actorName(a.actor_type, a.actor), what: a.resource ? `${auditLabel(a.action)} · ${a.resource}` : auditLabel(a.action) }))
    : [
        ...(inc ? [{ at: inc.created_at, who: agentName("monitor"), what: <>Opened issue <Mono>{incidentId}</Mono></> }] : []),
        ...(decisionAt ? [{ at: decisionAt, who: agentName("decision"), what: "Suggested options" }] : []),
        ...(approval && approver ? [{ at: approval.approved_at, who: approver, what: `Approved option ${letter} and signed` }] : []),
        ...receipts.filter((r) => r.ref).map((r) => ({ at: approval!.approved_at, who: "System", what: <>Created <Mono>{r.ref}</Mono></> })),
      ];

  return (
    <>
      <div role="status" className="flex flex-wrap items-center gap-3.5 rounded-lg border border-ok-border bg-ok-soft px-[18px] py-4">
        <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-ok">
          <Check className="h-[22px] w-[22px] text-white" aria-hidden />
        </span>
        <div className="flex flex-[1_1_420px] flex-col gap-0.5">
          <div className="text-[17px] font-semibold text-ok-deep">
            {approval ? (
              <>
                Option {letter} approved by {approver}. Everything is set up.
              </>
            ) : inc?.status === "resolved" ? (
              <>Issue {incidentId} is resolved.</>
            ) : (
              <>Option {letter || "?"} was approved for issue {incidentId}.</>
            )}
          </div>
          {approval && (
            <div className="text-sm text-[#2F5E43]">
              Signed at {formatTime(approval.approved_at)} · {okCount} {okCount === 1 ? "step was" : "steps were"} carried out automatically
            </div>
          )}
        </div>
      </div>

      <div className="flex flex-wrap items-start gap-4">
        <div className="flex min-w-0 flex-[3_1_560px] flex-col gap-4">
          {receipts.length > 0 && (
            <div className="grid grid-cols-[repeat(auto-fit,minmax(260px,1fr))] gap-4">
              {receipts.map((r, i) => (
                <Card key={r.ref ?? `${r.kind}-${i}`} className="flex flex-col gap-3">
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-[13px] font-semibold text-ink-4">{receiptTitle(r.kind)}</span>
                    <Badge tone={r.ok ? "ok" : "crit"}>{r.ok ? "Done" : "Failed"}</Badge>
                  </div>
                  {r.ref && <Mono className="text-[22px] font-medium">{r.ref}</Mono>}
                  {r.message && <div className="text-sm text-ink-3">{r.message}</div>}
                </Card>
              ))}
            </div>
          )}

          {chosen && without && (
            <Card className="flex flex-col gap-3">
              <CardTitle>What PlantGuard changed</CardTitle>
              <div className="overflow-x-auto">
                <table className="w-full min-w-[440px] border-collapse text-sm">
                  <thead>
                    <tr className="text-left text-[13px] text-ink-4">
                      <th scope="col" className="py-2 font-semibold" />
                      <th scope="col" className="px-3 py-2 font-semibold">Without PlantGuard</th>
                      <th scope="col" className="px-3 py-2 font-semibold text-brand-text">With PlantGuard</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr className="border-t border-line-soft">
                      <th scope="row" className="py-3 text-left font-medium text-ink-3">Cost</th>
                      <td className="px-3 py-3 font-mono text-xl text-crit-text">{can(me, "view_costs") ? money(without.cost) : "€ ••••"}</td>
                      <td className="px-3 py-3 font-mono text-xl text-ok-text">{can(me, "view_costs") ? money(chosen.cost) : "€ ••••"}</td>
                    </tr>
                    <tr className="border-t border-line-soft">
                      <th scope="row" className="py-3 text-left font-medium text-ink-3">Time to make a plan</th>
                      <td className="px-3 py-3 text-lg text-crit-text">{MANUAL_PLANNING_TIME}</td>
                      <td className="px-3 py-3 text-lg text-ok-text">{inc && approval ? elapsedText(inc.created_at, approval.approved_at) : "—"}</td>
                    </tr>
                    <tr className="border-t border-line-soft">
                      <th scope="row" className="py-3 text-left font-medium text-ink-3">Order deadline</th>
                      <td className="px-3 py-3 text-lg text-crit-text">{deadlineText(without)}</td>
                      <td className={cn("px-3 py-3 text-lg", chosen.deadline === "met" ? "text-ok-text" : "text-warn-text")}>{deadlineText(chosen)}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </Card>
          )}

          <Card className="flex flex-col gap-2.5">
            <CardHeader>
              <CardTitle>Activity record</CardTitle>
              {canAudit && verify.data && (
                <Badge tone={verify.data.valid ? "ok" : "crit"}>{verify.data.valid ? "Record untouched" : "Record was changed!"}</Badge>
              )}
            </CardHeader>
            {timeline.length === 0 && <p className="m-0 text-sm text-ink-4">Nothing recorded yet.</p>}
            <ol className="m-0 list-none p-0">
              {timeline.map((t, i) => (
                <li key={i} className="flex flex-wrap items-baseline gap-x-3.5 gap-y-0.5 border-b border-line-soft py-2.5 text-sm last:border-b-0">
                  <span className="w-16 shrink-0 font-mono text-[13px] text-ink-5">{formatTime(t.at)}</span>
                  <span className="w-36 shrink-0 text-ink-4">{t.who}</span>
                  <span className="min-w-0 flex-1">{t.what}</span>
                </li>
              ))}
            </ol>
          </Card>
        </div>

        <aside className="flex min-w-0 flex-[2_1_340px] flex-col gap-4">
          {simMode ? (
            overview.data ? <RepairStatusCard o={overview.data} /> : null
          ) : (
            <FeedbackCard
              incidentId={incidentId}
              technician={chosen?.repair?.technician ?? ""}
              confidenceBefore={diagnosisConfidence(events.data ?? []) ?? inc?.diagnosis?.confidence ?? null}
            />
          )}
        </aside>
      </div>
    </>
  );
}

/**
 * Simulated plant (v1.2): approval is not the end. The technician does the repair, the machine is checked, and only
 * the technician closes the issue — the result then goes to the maintenance log.
 */
function RepairStatusCard({ o }: { o: IncidentOverview }) {
  const done = o.stage === "resolved";
  return (
    <Card className="flex flex-col gap-3.5 border-2 border-brand p-[19px]">
      <CardHeader>
        <CardTitle>The repair</CardTitle>
        <StageBadge stage={o.stage} />
      </CardHeader>
      <LifecycleStepper o={o} />
      <StageDetail o={o} />
      <p className="m-0 text-sm text-ink-3">
        {done ? (
          <>Closed by the technician. The fix is in the maintenance log and the manuals library, so the agents learn from it.</>
        ) : (
          <>
            {o.technician ? <strong>{o.technician.name}</strong> : "The technician"} sees option {o.option_id?.replace("OPT-", "") ?? ""} in their
            jobs, does the repair and updates the status. After the machine check passes, only they can close the issue.
          </>
        )}
      </p>
      <Link href={`/jobs/${o.incident.id}`} className="flex items-center gap-1.5 self-start text-sm font-semibold">
        <Wrench className="h-4 w-4" aria-hidden /> Open the repair job
      </Link>
    </Card>
  );
}

/** Audit actor in plain words: agents by their team name, users by their short name. */
function actorName(type: string, actor: string) {
  if (type === "agent") return agentName(actor);
  if (type === "system") return actor === "system" ? "System" : actor.charAt(0).toUpperCase() + actor.slice(1);
  return userShortName(actor);
}

function receiptTitle(kind: string) {
  const k = kind.toLowerCase();
  if (k.includes("purchase")) return "Purchase order";
  if (k.includes("transfer") || k.includes("stock")) return "Part transfer";
  if (k.includes("work_order") || k.includes("repair")) return "Repair job";
  if (k.includes("speed")) return "Line speed change";
  return kind.replace(/_/g, " ");
}

function deadlineText(o: OptionView) {
  return o.deadline === "met" ? "On time" : o.deadline === "at_risk" ? "At risk" : "Late";
}

/** The "without PlantGuard" baseline: the costliest option that misses the deadline (C9: stop now). */
function pickWithout(options: OptionView[], chosen: OptionView | null): OptionView | null {
  const others = options.filter((o) => o.id !== chosen?.id);
  const missed = others.filter((o) => o.deadline === "missed");
  const pool = missed.length ? missed : others;
  return [...pool].sort((a, b) => b.cost.amount - a.cost.amount)[0] ?? null;
}

function FeedbackCard({ incidentId, technician, confidenceBefore }: { incidentId: string; technician: string; confidenceBefore: number | null }) {
  const { me } = useSession();
  const feedback = useFeedback(incidentId);
  const result = useFeedbackResult(incidentId).data ?? null;
  const [confirmed, setConfirmed] = useState("yes");
  const [cause, setCause] = useState("");
  const [tech, setTech] = useState(technician);
  const [notes, setNotes] = useState("");
  const allowed = can(me, "submit_feedback");
  const after = result?.new_similar_case_confidence ?? null;

  return (
    <Card id="feedback" className="flex scroll-mt-6 flex-col gap-4 border-2 border-brand p-[19px]">
      <div className="flex flex-col gap-1">
        <span className="text-[13px] font-semibold text-brand-text">After the repair</span>
        <h2 className="m-0 text-[19px] font-semibold">Was the AI right?</h2>
        <span className="text-sm text-ink-4">Your answer teaches the AI for next time.</span>
      </div>
      {!allowed ? (
        <div className="flex flex-col items-start gap-2">
          <LockBadge />
          <p className="m-0 text-sm text-ink-4">The technician or Maintenance Lead sends this. You’re signed in as {me ? roleLabel(me.role) : "a viewer"}.</p>
        </div>
      ) : result ? (
        <div className="flex items-start gap-3 rounded-[10px] border border-ok-border bg-ok-soft px-3.5 py-3">
          <TrendingUp className="mt-0.5 h-5 w-5 shrink-0 text-ok-text" aria-hidden />
          <span className="text-sm leading-relaxed text-ok-deep">
            Thanks!{" "}
            {after !== null ? (
              <>
                Next time a similar problem appears, the AI will be <strong>{percent(after)} sure</strong>
                {confidenceBefore !== null && <> instead of {percent(confidenceBefore)}</>}.
              </>
            ) : result.stored_for_retraining ? (
              <>Your answer was saved and will be used the next time the AI is trained.</>
            ) : (
              <>Your answer was saved.</>
            )}{" "}
            <Link href="/accuracy">See warning accuracy</Link>
          </span>
        </div>
      ) : (
        <form
          className="flex flex-col gap-4"
          onSubmit={(e) => {
            e.preventDefault();
            feedback.mutate({
              technician_id: tech.trim(),
              confirmed: confirmed === "yes",
              actual_root_cause: cause.trim(),
              notes: notes.trim() || null,
            });
          }}
        >
          <fieldset className="m-0 flex flex-col gap-2 border-0 p-0">
            <legend className="mb-2 p-0 text-sm font-semibold text-ink-2">Did the technician find the problem the AI predicted?</legend>
            <RadioGroup value={confirmed} onValueChange={setConfirmed}>
              <ChoiceCard value="yes" tone="ok">Yes</ChoiceCard>
              <ChoiceCard value="no">No</ChoiceCard>
            </RadioGroup>
          </fieldset>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="cause">What did the technician find?</Label>
            <Input id="cause" value={cause} onChange={(e) => setCause(e.target.value)} placeholder="For example: rear bearing outer race worn" required />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="tech">Technician</Label>
            <Input id="tech" value={tech} onChange={(e) => setTech(e.target.value)} placeholder="For example: TECH-104" required />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="notes">Notes (optional)</Label>
            <Textarea id="notes" value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="Parts used, anything unusual" rows={3} />
          </div>
          {feedback.error ? (
            <p role="alert" className="m-0 text-sm text-crit-text">
              {["FORBIDDEN", "PLANT_SCOPE"].includes(errorCode(feedback.error) ?? "") ? "Not allowed for your role." : "Couldn’t send the feedback. Try again."}
            </p>
          ) : null}
          <Button type="submit" size="lg" disabled={feedback.isPending}>
            {feedback.isPending && <Loader2 className="animate-spin" />} Send feedback
          </Button>
        </form>
      )}
    </Card>
  );
}
