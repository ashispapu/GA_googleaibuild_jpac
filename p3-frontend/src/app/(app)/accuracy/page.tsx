"use client";

import Link from "next/link";
import { EmptyState, PageTitle, QueryError, Skeleton } from "@/components/common";
import { Card, CardTitle } from "@/components/ui/card";
import { buttonVariants } from "@/components/ui/button";
import { useAppState } from "@/lib/app-state";
import { percent } from "@/lib/format";
import { diagnosisConfidence } from "@/lib/contract";
import { useAccuracy, useEvents, useFeedbackResult, useIncident } from "@/lib/queries";
import { cn } from "@/lib/utils";

/** "17 of 20" style dots: easier to read than a percentage or a confusion matrix. */
function Dots({ filled, total, missTone }: { filled: number; total: number; missTone: "crit" | "warn" }) {
  return (
    <div role="img" aria-label={`${filled} of ${total}`} className="grid w-fit grid-cols-[repeat(10,18px)] gap-1.5">
      {Array.from({ length: total }, (_, i) => (
        <span
          key={i}
          className={cn(
            "box-border h-[18px] w-[18px] rounded-full",
            i < filled ? "bg-brand" : missTone === "crit" ? "border-2 border-[#E5544B]" : "border-2 border-warn",
          )}
        />
      ))}
    </div>
  );
}

const WATCHES = [
  "How fast the shaking is growing",
  "Sharp knocks inside the vibration",
  "High-pitched vibration",
  "Bearing getting warmer",
  "Time since the last service",
];

export default function AccuracyPage() {
  const accuracy = useAccuracy();
  const { currentIncidentId: activeIncidentId } = useAppState();
  const learned = useFeedbackResult(activeIncidentId).data ?? null;
  const incident = useIncident(learned ? activeIncidentId : null);
  const events = useEvents(learned ? activeIncidentId : null);
  const after = learned?.new_similar_case_confidence ?? null;
  const before = diagnosisConfidence(events.data ?? []) ?? incident.data?.diagnosis?.confidence ?? null;
  const a = accuracy.data;

  if (accuracy.error) return <QueryError error={accuracy.error} onRetry={() => void accuracy.refetch()} what="the accuracy numbers" />;

  const caught = a?.recall !== null && a?.recall !== undefined ? Math.round(a.recall * 20) : null;
  const real = a?.precision !== null && a?.precision !== undefined ? Math.round(a.precision * 10) : null;

  return (
    <>
      <PageTitle
        title="How reliable are the warnings?"
        sub="Tested on machine data the AI had never seen before."
        right={a && <span className="rounded-full border border-line bg-surface px-3 py-1.5 text-[13px] text-ink-4">AI version {a.version}</span>}
      />

      {!a && accuracy.isLoading ? (
        <div className="grid grid-cols-[repeat(auto-fit,minmax(300px,1fr))] gap-4">
          <Skeleton className="h-56" />
          <Skeleton className="h-56" />
          <Skeleton className="h-56" />
        </div>
      ) : !a ? (
        <EmptyState title="No model results yet." />
      ) : (
        <div className="grid grid-cols-[repeat(auto-fit,minmax(300px,1fr))] gap-4">
          {caught !== null && (
            <section aria-label="Breakdowns caught" className="flex flex-col gap-3.5 rounded-[14px] border border-line bg-surface p-5">
              <h2 className="m-0 text-[15px] font-semibold text-ink-4">Catches most breakdowns early</h2>
              <div className="flex flex-wrap items-baseline gap-2">
                <span className="text-[34px] font-semibold text-brand-text">{caught} of 20</span>
                <span className="text-[15px] text-ink-3">breakdowns</span>
              </div>
              <Dots filled={caught} total={20} missTone="crit" />
              <p className="m-0 text-sm leading-relaxed text-ink-4">
                were spotted <strong className="text-ink">before</strong> they happened. {20 - caught} were missed.
              </p>
            </section>
          )}
          {real !== null && (
            <section aria-label="Warnings that were real" className="flex flex-col gap-3.5 rounded-[14px] border border-line bg-surface p-5">
              <h2 className="m-0 text-[15px] font-semibold text-ink-4">Most warnings are real</h2>
              <div className="flex flex-wrap items-baseline gap-2">
                <span className="text-[34px] font-semibold text-brand-text">{real} of 10</span>
                <span className="text-[15px] text-ink-3">warnings</span>
              </div>
              <Dots filled={real} total={10} missTone="warn" />
              <p className="m-0 text-sm leading-relaxed text-ink-4">
                turned out to be <strong className="text-ink">real problems</strong>. {10 - real} {10 - real === 1 ? "was a false alarm" : "were false alarms"}.
              </p>
            </section>
          )}
          {a.leadTimeGainHours !== null && (
            <section aria-label="Earlier than the old alarm" className="flex flex-col gap-3.5 rounded-[14px] border border-line bg-surface p-5">
              <h2 className="m-0 text-[15px] font-semibold text-ink-4">Warns earlier than the old alarm</h2>
              <div className="flex flex-wrap items-baseline gap-2">
                <span className="text-[34px] font-semibold text-brand-text">{Math.round(a.leadTimeGainHours)} hours</span>
                <span className="text-[15px] text-ink-3">sooner, on average</span>
              </div>
              <p className="m-0 text-sm leading-relaxed text-ink-4">
                The normal vibration alarm only goes off when shaking passes a fixed limit. The AI notices the small signs that come first, which gives the team
                time to plan a repair instead of reacting to a breakdown.
              </p>
            </section>
          )}
        </div>
      )}

      <div className="flex flex-wrap items-stretch gap-4">
        <Card className="flex min-w-0 flex-[3_1_420px] flex-col gap-3">
          <CardTitle>What the AI pays most attention to</CardTitle>
          <p className="m-0 text-sm text-ink-4">The normal alarm only looks at how much a machine shakes. The AI also notices smaller signs that show up first.</p>
          <ol className="m-0 flex list-none flex-col gap-2.5 p-0 text-[15px]">
            {WATCHES.map((w, i) => (
              <li key={w} className="flex items-center gap-3">
                <span className="flex h-[26px] w-[26px] shrink-0 items-center justify-center rounded-full bg-brand-soft text-[13px] font-semibold text-brand-text">{i + 1}</span>
                {w}
              </li>
            ))}
          </ol>
        </Card>
        <section aria-label="Learning from feedback" className="flex min-w-0 flex-[2_1_300px] flex-col gap-3 rounded-lg border border-ok-border bg-ok-soft p-[18px]">
          <h2 className="m-0 text-[17px] font-semibold text-ok-deep">It learns from your feedback</h2>
          {learned ? (
            <>
              <p className="m-0 text-sm leading-relaxed text-[#2F5E43]">The technician’s answer was saved as a new example.</p>
              {after !== null && (
                <div className="flex flex-col gap-1">
                  <span className="text-sm text-[#2F5E43]">For similar problems, the AI is now</span>
                  <span className="text-[30px] font-semibold text-ok-deep">
                    {percent(after)} sure{" "}
                    {before !== null && <span className="text-base font-normal text-[#2F5E43]">(was {percent(before)})</span>}
                  </span>
                </div>
              )}
            </>
          ) : (
            <p className="m-0 text-sm leading-relaxed text-[#2F5E43]">After each repair, the technician says whether the AI was right. Every answer makes the next warning more certain.</p>
          )}
          <Link href="/" className={cn(buttonVariants({ variant: "outline" }), "mt-auto border-[#8FCCA6] text-ok-deep no-underline")}>
            Back to overview
          </Link>
        </section>
      </div>

      {a && (
        <details className="rounded-lg border border-line bg-surface px-[18px] py-3.5">
          <summary className="cursor-pointer text-sm font-semibold text-ink-3">Technical numbers for engineers</summary>
          <div className="flex flex-wrap gap-6 pt-3 text-sm text-ink-3">
            <span>Precision <span className="font-mono">{a.precision?.toFixed(2) ?? "—"}</span></span>
            <span>Recall <span className="font-mono">{a.recall?.toFixed(2) ?? "—"}</span></span>
            <span>F1 <span className="font-mono">{a.f1?.toFixed(2) ?? "—"}</span></span>
            <span>Lead-time gain vs rule <span className="font-mono">{a.leadTimeGainHours !== null ? `+${a.leadTimeGainHours.toFixed(1)} h` : "—"}</span></span>
            <span>Horizon <span className="font-mono">72 h</span></span>
          </div>
        </details>
      )}
    </>
  );
}
