"use client";

/**
 * "Ask PlantGuard" — always available, from every screen (v1.2).
 *
 * A question is a small incident of its own (trigger user_question). When it is about an open issue,
 * it carries parent_id, so the agents read that issue first (get_incident: diagnosis, approved option,
 * technician notes, machine-check result) before they answer.
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import Link from "next/link";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import { Loader2, MessageCircleQuestion, Send, X } from "lucide-react";
import { answeringAgent, answerView, type EventView } from "@/lib/contract";
import { backend, errorCode } from "@/lib/backend";
import { formatTime } from "@/lib/format";
import { agentName } from "@/lib/labels";
import { useAppState } from "@/lib/app-state";
import { useIncidents, usePlants } from "@/lib/queries";
import { useIncidentStream } from "@/lib/stream";
import { cn } from "@/lib/utils";
import { EvidenceLink, Mono } from "./common";
import { Button } from "./ui/button";

interface OpenArgs {
  incidentId?: string | null;
  assetId?: string | null;
  question?: string;
}

interface AssistantApi {
  open: (a?: OpenArgs) => void;
}

const Ctx = createContext<AssistantApi | null>(null);

export function useAssistant(): AssistantApi {
  return useContext(Ctx) ?? { open: () => undefined };
}

export function AssistantProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<{ open: boolean; args: OpenArgs; key: number }>({ open: false, args: {}, key: 0 });
  const open = useCallback((args: OpenArgs = {}) => setState((s) => ({ open: true, args, key: s.key + 1 })), []);
  const api = useMemo(() => ({ open }), [open]);
  return (
    <Ctx.Provider value={api}>
      {children}
      <DialogPrimitive.Root open={state.open} onOpenChange={(o) => setState((s) => ({ ...s, open: o }))}>
        <DialogPrimitive.Portal>
          <DialogPrimitive.Overlay className="fixed inset-0 z-50 bg-[rgba(22,32,44,0.35)]" />
          <DialogPrimitive.Content
            className="fixed inset-y-0 right-0 z-50 flex w-full max-w-[520px] flex-col border-l border-line bg-surface shadow-pop focus:outline-none"
            aria-describedby={undefined}
          >
            <AssistantPanel key={state.key} initial={state.args} />
          </DialogPrimitive.Content>
        </DialogPrimitive.Portal>
      </DialogPrimitive.Root>
    </Ctx.Provider>
  );
}

const NONE = "";

function AssistantPanel({ initial }: { initial: OpenArgs }) {
  const { openIncidents, plantId } = useAppState();
  const plants = usePlants();
  const machines = useMemo(
    () =>
      (plants.data ?? []).flatMap((p) =>
        p.lines.flatMap((l) => l.stations.flatMap((s) => s.machines.map((m) => ({ id: m.id, name: m.name, plant: p.id, line: l.name })))),
      ),
    [plants.data],
  );

  const [incidentId, setIncidentId] = useState<string>(initial.incidentId ?? NONE);
  const [assetId, setAssetId] = useState<string>(initial.assetId ?? "");
  const [draft, setDraft] = useState(initial.question ?? "");
  const [askedId, setAskedId] = useState<string | null>(null);
  const [asked, setAsked] = useState<{ q: string; at: string } | null>(null);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const ctxIncident = openIncidents.find((o) => o.incident.id === incidentId);
  // a question about an issue is about that issue's machine
  useEffect(() => {
    if (ctxIncident) setAssetId(ctxIncident.incident.asset_id);
  }, [ctxIncident]);
  useEffect(() => {
    if (!assetId && machines.length) setAssetId(machines.find((m) => m.plant === plantId)?.id ?? machines[0]!.id);
  }, [assetId, machines, plantId]);

  const history = useIncidents({ kind: "question", mine: true, scope: "all", parentId: incidentId || undefined }, 8000);

  const send = async () => {
    const q = draft.trim();
    if (!q || !assetId) return;
    setSending(true);
    setError(null);
    try {
      const created = await backend.ask(assetId, q, incidentId || null);
      setAskedId(created.id);
      setAsked({ q, at: created.created_at });
      setDraft("");
      void history.refetch();
    } catch (e) {
      setError(e);
    } finally {
      setSending(false);
    }
  };

  const pastItems = (history.data ?? []).filter((o) => o.incident.id !== askedId).slice(0, 6);

  return (
    <>
      <div className="flex items-center gap-3 border-b border-line px-5 py-4">
        <span className="flex h-9 w-9 items-center justify-center rounded-md border border-brand-border bg-brand-soft">
          <MessageCircleQuestion className="h-[18px] w-[18px] text-brand" aria-hidden />
        </span>
        <div className="flex flex-1 flex-col">
          <DialogPrimitive.Title className="m-0 text-base font-semibold">Ask PlantGuard</DialogPrimitive.Title>
          <span className="text-[13px] text-ink-4">The AI team answers from live plant data, manuals and the issue’s history.</span>
        </div>
        <DialogPrimitive.Close aria-label="Close" className="flex h-10 w-10 items-center justify-center rounded-md border border-line text-ink-3 hover:bg-subtle">
          <X className="h-[18px] w-[18px]" />
        </DialogPrimitive.Close>
      </div>

      <div className="flex flex-col gap-3 border-b border-line bg-subtle px-5 py-3">
        <label className="flex flex-col gap-1 text-[13px] text-ink-4">
          About
          <select
            value={incidentId}
            onChange={(e) => setIncidentId(e.target.value)}
            className="min-h-[38px] rounded-md border border-line bg-surface px-2 text-sm text-ink"
          >
            <option value={NONE}>A machine (no open issue)</option>
            {openIncidents.map((o) => (
              <option key={o.incident.id} value={o.incident.id}>
                {o.incident.id} · {o.asset_name} · {o.incident.plant_id.replace("PLANT-", "Plant ")}
              </option>
            ))}
            {incidentId && !ctxIncident && <option value={incidentId}>{incidentId}</option>}
          </select>
        </label>
        {!incidentId ? (
          <label className="flex flex-col gap-1 text-[13px] text-ink-4">
            Machine
            <select
              value={assetId}
              onChange={(e) => setAssetId(e.target.value)}
              className="min-h-[38px] rounded-md border border-line bg-surface px-2 text-sm text-ink"
            >
              {machines.map((m) => (
                <option key={`${m.plant}-${m.id}`} value={m.id}>
                  {m.id} · {m.name} · {m.plant.replace("PLANT-", "Plant ")} {m.line}
                </option>
              ))}
            </select>
          </label>
        ) : (
          <span className="text-[13px] text-ink-4">
            The agents will read <Mono>{incidentId}</Mono> first: diagnosis, approved option, technician notes and the machine check.
          </span>
        )}
      </div>

      <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-5 py-4">
        {askedId && asked ? (
          <Conversation id={askedId} question={asked.q} at={asked.at} />
        ) : (
          <p className="m-0 text-sm text-ink-4">
            Try: “Is it safe to run the line until the part arrives?”, “Which manual section covers this?”, or “The new bearing is in but
            vibration is still high — what else could it be?”
          </p>
        )}
        {pastItems.length > 0 && (
          <div className="flex flex-col gap-1.5">
            <span className="text-[13px] font-semibold text-ink-4">Earlier questions{incidentId ? ` on ${incidentId}` : ""}</span>
            <ul className="m-0 flex list-none flex-col gap-1 p-0">
              {pastItems.map((o) => (
                <li key={o.incident.id}>
                  <Link
                    href={`/investigation/${o.incident.id}`}
                    className="flex gap-2 rounded-md px-2 py-1.5 text-sm text-ink no-underline hover:bg-subtle"
                  >
                    <Mono className="shrink-0 text-ink-5">{o.incident.id}</Mono>
                    <span className="truncate">{o.incident.question ?? o.incident.title}</span>
                  </Link>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>

      <form
        className="flex flex-col gap-2 border-t border-line px-5 py-4"
        onSubmit={(e) => {
          e.preventDefault();
          void send();
        }}
      >
        {error ? (
          <p className="m-0 text-sm text-crit-text">
            {errorCode(error) === "FORBIDDEN" ? "Your role can’t ask about this machine." : "Couldn’t send your question. Try again."}
          </p>
        ) : null}
        <div className="flex gap-2">
          <label htmlFor="assistant-q" className="sr-only">
            Your question
          </label>
          <textarea
            id="assistant-q"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void send();
              }
            }}
            rows={2}
            placeholder="Ask about this issue or machine…"
            className="min-h-[44px] flex-1 resize-none rounded-md border border-line bg-surface px-3 py-2 text-[15px] focus:border-brand focus:outline-none"
          />
          <Button type="submit" disabled={sending || !draft.trim() || !assetId} aria-label="Send question">
            {sending ? <Loader2 className="animate-spin" /> : <Send />}
          </Button>
        </div>
      </form>
    </>
  );
}

function Conversation({ id, question, at }: { id: string; question: string; at: string }) {
  const { events, state } = useIncidentStream(id, 1, 1);
  const answer = useMemo(() => answerView(events), [events]);
  const by = useMemo(() => answeringAgent(events), [events]);
  const evidence = useEvidence(events);
  const endRef = useRef<HTMLDivElement>(null);
  useEffect(() => endRef.current?.scrollIntoView({ block: "end" }), [answer?.message]);
  return (
    <div className="flex flex-col gap-3">
      <div className="flex justify-end">
        <div className="flex max-w-[88%] flex-col gap-1 rounded-[12px_12px_4px_12px] border border-brand-border bg-brand-soft px-3.5 py-2.5">
          <span className="text-xs text-brand-text">
            You · {formatTime(at)} · <Mono>{id}</Mono>
          </span>
          <span className="text-[15px] leading-snug">{question}</span>
        </div>
      </div>
      <div className="flex flex-col gap-2 rounded-[4px_12px_12px_12px] border border-line bg-subtle p-3.5">
        <span className="text-xs text-ink-5">
          PlantGuard{by.handedOff && ` · ${agentName(by.agent)}`}
          {answer && ` · ${formatTime(answer.ts)}`}
        </span>
        {answer ? (
          <p className="m-0 whitespace-pre-line text-[15px] leading-relaxed">
            {answer.message}
            {answer.live && <span className="ml-0.5 inline-block h-4 w-[2px] animate-pulse bg-ink align-middle" aria-label="still writing" />}
          </p>
        ) : (
          <p className="m-0 flex items-center gap-2 text-[15px] text-ink-4">
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> The AI team is looking into it…
          </p>
        )}
        {evidence.length > 0 && (
          <div className="flex flex-wrap gap-2">
            {evidence.map((ev) => (
              <EvidenceLink key={ev.link} link={ev.link} title={ev.title} snippet={ev.snippet} />
            ))}
          </div>
        )}
        <Link href={`/investigation/${id}`} className={cn("self-start text-[13px]", state === "finished" ? "" : "text-ink-4")}>
          Watch the agents work on it →
        </Link>
      </div>
      <div ref={endRef} />
    </div>
  );
}

function useEvidence(events: EventView[]) {
  return useMemo(() => {
    const seen = new Set<string>();
    return events.flatMap((e) => e.evidence).filter((ev) => (seen.has(ev.link) ? false : (seen.add(ev.link), true)));
  }, [events]);
}
