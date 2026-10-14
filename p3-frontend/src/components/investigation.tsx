"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { AnimatePresence, motion } from "framer-motion";
import { ArrowUp, Loader2, Maximize2, RotateCw, Send } from "lucide-react";
import {
  answeringAgent,
  answerView,
  diagnosisConfidence,
  foldStreams,
  isRunFinished,
  resultNeedsAttention,
  resultSummary,
  type EventView,
} from "@/lib/contract";
import { formatTime, formatTimeSeconds } from "@/lib/format";
import { AGENT_ORDER, AGENTS, agentName, EVENT_KIND_LABEL, REVIEW_THRESHOLD, subAgentName, toolLabel, type AgentKey } from "@/lib/labels";
import { cn } from "@/lib/utils";
import { Card, CardHeader, CardTitle } from "./ui/card";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "./ui/dialog";
import { EvidenceLink, Mono } from "./common";

// ---- Agent graph ----------------------------------------------------------------------
//
// Plain HTML + one SVG arrow, laid out by CSS grid. (It used React Flow before: React Flow hides nodes until it has
// measured them, and a new nodes array on every streamed token made the whole graph blink out while agents worked.)

type NodeState = "waiting" | "running" | "done" | "attention";

const STATE_STYLE: Record<NodeState, { box: string; dot: string; note: string }> = {
  waiting: { box: "border-line bg-subtle", dot: "border-[1.5px] border-[#9AA8BA] bg-transparent", note: "text-ink-5" },
  running: { box: "border-[1.5px] border-brand bg-brand-soft shadow-[0_0_0_4px_rgba(29,95,209,0.12)]", dot: "bg-brand animate-pulse", note: "text-brand-text" },
  done: { box: "border-ok-border bg-surface", dot: "bg-ok", note: "text-ok-text" },
  attention: { box: "border-warn-border bg-warn-soft", dot: "bg-warn", note: "text-warn-text" },
};

export function agentStates(events: EventView[]): Record<string, { state: NodeState; note: string }> {
  const out: Record<string, { state: NodeState; note: string }> = {};
  for (const a of AGENT_ORDER) out[a] = { state: "waiting", note: "waiting" };
  for (const e of events) {
    const cur = out[e.agent];
    if (!cur || e.subAgent) continue; // a helper sub-agent works inside its principal's node
    if (e.kind === "status" && e.status === "started") out[e.agent] = { state: "running", note: "working…" };
    if (e.kind === "tool_call" && cur.state === "running" && e.tool) out[e.agent] = { state: "running", note: toolLabel(e.tool) };
    if (e.kind === "result") out[e.agent] = { state: resultNeedsAttention(e) ? "attention" : "done", note: resultSummary(e) };
    if (e.kind === "status" && e.status === "finished" && cur.state === "running") out[e.agent] = { state: "done", note: "done" };
  }
  if (events.some(isRunFinished)) out.orchestrator = { state: "done", note: "options ready" };
  else if (out.orchestrator!.state === "running") out.orchestrator!.note = "running the team";
  return out;
}

export function AgentGraph({ events }: { events: EventView[] }) {
  const states = useMemo(() => agentStates(events), [events]);
  const n = AGENT_ORDER.length;
  return (
    <Card className="flex flex-col gap-3 py-4">
      <CardHeader>
        <CardTitle>The AI team</CardTitle>
        <div className="flex flex-wrap gap-3.5 text-[13px] text-ink-4">
          <span className="flex items-center gap-1.5"><span className="h-[9px] w-[9px] rounded-full bg-ok" />Done</span>
          <span className="flex items-center gap-1.5"><span className="h-[9px] w-[9px] rounded-full bg-warn" />Found a problem</span>
          <span className="flex items-center gap-1.5"><span className="h-[9px] w-[9px] rounded-full bg-brand" />Working</span>
        </div>
      </CardHeader>
      <div className="overflow-x-auto pb-1">
        <div
          className="relative grid min-w-[980px] gap-x-[18px] pb-9"
          style={{ gridTemplateColumns: `repeat(${n}, minmax(0, 1fr))` }}
          role="img"
          aria-label={`Agent graph: ${AGENT_ORDER.map((a) => `${AGENTS[a].name} ${states[a]!.state}`).join(", ")}`}
        >
          {AGENT_ORDER.map((agent, i) => {
            const st = states[agent]!;
            const s = STATE_STYLE[st.state];
            const nextRunning = i < n - 1 && states[AGENT_ORDER[i + 1]!]!.state === "running";
            return (
              <div key={agent} className="relative">
                <div className={cn("flex h-full flex-col gap-0.5 rounded-[10px] border px-3 py-2.5 transition-colors duration-300", s.box)}>
                  <span className="flex items-center gap-1.5 text-sm font-semibold text-ink">
                    <span className={cn("h-[9px] w-[9px] shrink-0 rounded-full", s.dot)} />
                    {AGENTS[agent].name}
                  </span>
                  <span className="text-xs text-ink-4">{AGENTS[agent].job}</span>
                  <span className={cn("truncate text-xs", s.note)} title={st.note}>
                    {st.note}
                  </span>
                </div>
                {i < n - 1 && (
                  <svg className="absolute -right-[18px] top-1/2 h-3 w-[18px] -translate-y-1/2" viewBox="0 0 18 12" aria-hidden>
                    <line x1="1" y1="6" x2="13" y2="6" stroke={nextRunning ? "#1D5FD1" : "#9AA8BA"} strokeWidth="1.6" strokeDasharray={nextRunning ? "3 3" : undefined}>
                      {nextRunning && <animate attributeName="stroke-dashoffset" from="12" to="0" dur="0.8s" repeatCount="indefinite" />}
                    </line>
                    <path d="M12 2 L17 6 L12 10" fill="none" stroke={nextRunning ? "#1D5FD1" : "#9AA8BA"} strokeWidth="1.6" />
                  </svg>
                )}
              </div>
            );
          })}
          {/* decision → back to the coordinator */}
          <svg className="pointer-events-none absolute inset-x-0 bottom-0 h-9 w-full" preserveAspectRatio="none" viewBox="0 0 1000 36" aria-hidden>
            <path
              d={`M ${1000 - 1000 / (2 * n)} 0 V 22 H ${1000 / (2 * n)} V 2`}
              fill="none"
              stroke="#1D5FD1"
              strokeWidth="1.6"
              strokeDasharray="5 5"
              vectorEffect="non-scaling-stroke"
            >
              {states.decision!.state === "running" && <animate attributeName="stroke-dashoffset" from="20" to="0" dur="1s" repeatCount="indefinite" />}
            </path>
          </svg>
          <span className="absolute bottom-[6px] left-1/2 -translate-x-1/2 bg-surface px-2 text-xs text-brand-text">brings the options back</span>
        </div>
      </div>
    </Card>
  );
}

// ---- Event feed -----------------------------------------------------------------------

const FILTERS = [
  { key: "all", label: "All" },
  { key: "thought", label: "Thinking" },
  { key: "tool_call", label: "Checking data" },
  { key: "evidence", label: "Evidence" },
  { key: "result", label: "Findings" },
] as const;

const KIND_STYLE: Record<string, { card: string; chip: string }> = {
  thought: { card: "border-brand-border bg-brand-soft", chip: "border border-brand-border bg-surface text-brand-text" },
  thought_delta: { card: "border-brand-border bg-brand-soft", chip: "border border-brand-border bg-surface text-brand-text" },
  message: { card: "border-line bg-subtle", chip: "bg-ink text-white" },
  message_delta: { card: "border-line bg-subtle", chip: "bg-ink text-white" },
  handoff: { card: "border-brand-border bg-surface", chip: "bg-brand text-white" },
  tool_call: { card: "border-line bg-surface", chip: "bg-line-soft text-ink-3" },
  tool_result: { card: "border-line bg-surface", chip: "bg-line-soft text-ink-3" },
  evidence: { card: "border-brand-border bg-surface", chip: "bg-brand-soft text-brand-text" },
  result: { card: "border-ok-border bg-surface", chip: "bg-ok-soft text-ok-text" },
  error: { card: "border-crit-border bg-crit-soft", chip: "border border-crit-border bg-surface text-crit-text" },
};

/** STATUS events are normally only node on/off signals; anything else (e.g. "trying again in 8 s") is a notice. */
export const isNotice = (e: EventView) => e.kind === "status" && !/^(started|finished)$/i.test(e.message.trim());

type FilterKey = (typeof FILTERS)[number]["key"];

/** Folded, filtered and newest-first: the latest update is always at the top. */
function useFeed(events: EventView[], filter: FilterKey) {
  // Live token streams are joined into one card per stream (v1.1); old recordings pass through unchanged.
  const folded = useMemo(() => foldStreams(events), [events]);
  return useMemo(() => {
    // C2 rendering rules: STATUS only lights nodes (notices excepted); TOOL_RESULT is folded into its TOOL_CALL.
    const visible = folded.filter(
      (e) =>
        (e.kind !== "status" || isNotice(e)) &&
        e.kind !== "tool_result" &&
        (filter === "all" || e.kind === filter || e.kind === `${filter}_delta`),
    );
    return visible.reverse();
  }, [folded, filter]);
}

function FilterChips({ filter, setFilter }: { filter: FilterKey; setFilter: (f: FilterKey) => void }) {
  return (
    <div role="group" aria-label="Filter" className="flex flex-wrap gap-1.5 text-xs">
      {FILTERS.map((f) => (
        <button
          key={f.key}
          type="button"
          aria-pressed={filter === f.key}
          onClick={() => setFilter(f.key)}
          className={cn(
            "min-h-[30px] rounded-full px-2.5",
            filter === f.key ? "bg-ink font-semibold text-white" : "border border-[#D6DEE7] text-ink-3 hover:bg-subtle",
          )}
        >
          {f.label}
        </button>
      ))}
    </div>
  );
}

function LiveBadge({ live, count }: { live: boolean; count: number }) {
  return (
    <span className="flex items-center gap-1.5 text-xs text-ink-4">
      {live ? (
        <>
          <span className="relative flex h-2 w-2">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-brand opacity-60" />
            <span className="relative inline-flex h-2 w-2 rounded-full bg-brand" />
          </span>
          <span className="font-semibold text-brand-text">Live</span>
        </>
      ) : null}
      <span>
        {count} update{count === 1 ? "" : "s"}
      </span>
    </span>
  );
}

/**
 * The scrolling list. Newest on top; the box scrolls by itself so the page never grows. If the reader has scrolled
 * down to older updates, new ones don't yank the view: a pill offers to jump back to the latest.
 */
function FeedList({
  items,
  incidentId,
  assetId,
  live,
  className,
}: {
  items: EventView[];
  incidentId: string;
  assetId?: string;
  live: boolean;
  className?: string;
}) {
  const box = useRef<HTMLOListElement>(null);
  const [away, setAway] = useState(false);
  const [seen, setSeen] = useState(items.length);
  const unseen = away ? Math.max(0, items.length - seen) : 0;

  useEffect(() => {
    if (!away) setSeen(items.length);
  }, [items.length, away]);

  const onScroll = () => {
    const isAway = (box.current?.scrollTop ?? 0) > 24;
    if (isAway !== away) {
      setAway(isAway);
      if (isAway) setSeen(items.length);
    }
  };
  const toTop = () => box.current?.scrollTo({ top: 0, behavior: "smooth" });

  return (
    <div className="relative flex min-h-0 flex-col">
      {unseen > 0 && (
        <button
          type="button"
          onClick={toTop}
          className="absolute left-1/2 top-2 z-10 flex -translate-x-1/2 items-center gap-1 rounded-full bg-ink px-3 py-1 text-xs font-semibold text-white shadow-pop"
        >
          <ArrowUp className="h-3.5 w-3.5" /> {unseen} new update{unseen === 1 ? "" : "s"}
        </button>
      )}
      <ol
        ref={box}
        onScroll={onScroll}
        aria-live={live ? "polite" : "off"}
        aria-label="AI team updates, newest first"
        className={cn("m-0 flex list-none flex-col gap-2.5 overflow-y-auto overscroll-contain p-0 pr-1", className)}
      >
        <AnimatePresence initial={false}>
          {items.map((e, i) => (
            <motion.li
              key={e.streamId ?? e.seq}
              layout="position"
              initial={{ opacity: 0, y: -8 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.25 }}
            >
              <EventCard e={e} incidentId={incidentId} assetId={assetId} streaming={live && !!e.live} latest={live && i === 0} />
            </motion.li>
          ))}
        </AnimatePresence>
      </ol>
    </div>
  );
}

export function EventFeed({ events, incidentId, assetId, live }: { events: EventView[]; incidentId: string; assetId?: string; live: boolean }) {
  const [filter, setFilter] = useState<FilterKey>("all");
  const [expanded, setExpanded] = useState(false);
  const items = useFeed(events, filter);
  const empty = <p className="m-0 py-6 text-center text-sm text-ink-4">{live ? "Waiting for the AI team…" : "Nothing here yet."}</p>;

  return (
    <Card className="flex min-w-0 flex-[1.2_1_420px] flex-col gap-2.5">
      <CardHeader>
        <div className="flex items-center gap-3">
          <CardTitle>What the AI team is doing</CardTitle>
          <LiveBadge live={live} count={items.length} />
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <FilterChips filter={filter} setFilter={setFilter} />
          <button
            type="button"
            onClick={() => setExpanded(true)}
            aria-label="Open the updates full screen"
            title="Open full screen"
            className="flex h-[30px] w-[30px] items-center justify-center rounded-md border border-[#D6DEE7] text-ink-3 hover:bg-subtle"
          >
            <Maximize2 className="h-3.5 w-3.5" />
          </button>
        </div>
      </CardHeader>
      {items.length === 0 ? empty : <FeedList items={items} incidentId={incidentId} assetId={assetId} live={live} className="max-h-[min(62vh,560px)]" />}

      <Dialog open={expanded} onOpenChange={setExpanded}>
        <DialogContent className="top-[3vh] h-[94vh] max-h-[94vh] max-w-[1180px] gap-3 overflow-hidden p-5 sm:p-6">
          <div className="flex flex-wrap items-center gap-3 pr-14">
            <DialogTitle className="text-xl">What the AI team is doing</DialogTitle>
            <LiveBadge live={live} count={items.length} />
          </div>
          <DialogDescription>Newest first. Updates appear here as they happen.</DialogDescription>
          <TeamStrip events={events} />
          <FilterChips filter={filter} setFilter={setFilter} />
          <div className="flex min-h-0 flex-1 flex-col">
            {items.length === 0 ? empty : <FeedList items={items} incidentId={incidentId} assetId={assetId} live={live} className="h-full" />}
          </div>
        </DialogContent>
      </Dialog>
    </Card>
  );
}

/** Compact who-is-doing-what row for the full-screen view (same states as the graph). */
function TeamStrip({ events }: { events: EventView[] }) {
  const states = agentStates(events);
  return (
    <div className="flex flex-wrap gap-1.5" aria-label="Agent status">
      {AGENT_ORDER.map((a) => {
        const st = states[a]!;
        const s = STATE_STYLE[st.state];
        return (
          <span key={a} className={cn("flex max-w-[220px] items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs", s.box)} title={st.note}>
            <span className={cn("h-2 w-2 shrink-0 rounded-full", s.dot)} />
            <span className="font-semibold text-ink">{AGENTS[a].name}</span>
            <span className={cn("truncate", s.note)}>{st.note}</span>
          </span>
        );
      })}
    </div>
  );
}

function EventCard({
  e,
  incidentId,
  assetId,
  streaming,
  latest = false,
}: {
  e: EventView;
  incidentId: string;
  assetId?: string;
  streaming: boolean;
  latest?: boolean;
}) {
  const attention = resultNeedsAttention(e) || isNotice(e);
  const style = attention ? { card: "border-warn-border bg-warn-soft", chip: "border border-warn-border bg-surface text-warn-text" } : KIND_STYLE[e.kind] ?? KIND_STYLE.tool_call!;
  const label = isNotice(e) ? (/wait|again/i.test(e.message) ? "Waiting" : "Update") : EVENT_KIND_LABEL[e.kind] ?? e.kind;
  const head = (
    <div className="flex items-center gap-2 text-xs text-ink-5">
      <span className={cn("rounded-full px-2 py-0.5 font-semibold", style.chip)}>{label}</span>
      <span className="font-semibold text-ink">
        {agentName(e.agent)}
        {e.subAgent && <span className="font-normal text-ink-4"> › {subAgentName(e.subAgent)}</span>}
      </span>
      {latest && <span className="rounded-full bg-brand px-1.5 py-px text-[10px] font-semibold uppercase tracking-wide text-white">Latest</span>}
      <span className="ml-auto font-mono">{formatTimeSeconds(e.ts)}</span>
    </div>
  );

  // Every AI claim is clickable (README): results link to the screen they talk about.
  const resultHref =
    e.agent === "decision" ? `/options/${incidentId}` : e.agent === "monitor" && assetId ? `/machines/${assetId}` : null;

  return (
    <div className={cn("flex flex-col gap-1.5 rounded-[10px] border px-3 py-2.5", style.card)}>
      {head}
      {(e.kind === "thought" || e.kind === "thought_delta") && (
        <div className="flex items-center gap-2 text-sm text-ink-2">
          <span className={cn(e.kind === "thought_delta" && "line-clamp-3")}>{e.message}</span>
          {streaming && <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-brand" aria-label="still thinking" />}
        </div>
      )}
      {(e.kind === "message" || e.kind === "message_delta") && (
        <p className="m-0 whitespace-pre-line text-sm leading-relaxed text-ink">
          {e.message}
          {streaming && <span className="ml-0.5 inline-block h-3.5 w-[2px] animate-pulse bg-ink align-middle" aria-label="still writing" />}
        </p>
      )}
      {e.kind === "handoff" && (
        <div className="text-sm">
          Handed to <strong>{agentName(String(e.args?.to ?? ""))}</strong>
          {typeof e.args?.task === "string" && <span className="text-ink-2"> · {e.args.task}</span>}
        </div>
      )}
      {e.kind === "tool_call" && (
        <div className="text-sm">
          {toolLabel(e.tool ?? "")} {e.tool && <Mono className="text-xs text-ink-5">{e.tool}</Mono>}
        </div>
      )}
      {(e.kind === "evidence" || e.kind === "error" || isNotice(e)) && e.message && e.evidence.length === 0 && (
        <div className="flex items-center gap-2 text-sm">
          {isNotice(e) && latest && <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-warn" aria-hidden />}
          {e.message}
        </div>
      )}
      {e.kind === "evidence" &&
        e.evidence.map((ev) => (
          <EvidenceLink key={ev.link} link={ev.link} title={ev.title} snippet={ev.snippet} className="w-fit rounded-md border-0 bg-transparent p-0 text-left text-sm hover:bg-transparent">
            <span>
              <span className="font-medium text-brand-text underline-offset-2 hover:underline">{ev.title}</span>
              {ev.snippet && <span className="text-ink-2"> · {ev.snippet}</span>}
            </span>
          </EvidenceLink>
        ))}
      {e.kind === "result" &&
        (resultHref ? (
          <Link href={resultHref} className="text-sm text-ink no-underline hover:text-brand-text">
            {resultSummary(e)}
          </Link>
        ) : (
          <div className="text-sm">{resultSummary(e)}</div>
        ))}
    </div>
  );
}

// ---- Ask panel ------------------------------------------------------------------------

export function AskPanel({
  question,
  askedBy,
  askedAt,
  events,
  onAsk,
  asking,
  askError,
}: {
  question: string | null;
  askedBy: string | null;
  askedAt: string | null;
  events: EventView[];
  onAsk: (q: string) => void;
  asking: boolean;
  askError: React.ReactNode;
}) {
  const [draft, setDraft] = useState("");
  // The coordinator answers, unless it handed the question to a teammate (v1.1): then that agent's answer shows.
  const answer = useMemo(() => answerView(events), [events]);
  const answeredBy = useMemo(() => answeringAgent(events), [events]);
  const evidence = useMemo(() => {
    const seen = new Set<string>();
    return events
      .flatMap((e) => e.evidence)
      .filter((ev) => (seen.has(ev.link) ? false : (seen.add(ev.link), true)));
  }, [events]);
  const confidence = diagnosisConfidence(events);
  const dash = confidence !== null ? 106.8 * confidence : 0;

  return (
    <Card className="flex min-w-0 flex-[1_1_380px] flex-col gap-4">
      <CardTitle>Ask PlantGuard</CardTitle>
      {question && (
        <div className="flex justify-end">
          <div className="flex max-w-[85%] flex-col gap-1.5 rounded-[12px_12px_4px_12px] border border-brand-border bg-brand-soft px-3.5 py-3">
            <span className="text-xs text-brand-text">
              {askedBy ?? "You"}
              {askedAt && ` · ${formatTime(askedAt)}`}
            </span>
            <span className="text-[15px] leading-snug">{question}</span>
          </div>
        </div>
      )}
      <div className="flex items-start gap-2.5">
        <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md border border-brand-border bg-brand-soft">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#1D5FD1" strokeWidth="1.8" strokeLinejoin="round" aria-hidden>
            <path d="M12 3l7 3v5c0 4.5-3 8.2-7 10-4-1.8-7-5.5-7-10V6l7-3z" />
          </svg>
        </span>
        <div className="flex min-w-0 flex-1 flex-col gap-3 rounded-[4px_12px_12px_12px] border border-line bg-subtle p-3.5">
          <span className="text-xs text-ink-5">
            PlantGuard{answeredBy.handedOff && ` · ${agentName(answeredBy.agent)}`}
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
          {confidence !== null && (
            <div
              className={cn(
                "flex items-center gap-3 rounded-md border px-3 py-2.5",
                confidence < REVIEW_THRESHOLD ? "border-warn-border bg-warn-soft" : "border-ok-border bg-ok-soft",
              )}
            >
              <svg width="44" height="44" viewBox="0 0 44 44" role="img" aria-label={`${Math.round(confidence * 100)}% sure`} className="shrink-0">
                <circle cx="22" cy="22" r="17" fill="none" stroke={confidence < REVIEW_THRESHOLD ? "#F6E3B4" : "#CDEBD9"} strokeWidth="5" />
                <circle cx="22" cy="22" r="17" fill="none" stroke={confidence < REVIEW_THRESHOLD ? "#E0A100" : "#1F9D55"} strokeWidth="5" strokeLinecap="round" strokeDasharray={`${dash} 106.8`} transform="rotate(-90 22 22)" />
                <text x="22" y="26" textAnchor="middle" fill={confidence < REVIEW_THRESHOLD ? "#8A5300" : "#146C3A"} fontSize="11" fontWeight="600" fontFamily="IBM Plex Mono, monospace">
                  {Math.round(confidence * 100)}%
                </text>
              </svg>
              {confidence < REVIEW_THRESHOLD ? (
                <span className="text-sm leading-snug text-warn-deep">
                  <strong>{Math.round(confidence * 100)}% sure.</strong> That’s below 70%, so a person must check before anything happens.
                </span>
              ) : (
                <span className="text-sm leading-snug text-ok-deep">
                  <strong>{Math.round(confidence * 100)}% sure.</strong> A person still approves any repair.
                </span>
              )}
            </div>
          )}
        </div>
      </div>
      <form
        className="flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (draft.trim()) onAsk(draft.trim());
        }}
      >
        <label htmlFor="follow-up" className="sr-only">
          Ask a follow-up question
        </label>
        <input
          id="follow-up"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="Ask a follow-up question…"
          className="h-11 min-w-0 flex-1 rounded-md border border-line-strong bg-surface px-3 text-sm focus:border-brand focus:outline-none"
        />
        <button type="submit" aria-label="Send" disabled={asking} className="flex h-11 w-11 items-center justify-center rounded-md bg-brand text-white disabled:opacity-60">
          {asking ? <RotateCw className="h-[18px] w-[18px] animate-spin" /> : <Send className="h-[18px] w-[18px]" />}
        </button>
      </form>
      {askError}
    </Card>
  );
}
