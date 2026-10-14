"use client";

/**
 * v1.2 approval chain and options chat.
 *  - SignoffPanel: who has signed the option being approved, who still must, and Sign / Decline for the right person.
 *  - SignoffInbox: "Waiting for your signature" (another plant's lead, the plant manager).
 *  - OptionsChat: an approver tells the AI what they would rather do; workable plans come back as new options.
 */
import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { Check, Clock, Loader2, MessageSquareText, PenLine, Send, X } from "lucide-react";
import type { ApprovalStep, Incident, OptionsView, User } from "@/lib/contract";
import { errorCode } from "@/lib/backend";
import { formatDayTime, formatTime } from "@/lib/format";
import { useApprove, useOptionChat, usePostOptionChat, useSignoffs } from "@/lib/queries";
import { cn } from "@/lib/utils";
import { Mono } from "./common";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import { Card, CardHeader, CardTitle } from "./ui/card";
import { Textarea } from "./ui/form";

export function canSign(step: ApprovalStep, me: User | null): boolean {
  return !!me && step.decision === "pending" && step.roles.includes(me.role) && me.plant_ids.includes(step.plant_id);
}

const shortName = (n?: string | null) => (n ?? "").replace(/\s*\(.*\)$/, "");

/** Small list of the signatures an option needs (on each option card). */
export function SignersNeeded({ steps }: { steps: ApprovalStep[] }) {
  if (steps.length <= 1) return null;
  return (
    <div className="flex flex-col gap-1 rounded-md border border-line bg-subtle px-3 py-2 text-[13px]">
      <span className="font-semibold text-ink-3">Needs {steps.length} signatures</span>
      {steps.map((s) => (
        <span key={s.key} className="flex items-start gap-1.5 text-ink-4" title={s.reason}>
          <PenLine className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
          <span>
            {s.label}
            {s.key !== "requesting_plant" && <span className="text-ink-5"> · {s.reason}</span>}
          </span>
        </span>
      ))}
    </div>
  );
}

function StepRow({ s }: { s: ApprovalStep }) {
  const icon =
    s.decision === "approved" ? (
      <span className="flex h-6 w-6 items-center justify-center rounded-full bg-ok text-white"><Check className="h-3.5 w-3.5" /></span>
    ) : s.decision === "declined" ? (
      <span className="flex h-6 w-6 items-center justify-center rounded-full bg-crit text-white"><X className="h-3.5 w-3.5" /></span>
    ) : (
      <span className="flex h-6 w-6 items-center justify-center rounded-full border border-warn-border bg-warn-soft text-warn-text"><Clock className="h-3.5 w-3.5" /></span>
    );
  return (
    <li className="flex items-start gap-3">
      {icon}
      <span className="flex min-w-0 flex-1 flex-col">
        <span className="text-sm font-semibold">{s.label}</span>
        <span className="text-[13px] text-ink-4">
          {s.decision === "pending" ? (
            <>Waiting · {s.reason}</>
          ) : (
            <>
              {s.decision === "approved" ? "Signed" : "Declined"} by {shortName(s.signed_name)}
              {s.signed_at && ` · ${formatTime(s.signed_at)}`}
              {s.note && <> · “{s.note}”</>}
            </>
          )}
        </span>
      </span>
    </li>
  );
}

/** The option being signed (or the last one declined), with Sign / Decline for whoever is next. */
export function SignoffPanel({ incident, view, me, onFinal }: { incident: Incident; view: OptionsView | undefined; me: User | null; onFinal: () => void }) {
  const so = incident.signoff;
  const approve = useApprove(incident.id);
  const [reason, setReason] = useState("");
  const [mode, setMode] = useState<"sign" | "decline" | null>(null);
  if (!so) return null;
  const option = view?.options.find((o) => o.id === so.option_id);
  const letter = so.option_id.replace("OPT-", "");
  const mine = so.status === "in_progress" ? so.steps.filter((s) => canSign(s, me)) : [];
  const pending = so.steps.filter((s) => s.decision === "pending");
  const declined = so.status === "declined";
  if (declined && incident.status !== "awaiting_approval") return null;

  const submit = (decision: "approve" | "reject") =>
    approve.mutate(
      { optionId: so.option_id, decision, reason: reason.trim() },
      {
        onSuccess: (a) => {
          setMode(null);
          setReason("");
          if (a.approval_token) onFinal();
        },
      },
    );

  return (
    <Card className={cn("flex flex-col gap-3.5 border-2", declined ? "border-crit-border" : "border-warn-border")}>
      <CardHeader>
        <CardTitle>
          {declined ? `Option ${letter} was declined` : `Option ${letter} is being signed off`}
          {option && <span className="font-normal text-ink-4"> · {option.title}</span>}
        </CardTitle>
        <Badge tone={declined ? "crit" : "warn"}>{declined ? "Choose another option" : `${pending.length} signature${pending.length === 1 ? "" : "s"} to go`}</Badge>
      </CardHeader>
      <p className="m-0 text-sm text-ink-3">
        {declined
          ? "Nothing was created. Pick another option below, or ask PlantGuard for a different plan in the chat."
          : "Nothing is created until everyone below has signed. Any of them can decline with a reason."}
      </p>
      <ol className="m-0 flex list-none flex-col gap-3 p-0">
        {so.steps.map((s) => (
          <StepRow key={s.key} s={s} />
        ))}
      </ol>
      {mine.length > 0 && (
        <div className="flex flex-col gap-2.5 rounded-lg border border-brand-border bg-brand-tint p-3">
          <span className="text-sm font-semibold text-brand-text">
            Your signature is needed: {mine.map((s) => s.label).join(" and ")}
          </span>
          {mode ? (
            <>
              <label htmlFor="signoff-reason" className="text-[13px] text-ink-3">
                {mode === "decline" ? "Why are you declining? (the requester sees this)" : "Note (optional)"}
              </label>
              <Textarea id="signoff-reason" rows={2} value={reason} onChange={(e) => setReason(e.target.value)}
                        placeholder={mode === "decline" ? "e.g. we need that part for our own machine this week" : ""} />
              <div className="flex flex-wrap gap-2">
                {mode === "sign" ? (
                  <Button onClick={() => submit("approve")} disabled={approve.isPending}>
                    {approve.isPending ? <Loader2 className="animate-spin" /> : <Check />} Sign option {letter}
                  </Button>
                ) : (
                  <Button variant="danger" onClick={() => submit("reject")} disabled={approve.isPending || reason.trim().length < 3}>
                    {approve.isPending ? <Loader2 className="animate-spin" /> : <X />} Decline option {letter}
                  </Button>
                )}
                <Button variant="outline" onClick={() => setMode(null)}>Cancel</Button>
              </div>
            </>
          ) : (
            <div className="flex flex-wrap gap-2">
              <Button onClick={() => setMode("sign")}><Check /> Sign</Button>
              <Button variant="outline" onClick={() => setMode("decline")}><X /> Decline</Button>
            </div>
          )}
          {approve.error ? (
            <p className="m-0 text-sm text-crit-text">
              {errorCode(approve.error) === "FORBIDDEN" ? "This step isn't yours to sign." : "That didn't work. Try again."}
            </p>
          ) : null}
        </div>
      )}
    </Card>
  );
}

/** "Waiting for your signature" — on the overview and the issues page. */
export function SignoffInbox() {
  const tasks = useSignoffs();
  const items = tasks.data ?? [];
  if (!items.length) return null;
  return (
    <Card className="flex flex-col gap-2 border-2 border-warn-border">
      <CardHeader>
        <CardTitle>Waiting for your signature</CardTitle>
        <Badge tone="warn">{items.length}</Badge>
      </CardHeader>
      <ul className="m-0 flex list-none flex-col divide-y divide-line-soft p-0">
        {items.map((t) => (
          <li key={`${t.incident_id}-${t.step.key}`}>
            <Link href={`/options/${t.incident_id}`} className="flex flex-wrap items-center gap-x-4 gap-y-1 py-2.5 text-ink no-underline hover:bg-subtle">
              <Mono className="w-[86px] text-[15px] font-medium">{t.incident_id}</Mono>
              <span className="flex min-w-[220px] flex-1 flex-col">
                <span className="text-sm font-semibold">{t.step.label}: option {t.option_id.replace("OPT-", "")} · {t.option_label}</span>
                <span className="text-xs text-ink-5">
                  {t.asset_id} · {t.plant_id.replace("PLANT-", "Plant ")} · asked by {shortName(t.requested_by)} {formatDayTime(t.requested_at)} · {t.step.reason}
                </span>
              </span>
              <span className="text-sm font-semibold text-brand-text">Review →</span>
            </Link>
          </li>
        ))}
      </ul>
    </Card>
  );
}

/** Approvers discuss the options with PlantGuard. Each workable suggestion becomes a new option. */
export function OptionsChat({ incidentId, canChat, locked }: { incidentId: string; canChat: boolean; locked: string | null }) {
  const chat = useOptionChat(incidentId);
  const post = usePostOptionChat(incidentId);
  const [draft, setDraft] = useState("");
  const endRef = useRef<HTMLDivElement>(null);
  const msgs = chat.data ?? [];
  useEffect(() => endRef.current?.scrollIntoView({ block: "nearest" }), [msgs.length, post.isPending]);
  if (!canChat && !msgs.length) return null;
  const send = () => {
    const m = draft.trim();
    if (m.length < 2) return;
    post.mutate(m, { onSuccess: () => setDraft("") });
  };
  return (
    <Card className="flex flex-col gap-3">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <MessageSquareText className="h-[18px] w-[18px] text-brand" aria-hidden /> Not quite right? Tell PlantGuard what you’d rather do
        </CardTitle>
      </CardHeader>
      <p className="m-0 text-sm text-ink-4">
        Every workable suggestion becomes a new option with real costs, deadline and risk, worked out from live plant data. The AI’s own
        options stay as they are. Keep refining as long as you like, then approve the one you want.
      </p>
      {msgs.length > 0 && (
        <ol className="m-0 flex max-h-[360px] list-none flex-col gap-2.5 overflow-y-auto p-0">
          {msgs.map((m) => (
            <li key={m.seq} className={cn("flex", m.role === "user" ? "justify-end" : "justify-start")}>
              <div
                className={cn(
                  "flex max-w-[85%] flex-col gap-1 px-3.5 py-2.5",
                  m.role === "user" ? "rounded-[12px_12px_4px_12px] border border-brand-border bg-brand-soft" : "rounded-[4px_12px_12px_12px] border border-line bg-subtle",
                )}
              >
                <span className="text-xs text-ink-5">
                  {m.role === "user" ? shortName(m.author_name) || "Approver" : "PlantGuard · Decision"} · {formatTime(m.ts)}
                  {m.option_id && <> · <a href={`#opt-${m.option_id}`}>option {m.option_id.replace("OPT-", "")}</a></>}
                </span>
                <span className="text-[15px] leading-snug">{m.text}</span>
              </div>
            </li>
          ))}
          {post.isPending && (
            <li className="flex items-center gap-2 text-sm text-ink-4">
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> Working out the numbers…
            </li>
          )}
          <div ref={endRef} />
        </ol>
      )}
      {canChat &&
        (locked ? (
          <p className="m-0 rounded-md border border-line bg-subtle px-3 py-2 text-sm text-ink-4">{locked}</p>
        ) : (
          <form
            className="flex gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              send();
            }}
          >
            <label htmlFor="opt-chat" className="sr-only">Your suggestion</label>
            <textarea
              id="opt-chat"
              rows={2}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  send();
                }
              }}
              placeholder="e.g. “Repair tomorrow morning at 80% speed”, “buy it from the supplier instead”, “let Meera do it”"
              className="min-h-[48px] flex-1 resize-none rounded-md border border-line bg-surface px-3 py-2 text-[15px] focus:border-brand focus:outline-none"
            />
            <Button type="submit" disabled={post.isPending || draft.trim().length < 2} aria-label="Send suggestion">
              {post.isPending ? <Loader2 className="animate-spin" /> : <Send />}
            </Button>
          </form>
        ))}
      {post.error ? (
        <p className="m-0 text-sm text-crit-text">
          {errorCode(post.error) === "CONFLICT" ? "The options can't change right now (they are being signed off or already approved)." : "Couldn't send that. Try again."}
        </p>
      ) : null}
    </Card>
  );
}
