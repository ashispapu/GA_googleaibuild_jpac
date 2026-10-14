"use client";

import { useEffect, useState } from "react";
import { AlertTriangle, Check, Gauge, Lock, Loader2, ShoppingCart, Sparkles, Truck, UserRound, Wrench, X } from "lucide-react";
import { SignersNeeded } from "./signoff";
import type { EvidenceView, OptionView, OptionsView } from "@/lib/contract";
import { durationText, formatDayTime, formatWeekdayTime, money, percent } from "@/lib/format";
import { actionLabel } from "@/lib/labels";
import { errorCode } from "@/lib/backend";
import { cn } from "@/lib/utils";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import { Card, CardHeader, CardTitle } from "./ui/card";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "./ui/dialog";
import { Label, Textarea } from "./ui/form";
import { EvidenceLink, Mono } from "./common";

export function ReviewBanner({ view }: { view: OptionsView }) {
  if (!view.needsHumanReview) return null;
  return (
    <div role="status" className="flex flex-wrap items-center gap-3 rounded-lg border border-warn-border bg-warn-soft px-4 py-3.5">
      <UserRound className="h-[22px] w-[22px] shrink-0 text-warn-text" aria-hidden />
      <div className="flex-[1_1_400px] text-sm leading-relaxed text-warn-deep">
        <strong>The AI is {percent(view.confidence)} sure, so a person must decide.</strong> Nothing happens until a Maintenance Lead or
        Plant Manager approves one option.
        {view.reason && <span className="mt-1 block text-[13px] text-warn-text">{view.reason}</span>}
      </div>
    </div>
  );
}

const DEADLINE = {
  met: { tone: "ok" as const, text: "Meets the deadline" },
  missed: { tone: "crit" as const, text: "Misses the deadline" },
  at_risk: { tone: "warn" as const, text: "Deadline at risk" },
};

type Point = { tone: "good" | "bad" | "warn"; text: React.ReactNode };

export function optionPoints(o: OptionView): Point[] {
  const pts: Point[] = [];
  if (o.speed) pts.push({ tone: "good", text: `Run the line at ${Math.round(o.speed.pct)}% speed until the repair` });
  if (o.repair) {
    pts.push({
      tone: "good",
      text: (
        <>
          Repair {formatDayTime(o.repair.start)} · {durationText(o.repair.hours)}
          {o.repair.technician && (
            <>
              {" "}
              · technician <Mono>{o.repair.technician}</Mono>
            </>
          )}
        </>
      ),
    });
  } else if (o.downtimeHours > 0) {
    pts.push({ tone: "bad", text: `The line stands still for ${durationText(o.downtimeHours)}` });
  }
  if (o.deadline === "met") pts.push({ tone: "good", text: "The order ships on time" });
  if (o.deadline === "missed") pts.push({ tone: "bad", text: "The order will be late" });
  if (o.deadline === "at_risk") pts.push({ tone: "warn", text: "The order could be late" });
  if (o.failureRisk >= 0.5) pts.push({ tone: "bad", text: `${percent(o.failureRisk)} chance the machine breaks down first` });
  else if (o.failureRisk <= 0.05) pts.push({ tone: "good", text: "No more risk of breakdown" });
  if (o.energyKwh < 0)
    pts.push({ tone: "good", text: `Uses ${Math.round(-o.energyKwh)} kWh less electricity · ${Math.abs(o.co2Kg).toFixed(1)} kg less CO2` });
  for (const r of o.risks) pts.push({ tone: "warn", text: r });
  return pts;
}

function PointIcon({ tone }: { tone: Point["tone"] }) {
  if (tone === "good") return <Check className="mt-0.5 h-4 w-4 shrink-0 text-ok-text" aria-label="good" />;
  if (tone === "bad") return <X className="mt-0.5 h-4 w-4 shrink-0 text-crit-text" aria-label="bad" />;
  return <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-warn-text" aria-label="watch out" />;
}

export function CostLine({ option, canSeeCosts }: { option: OptionView; canSeeCosts: boolean }) {
  const hidden = !canSeeCosts || !option.cost;
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-[13px] text-ink-4">{option.costIsExpected ? "Likely cost" : "Total cost"}</span>
      {hidden ? (
        <span className="flex items-baseline gap-2">
          <span className="font-mono text-[32px] font-medium tracking-widest text-ink-5">€ ••••</span>
          <span className="text-[13px] text-ink-5">hidden for your role</span>
        </span>
      ) : (
        <span className={cn("font-mono text-[32px] font-medium", option.recommended && "text-brand-text")}>{money(option.cost)}</span>
      )}
    </div>
  );
}

export function OptionCard({ option, canSeeCosts, onChoose, lockedReason }: { option: OptionView; canSeeCosts: boolean; onChoose: () => void; lockedReason?: string | null }) {
  const d = DEADLINE[option.deadline] ?? DEADLINE.at_risk;
  return (
    <section
      id={`opt-${option.id}`}
      aria-label={`Option ${option.letter}${option.recommended ? ", recommended" : ""}`}
      className={cn(
        "flex flex-col gap-3.5 rounded-[14px] bg-surface p-5",
        option.recommended ? "border-2 border-brand p-[19px] shadow-lift" : "border border-line",
      )}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="flex items-center gap-2">
          <span className={cn("text-[13px] font-semibold", option.recommended ? "text-brand-text" : "text-ink-4")}>Option {option.letter}</span>
          {option.recommended && <Badge tone="solid">Recommended</Badge>}
          {option.origin === "approver" && (
            <Badge tone="brand">
              <Sparkles className="h-3 w-3" aria-hidden /> Your plan{option.suggestedBy ? ` · ${option.suggestedBy}` : ""}
            </Badge>
          )}
        </span>
        <Badge tone={d.tone}>{d.text}</Badge>
      </div>
      <h2 className="m-0 text-[19px] font-semibold">{option.title}</h2>
      <CostLine option={option} canSeeCosts={canSeeCosts} />
      {option.summary && <p className="m-0 text-sm leading-relaxed text-ink-3">{option.summary}</p>}
      <ul className="m-0 flex list-none flex-col gap-2.5 p-0 text-sm">
        {optionPoints(option).map((p, i) => (
          <li key={i} className="flex gap-2">
            <PointIcon tone={p.tone} />
            <span>{p.text}</span>
          </li>
        ))}
      </ul>
      <SignersNeeded steps={option.approvalsNeeded} />
      <div className="flex-1" />
      <Button variant={option.recommended ? "primary" : "outline"} onClick={onChoose} disabled={!!lockedReason} title={lockedReason ?? undefined}>
        Choose {option.letter}
      </Button>
    </section>
  );
}

// ---- Timeline ---------------------------------------------------------------------------

export function OptionsTimeline({ options, dueAt, hoursLeft }: { options: OptionView[]; dueAt: string | null; hoursLeft: number | null }) {
  const start = Date.now();
  const end = dueAt ? new Date(dueAt).getTime() : start + 72 * 3_600_000;
  if (end <= start) return null;
  const pos = (t: number) => `${Math.min(100, Math.max(0, ((t - start) / (end - start)) * 100))}%`;
  const ticks = Array.from({ length: 4 }, (_, i) => start + ((i + 1) / 5) * (end - start));

  return (
    <Card className="flex flex-col gap-3">
      <CardHeader>
        <CardTitle>What each option means until the deadline</CardTitle>
        <div className="flex flex-wrap gap-3.5 text-[13px] text-ink-4">
          <span className="flex items-center gap-1.5"><span className="h-2.5 w-3.5 rounded-sm bg-line-strong" />Running</span>
          <span className="flex items-center gap-1.5"><span className="h-2.5 w-3.5 rounded-sm bg-[#F2C14E]" />Until the repair</span>
          <span className="flex items-center gap-1.5"><span className="h-2.5 w-3.5 rounded-sm bg-brand" />Planned repair</span>
          <span className="flex items-center gap-1.5"><span className="h-2.5 w-3.5 rounded-sm bg-[#E5544B]" />Stopped or likely breakdown</span>
        </div>
      </CardHeader>
      <div className="overflow-x-auto">
        <div className="grid min-w-[560px] grid-cols-[80px_minmax(0,1fr)] items-center gap-x-3 gap-y-2.5">
          <span />
          <div className="relative h-[18px] font-mono text-[11px] text-ink-5">
            <span className="absolute left-0">now</span>
            {ticks.map((t) => (
              <span key={t} className="absolute -translate-x-1/2" style={{ left: pos(t) }}>
                {formatWeekdayTime(t)}
              </span>
            ))}
            <span className="absolute right-0 font-semibold text-ink">due {formatWeekdayTime(end)}</span>
          </div>
          {options.map((o) => (
            <TimelineRow key={o.id} option={o} pos={pos} start={start} end={end} hoursLeft={hoursLeft} />
          ))}
        </div>
      </div>
    </Card>
  );
}

function TimelineRow({ option: o, pos, start, end, hoursLeft }: { option: OptionView; pos: (t: number) => string; start: number; end: number; hoursLeft: number | null }) {
  const endNote =
    o.deadline === "met" ? (
      <span className="font-semibold text-ok-text">done before the deadline</span>
    ) : o.deadline === "missed" ? (
      <span className="font-semibold text-crit-deep">not finished by the deadline</span>
    ) : (
      <span className="font-semibold text-warn-text">might not finish in time</span>
    );
  let blocks: React.ReactNode;
  if (o.repair) {
    const rs = new Date(o.repair.start).getTime();
    const re = rs + o.repair.hours * 3_600_000;
    blocks = (
      <>
        <div className="absolute inset-y-0 left-0 flex items-center whitespace-nowrap bg-[#F2C14E] pl-2 text-xs text-[#4A3000]" style={{ width: pos(rs) }}>
          {o.speed ? `${Math.round(o.speed.pct)}% speed` : "until repair"}
        </div>
        <div className="absolute inset-y-0 bg-brand" style={{ left: pos(rs), width: `calc(${pos(re)} - ${pos(rs)})` }} />
        <div className="absolute inset-y-0 right-0 flex items-center justify-end bg-line-strong pr-2.5 text-xs" style={{ left: pos(re) }}>
          {endNote}
        </div>
      </>
    );
  } else if (o.downtimeHours > 0) {
    const de = start + o.downtimeHours * 3_600_000;
    blocks = (
      <>
        <div className="absolute inset-y-0 left-0 flex items-center whitespace-nowrap bg-[#E5544B] pl-2 text-xs text-white" style={{ width: pos(de) }}>
          stopped {Math.round(o.downtimeHours)} h
        </div>
        <div className="absolute inset-y-0 right-0 flex items-center justify-end bg-line-strong pr-2.5 text-xs" style={{ left: pos(de) }}>
          {endNote}
        </div>
      </>
    );
  } else {
    const fail = hoursLeft !== null && o.failureRisk >= 0.5 ? Math.min(end, start + hoursLeft * 0.5 * 3_600_000) : end;
    blocks = (
      <>
        <div className="absolute inset-y-0 left-0 bg-line-strong" style={{ width: pos(fail) }} />
        {fail < end && (
          <div className="absolute inset-y-0 right-0 flex items-center whitespace-nowrap bg-[#F6C9C4] pl-2.5 text-xs font-semibold text-crit-deep" style={{ left: pos(fail) }}>
            likely breakdown
          </div>
        )}
      </>
    );
  }
  return (
    <>
      <span className={cn("text-sm", o.recommended ? "font-semibold text-brand-text" : "text-ink-4")}>Option {o.letter}</span>
      <div className={cn("relative h-8 overflow-hidden rounded-md bg-muted", o.recommended && "outline outline-2 outline-brand-border")}>{blocks}</div>
    </>
  );
}

// ---- Approval -------------------------------------------------------------------------

export function ApprovalDialog({
  open,
  onOpenChange,
  option,
  view,
  incidentId,
  approverLabel,
  canSeeCosts,
  evidence,
  onDecide,
  busy,
  error,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  option: OptionView | null;
  view: OptionsView;
  incidentId: string;
  approverLabel: string;
  canSeeCosts: boolean;
  evidence: EvidenceView[];
  /** POST /approve with decision approve or reject (ApprovalRequest) */
  onDecide: (decision: "approve" | "reject", reason: string) => void;
  busy: boolean;
  error: unknown;
}) {
  const [reason, setReason] = useState("");
  useEffect(() => {
    if (open) setReason("");
  }, [open, option?.id]);
  if (!option) return null;
  const code = errorCode(error);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <div className="flex flex-col gap-1">
          <span className="text-[13px] text-ink-4">Your decision · {approverLabel}</span>
          <DialogTitle>
            Approve option {option.letter} for issue <Mono>{incidentId}</Mono>?
          </DialogTitle>
        </div>
        <DialogDescription className="sr-only">Check what will happen, then approve and sign.</DialogDescription>

        <div className="flex flex-col gap-2.5 rounded-xl border border-brand-border bg-brand-tint px-4 py-3.5">
          <div className="text-base font-semibold">{option.title}</div>
          {option.summary && <div className="text-sm leading-relaxed text-ink-3">{option.summary}</div>}
          <div className="flex flex-wrap gap-2">
            <span className="rounded-sm border border-[#D6DEE7] bg-surface px-2.5 py-1 font-mono text-sm">
              {canSeeCosts && option.cost ? money(option.cost) : "€ ••••"}
            </span>
            <Badge tone={DEADLINE[option.deadline]?.tone ?? "warn"} className="rounded-sm">
              {DEADLINE[option.deadline]?.text}
            </Badge>
            {option.energyKwh < 0 && (
              <span className="rounded-sm border border-[#D6DEE7] bg-surface px-2.5 py-1 text-[13px] text-ok-text">
                −{Math.round(-option.energyKwh)} kWh · −{Math.abs(option.co2Kg).toFixed(1)} kg CO2
              </span>
            )}
          </div>
        </div>

        {view.needsHumanReview && (
          <div role="status" className="flex items-center gap-2.5 rounded-xl border border-warn-border bg-warn-soft px-3.5 py-3">
            <AlertTriangle className="h-5 w-5 shrink-0 text-warn-text" aria-hidden />
            <span className="text-sm leading-snug text-warn-deep">
              The AI is {percent(view.confidence)} sure about the cause. That’s why it needs your approval.
            </span>
          </div>
        )}

        {evidence.length > 0 && (
          <div className="flex flex-col gap-2">
            <h3 className="m-0 text-sm font-semibold text-ink-2">What the AI based this on</h3>
            <div className="flex flex-wrap gap-2">
              {evidence.map((e) => (
                <EvidenceLink key={e.link} link={e.link} title={e.title} snippet={e.snippet} />
              ))}
            </div>
          </div>
        )}

        <div className="flex flex-col gap-2">
          <h3 className="m-0 text-sm font-semibold text-ink-2">What happens when you approve</h3>
          {option.actions.map((a, i) => (
            <div key={i} className="flex items-start gap-3 rounded-[10px] border border-line bg-subtle px-3.5 py-3">
              {a.kind === "stock_transfer" ? (
                <Truck className="mt-0.5 h-5 w-5 shrink-0 text-brand" aria-hidden />
              ) : a.kind === "purchase_order" ? (
                <ShoppingCart className="mt-0.5 h-5 w-5 shrink-0 text-brand" aria-hidden />
              ) : a.kind === "speed_change" ? (
                <Gauge className="mt-0.5 h-5 w-5 shrink-0 text-brand" aria-hidden />
              ) : (
                <Wrench className="mt-0.5 h-5 w-5 shrink-0 text-brand" aria-hidden />
              )}
              <div className="flex flex-col gap-0.5">
                <span className="text-sm font-semibold">{actionLabel(a.kind)}</span>
                <span className="text-[13px] text-ink-4">{a.description}</span>
              </div>
            </div>
          ))}
        </div>

        <div className="flex flex-col gap-1.5">
          <Label htmlFor="reason">
            Why are you choosing this? <span className="font-normal text-ink-5">(saved with your approval)</span>
          </Label>
          <Textarea id="reason" rows={3} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="For example: signs match the manual, a night repair keeps the order on time." />
        </div>

        {option.approvalsNeeded.length > 1 && (
          <div className="flex flex-col gap-1 rounded-md border border-warn-border bg-warn-soft px-3 py-2.5 text-[13px] text-warn-deep">
            <strong>Your signature starts the approval. Nothing is created until these also sign:</strong>
            {option.approvalsNeeded.slice(1).map((s) => (
              <span key={s.key}>• {s.label} — {s.reason}</span>
            ))}
          </div>
        )}
        <div className="flex items-center gap-2 text-[13px] text-ink-4">
          <Lock className="h-4 w-4 text-ok-text" aria-hidden />
          Your approval is signed with your name, works once, and expires in 10 minutes.
        </div>

        {error ? (
          <p role="alert" className="m-0 text-sm text-crit-text">
            {code === "APPROVAL_REQUIRED" || code === "APPROVAL_INVALID"
              ? "The approval didn't go through. Please approve again."
              : code === "CONFLICT"
                ? "This issue changed while you were deciding. The screen has been refreshed — please check and approve again."
                : "Something went wrong. Please try again."}
          </p>
        ) : null}

        <div className="flex flex-wrap justify-end gap-2.5">
          <Button variant="danger" onClick={() => onDecide("reject", reason)} disabled={busy}>
            Reject
          </Button>
          <Button onClick={() => onDecide("approve", reason)} disabled={busy}>
            {busy ? <Loader2 className="animate-spin" /> : <Check />} Approve and sign
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

export function LockedDialog({ open, onOpenChange, option, incidentId, roleText }: { open: boolean; onOpenChange: (o: boolean) => void; option: OptionView | null; incidentId: string; roleText: string }) {
  const [notified, setNotified] = useState(false);
  if (!option) return null;
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <div className="flex flex-col gap-2">
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone="neutral">{roleText}</Badge>
            <Badge tone="crit">
              <Lock className="h-3.5 w-3.5" aria-hidden /> Not allowed for your role
            </Badge>
          </div>
          <DialogTitle>
            Approve option {option.letter} for issue <Mono>{incidentId}</Mono>?
          </DialogTitle>
        </div>
        <DialogDescription className="sr-only">You can’t approve this option with your role.</DialogDescription>
        <div className="flex flex-col gap-2.5 rounded-xl border border-line bg-subtle px-4 py-3.5">
          <div className="text-base font-semibold">{option.title}</div>
          <div className="flex items-center gap-2">
            <span className="rounded-sm border border-[#D6DEE7] bg-surface px-2.5 py-1 font-mono text-sm tracking-widest text-ink-5">€ ••••</span>
            <span className="text-[13px] text-ink-5">Costs are hidden for your role</span>
          </div>
        </div>
        <div className="flex flex-col items-center gap-3 rounded-xl border border-crit-border bg-[#FFF8F7] p-5 text-center">
          <span className="flex h-14 w-14 items-center justify-center rounded-full border border-crit-border bg-crit-soft">
            <Lock className="h-6 w-6 text-crit-text" aria-hidden />
          </span>
          <div className="text-lg font-semibold">Only a Maintenance Lead or Plant Manager can approve repairs</div>
          <div className="max-w-[440px] text-sm leading-relaxed text-ink-4">Ask a Maintenance Lead or Plant Manager to approve.</div>
        </div>
        <div className="flex flex-wrap items-center justify-between gap-2.5">
          <Button variant="locked" disabled>
            <Lock /> Approve and sign
          </Button>
          <div className="flex flex-wrap gap-2.5">
            <Button variant="outline" onClick={() => onOpenChange(false)}>
              Close
            </Button>
            {/* C1 has no "notify" call, so this copies the link to send to a lead. */}
            <Button
              onClick={() => {
                void navigator.clipboard?.writeText(window.location.href).then(() => setNotified(true));
              }}
            >
              {notified ? "Link copied" : "Copy link for my lead"}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
