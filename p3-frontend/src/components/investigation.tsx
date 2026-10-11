"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { AnimatePresence, motion } from "framer-motion";
import { Background, Handle, Position, ReactFlow, type Edge, type Node, type NodeProps } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { Loader2, RotateCw, Send } from "lucide-react";
import { diagnosisConfidence, isRunFinished, resultNeedsAttention, resultSummary, type EventView } from "@/lib/contract";
import { formatTime, formatTimeSeconds } from "@/lib/format";
import { AGENT_ORDER, AGENTS, agentName, EVENT_KIND_LABEL, REVIEW_THRESHOLD, toolLabel, type AgentKey } from "@/lib/labels";
import { cn } from "@/lib/utils";
import { Card, CardHeader, CardTitle } from "./ui/card";
import { EvidenceLink, Mono } from "./common";

// ---- Agent graph (React Flow) ---------------------------------------------------------

type NodeState = "waiting" | "running" | "done" | "attention";

interface AgentNodeData extends Record<string, unknown> {
  agent: AgentKey;
  state: NodeState;
  note: string;
}

const STATE_STYLE: Record<NodeState, { box: string; dot: string; note: string }> = {
  waiting: { box: "border-line bg-subtle", dot: "border-[1.5px] border-[#9AA8BA] bg-transparent", note: "text-ink-5" },
  running: { box: "border-[1.5px] border-brand bg-brand-soft shadow-[0_0_0_4px_rgba(29,95,209,0.12)]", dot: "bg-brand animate-pulse", note: "text-brand-text" },
  done: { box: "border-ok-border bg-surface", dot: "bg-ok", note: "text-ok-text" },
  attention: { box: "border-warn-border bg-warn-soft", dot: "bg-warn", note: "text-warn-text" },
};

function AgentNode({ data }: NodeProps<Node<AgentNodeData>>) {
  const s = STATE_STYLE[data.state];
  return (
    <div className={cn("flex w-[150px] flex-col gap-0.5 rounded-[10px] border px-3 py-2.5 font-sans", s.box)}>
      <Handle type="target" position={Position.Left} />
      <Handle type="source" position={Position.Right} />
      <Handle id="bottom-out" type="source" position={Position.Bottom} />
      <Handle id="bottom-in" type="target" position={Position.Bottom} />
      <span className="flex items-center gap-1.5 text-sm font-semibold text-ink">
        <span className={cn("h-[9px] w-[9px] rounded-full", s.dot)} />
        {AGENTS[data.agent].name}
      </span>
      <span className="text-xs text-ink-4">{AGENTS[data.agent].job}</span>
      <span className={cn("truncate text-xs", s.note)} title={data.note}>
        {data.note}
      </span>
    </div>
  );
}

const nodeTypes = { agent: AgentNode };

export function agentStates(events: EventView[]): Record<string, { state: NodeState; note: string }> {
  const out: Record<string, { state: NodeState; note: string }> = {};
  for (const a of AGENT_ORDER) out[a] = { state: "waiting", note: "waiting" };
  for (const e of events) {
    const cur = out[e.agent];
    if (!cur) continue;
    if (e.kind === "status" && e.status === "started") out[e.agent] = { state: "running", note: "working…" };
    if (e.kind === "result") out[e.agent] = { state: resultNeedsAttention(e) ? "attention" : "done", note: resultSummary(e) };
    if (e.kind === "status" && e.status === "finished" && cur.state === "running") out[e.agent] = { state: "done", note: "done" };
  }
  if (events.some(isRunFinished)) out.orchestrator = { state: "done", note: "options ready" };
  else if (out.orchestrator!.state === "running") out.orchestrator!.note = "running the team";
  return out;
}

export function AgentGraph({ events }: { events: EventView[] }) {
  const states = agentStates(events);
  const nodes: Node<AgentNodeData>[] = AGENT_ORDER.map((agent, i) => ({
    id: agent,
    type: "agent",
    position: { x: i * 165, y: 0 },
    data: { agent, state: states[agent]!.state, note: states[agent]!.note },
    draggable: false,
    selectable: false,
  }));
  const edges: Edge[] = AGENT_ORDER.slice(1).map((agent, i) => ({
    id: `${AGENT_ORDER[i]}-${agent}`,
    source: AGENT_ORDER[i]!,
    target: agent,
    animated: states[agent]!.state === "running",
    style: { stroke: "#9AA8BA", strokeWidth: 1.6 },
  }));
  edges.push({
    id: "decision-back",
    source: "decision",
    sourceHandle: "bottom-out",
    target: "orchestrator",
    targetHandle: "bottom-in",
    type: "smoothstep",
    animated: states.decision!.state === "running",
    style: { stroke: "#1D5FD1", strokeWidth: 1.6, strokeDasharray: "5 5" },
    label: "brings the options back",
    labelStyle: { fill: "#1A4FAE", fontSize: 12, fontFamily: "IBM Plex Sans" },
    labelBgStyle: { fill: "#FFFFFF" },
  });

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
      <div className="h-[170px] w-full" aria-label="Agent graph: Coordinator, Monitor, Diagnosis, Spare parts, Scheduler, Energy, Decision" role="img">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          fitView
          fitViewOptions={{ padding: 0.02 }}
          nodesDraggable={false}
          nodesConnectable={false}
          elementsSelectable={false}
          panOnDrag={false}
          zoomOnScroll={false}
          zoomOnPinch={false}
          zoomOnDoubleClick={false}
          preventScrolling={false}
          proOptions={{ hideAttribution: true }}
        >
          <Background color="#EEF1F5" gap={18} />
        </ReactFlow>
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
  tool_call: { card: "border-line bg-surface", chip: "bg-line-soft text-ink-3" },
  tool_result: { card: "border-line bg-surface", chip: "bg-line-soft text-ink-3" },
  evidence: { card: "border-brand-border bg-surface", chip: "bg-brand-soft text-brand-text" },
  result: { card: "border-ok-border bg-surface", chip: "bg-ok-soft text-ok-text" },
  error: { card: "border-crit-border bg-crit-soft", chip: "border border-crit-border bg-surface text-crit-text" },
};

export function EventFeed({ events, incidentId, assetId, live }: { events: EventView[]; incidentId: string; assetId?: string; live: boolean }) {
  const [filter, setFilter] = useState<(typeof FILTERS)[number]["key"]>("all");
  // C2 rendering rules: STATUS only lights nodes; TOOL_RESULT is folded into its TOOL_CALL.
  const visible = events.filter((e) => e.kind !== "status" && e.kind !== "tool_result" && (filter === "all" || e.kind === filter));
  const lastSeq = events[events.length - 1]?.seq;

  return (
    <Card className="flex min-w-0 flex-[1.2_1_420px] flex-col gap-2.5">
      <CardHeader>
        <CardTitle>What the AI team is doing</CardTitle>
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
      </CardHeader>
      {visible.length === 0 && <p className="m-0 py-6 text-center text-sm text-ink-4">{live ? "Waiting for the AI team…" : "Nothing here yet."}</p>}
      <ol className="m-0 flex list-none flex-col gap-2.5 p-0">
        <AnimatePresence initial={false}>
          {visible.map((e) => (
            <motion.li
              key={e.seq}
              layout
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.25 }}
            >
              <EventCard e={e} incidentId={incidentId} assetId={assetId} streaming={live && e.seq === lastSeq && e.kind === "thought"} />
            </motion.li>
          ))}
        </AnimatePresence>
      </ol>
    </Card>
  );
}

function EventCard({ e, incidentId, assetId, streaming }: { e: EventView; incidentId: string; assetId?: string; streaming: boolean }) {
  const attention = resultNeedsAttention(e);
  const style = attention ? { card: "border-warn-border bg-warn-soft", chip: "border border-warn-border bg-surface text-warn-text" } : KIND_STYLE[e.kind] ?? KIND_STYLE.tool_call!;
  const head = (
    <div className="flex items-center gap-2 text-xs text-ink-5">
      <span className={cn("rounded-full px-2 py-0.5 font-semibold", style.chip)}>{EVENT_KIND_LABEL[e.kind] ?? e.kind}</span>
      <span className="font-semibold text-ink">{agentName(e.agent)}</span>
      <span className="ml-auto font-mono">{formatTimeSeconds(e.ts)}</span>
    </div>
  );

  // Every AI claim is clickable (README): results link to the screen they talk about.
  const resultHref =
    e.agent === "decision" ? `/options/${incidentId}` : e.agent === "monitor" && assetId ? `/machines/${assetId}` : null;

  return (
    <div className={cn("flex flex-col gap-1.5 rounded-[10px] border px-3 py-2.5", style.card)}>
      {head}
      {e.kind === "thought" && (
        <div className="flex items-center gap-2 text-sm text-ink-2">
          {e.message}
          {streaming && <Loader2 className="h-3.5 w-3.5 animate-spin text-brand" aria-label="still thinking" />}
        </div>
      )}
      {e.kind === "tool_call" && (
        <div className="text-sm">
          {toolLabel(e.tool ?? "")} {e.tool && <Mono className="text-xs text-ink-5">{e.tool}</Mono>}
        </div>
      )}
      {(e.kind === "evidence" || e.kind === "error") && e.message && e.evidence.length === 0 && <div className="text-sm">{e.message}</div>}
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
  const answer = useMemo(() => {
    const said = events.filter((e) => e.agent === "orchestrator" && e.kind !== "status" && e.message);
    return said[said.length - 1] ?? null;
  }, [events]);
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
          <span className="text-xs text-ink-5">PlantGuard{answer && ` · ${formatTime(answer.ts)}`}</span>
          {answer ? (
            <p className="m-0 whitespace-pre-line text-[15px] leading-relaxed">{answer.message}</p>
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
