"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";
import { ArrowRight } from "lucide-react";
import { AgentGraph, AskPanel, EventFeed } from "@/components/investigation";
import { IncidentGone, LockBadge, Mono, PageTitle, QueryError } from "@/components/common";
import { Badge } from "@/components/ui/badge";
import { buttonVariants } from "@/components/ui/button";
import { backend, errorCode } from "@/lib/backend";
import { formatTime } from "@/lib/format";
import { useIncident } from "@/lib/queries";
import { useIncidentStream } from "@/lib/stream";
import { useSession } from "@/lib/session";
import { cn } from "@/lib/utils";

const SPEEDS = [0.5, 1, 2];

export default function InvestigationPage() {
  const { incidentId } = useParams<{ incidentId: string }>();
  const router = useRouter();
  const { me } = useSession();
  const incident = useIncident(incidentId);
  const [speed, setSpeed] = useState(1);
  const { events, state } = useIncidentStream(incident.data ? incidentId : null, speed);
  const [asking, setAsking] = useState(false);
  const [askError, setAskError] = useState<unknown>(null);

  if (incident.error && !incident.data) return <QueryError error={incident.error} onRetry={() => void incident.refetch()} what="this issue" />;
  if (incident.data === null) return <IncidentGone id={incidentId} />;
  const inc = incident.data;
  const live = state === "live" || state === "connecting" || state === "reconnecting";
  const optionsReady = events.some((e) => e.agent === "decision" && e.kind === "result");

  const ask = async (question: string) => {
    if (!inc) return;
    setAsking(true);
    setAskError(null);
    try {
      const created = await backend.createIncident({ asset_id: inc.asset_id, trigger: "user_question", question });
      router.push(`/investigation/${created.id}`);
    } catch (e) {
      setAskError(e);
    } finally {
      setAsking(false);
    }
  };

  return (
    <>
      <PageTitle
        title={
          <span className="flex flex-wrap items-center gap-3">
            Investigation · <Mono>{incidentId}</Mono>
            {live ? (
              <Badge tone="brand">
                <span className="h-2 w-2 animate-pulse rounded-full bg-brand" /> AI team at work
              </Badge>
            ) : state === "finished" ? (
              <Badge tone="ok">Finished</Badge>
            ) : state === "error" ? (
              <Badge tone="warn">Connection lost</Badge>
            ) : null}
          </span>
        }
        sub={
          inc && (
            <>
              Machine <Mono>{inc.asset_id}</Mono> · Line {inc.line_id} · opened {formatTime(inc.created_at)}
            </>
          )
        }
        right={
          <>
            <div role="group" aria-label="Replay speed" className="flex items-center rounded-md border border-line bg-muted p-[3px]">
              <span className="px-2 text-[13px] text-ink-4">Replay speed</span>
              {SPEEDS.map((s) => (
                <button
                  key={s}
                  type="button"
                  aria-pressed={speed === s}
                  onClick={() => setSpeed(s)}
                  className={cn("min-h-[34px] rounded-[6px] px-2.5 text-[13px]", speed === s ? "bg-surface font-semibold shadow-[0_1px_2px_rgba(16,24,40,0.12)]" : "text-ink-4")}
                >
                  {s}×
                </button>
              ))}
            </div>
            <Link
              href={`/options/${incidentId}`}
              aria-disabled={!optionsReady}
              className={cn(buttonVariants({ variant: optionsReady ? "primary" : "outline" }), "no-underline", optionsReady && "text-white hover:text-white")}
            >
              See the options <ArrowRight />
            </Link>
          </>
        }
      />

      <AgentGraph events={events} />

      <div className="flex flex-wrap items-start gap-4">
        <AskPanel
          question={inc?.title ?? null}
          askedBy={inc ? (inc.trigger === "user_question" ? (me?.name ?? null) : "Opened automatically") : null}
          askedAt={inc?.created_at ?? null}
          events={events}
          onAsk={ask}
          asking={asking}
          askError={
            askError ? (
              errorCode(askError) === "FORBIDDEN" ? (
                <LockBadge />
              ) : (
                <p className="m-0 text-sm text-crit-text">Couldn’t send your question. Try again.</p>
              )
            ) : null
          }
        />
        <EventFeed events={events} incidentId={incidentId} assetId={inc?.asset_id} live={live} />
      </div>
    </>
  );
}
