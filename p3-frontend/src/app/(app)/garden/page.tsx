"use client";

/**
 * PlantGuard garden — the whole system on one map, and where every open issue's agent is right now.
 *
 * Each issue has its own colour; all tokens share one shape. A token is keyed by its incident id and only its
 * x/y animate, so it glides from system to system instead of disappearing and reappearing. Moves that arrive in a
 * burst (an agent calling five tools in a second) are paced: a token stays at least DWELL_MS on a node, then jumps
 * to the latest place, so the eye can follow it.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { motion, AnimatePresence } from "framer-motion";
import {
  BadgeCheck, Bot, BookOpen, Boxes, BrainCircuit, CalendarClock, ClipboardList, Cpu, Database, Factory, HardHat, Layers,
  MonitorDot, Router, Sparkles, Truck, UserCheck, Users, Wrench, Zap, type LucideIcon,
} from "lucide-react";
import { PageTitle, Mono, EmptyState } from "@/components/common";
import { StageBadge } from "@/components/incidents";
import { Card, CardTitle } from "@/components/ui/card";
import { API, backend, getToken } from "@/lib/backend";
import type { GardenAgent } from "@/lib/contract";
import { formatTime } from "@/lib/format";
import { agentName } from "@/lib/labels";
import { useAppState } from "@/lib/app-state";
import { openStream } from "@/lib/ws";
import { cn } from "@/lib/utils";

// ---- the map ---------------------------------------------------------------------------------

type Zone = "ot" | "it" | "ai" | "people";
interface SysNode { id: string; label: string; sub: string; icon: LucideIcon; x: number; y: number; zone: Zone }

const W = 1200;
const H = 740;
const NODES: SysNode[] = [
  // OT — machines to data
  { id: "floor", label: "Shop floor", sub: "machines & lines", icon: Factory, x: 180, y: 640, zone: "ot" },
  { id: "plc", label: "PLCs", sub: "machine controllers", icon: Cpu, x: 95, y: 500, zone: "ot" },
  { id: "gateway", label: "OT gateway", sub: "OPC UA / MQTT", icon: Router, x: 265, y: 500, zone: "ot" },
  { id: "historian", label: "Historian", sub: "sensor history", icon: Database, x: 95, y: 350, zone: "ot" },
  { id: "scada", label: "SCADA", sub: "alarms", icon: MonitorDot, x: 265, y: 350, zone: "ot" },
  { id: "ml", label: "ML health", sub: "risk scores", icon: BrainCircuit, x: 180, y: 200, zone: "ot" },
  // AI core
  { id: "llm", label: "LLM", sub: "Gemini via LiteLLM", icon: Sparkles, x: 480, y: 190, zone: "ai" },
  { id: "vdb", label: "Manuals", sub: "FAISS vector DB", icon: BookOpen, x: 720, y: 190, zone: "ai" },
  { id: "agents", label: "Agent team", sub: "7 agents", icon: Bot, x: 600, y: 350, zone: "ai" },
  { id: "incidents", label: "Incidents", sub: "store & audit", icon: ClipboardList, x: 600, y: 510, zone: "ai" },
  // IT — business systems
  { id: "mes", label: "MES", sub: "orders & schedule", icon: CalendarClock, x: 935, y: 130, zone: "it" },
  { id: "erp", label: "ERP", sub: "stock & transfers", icon: Boxes, x: 1095, y: 130, zone: "it" },
  { id: "cmms", label: "CMMS", sub: "work orders", icon: Wrench, x: 935, y: 265, zone: "it" },
  { id: "suppliers", label: "Suppliers", sub: "lead times", icon: Truck, x: 1095, y: 265, zone: "it" },
  { id: "plm", label: "PLM", sub: "parts lists", icon: Layers, x: 935, y: 400, zone: "it" },
  { id: "qms", label: "QMS", sub: "quality rejects", icon: BadgeCheck, x: 1095, y: 400, zone: "it" },
  { id: "workforce", label: "Workforce", sub: "shifts & skills", icon: Users, x: 935, y: 535, zone: "it" },
  { id: "ems", label: "Energy", sub: "tariffs & CO₂", icon: Zap, x: 1095, y: 535, zone: "it" },
  // people
  { id: "lead", label: "Maintenance lead", sub: "approves options", icon: UserCheck, x: 470, y: 660, zone: "people" },
  { id: "technician", label: "Technician", sub: "does the repair", icon: HardHat, x: 730, y: 660, zone: "people" },
];
const BY_ID = Object.fromEntries(NODES.map((n) => [n.id, n]));

const EDGES: [string, string][] = [
  ["floor", "plc"], ["plc", "gateway"], ["gateway", "historian"], ["gateway", "scada"], ["historian", "ml"],
  ["ml", "incidents"], ["scada", "incidents"], ["incidents", "agents"], ["agents", "llm"], ["agents", "vdb"],
  ["agents", "historian"], ["agents", "ml"], ["agents", "scada"],
  ["agents", "mes"], ["agents", "erp"], ["agents", "cmms"], ["agents", "plm"], ["agents", "qms"], ["agents", "workforce"], ["agents", "ems"],
  ["erp", "suppliers"], ["cmms", "workforce"], ["incidents", "lead"], ["incidents", "technician"], ["technician", "floor"], ["cmms", "technician"],
];

const ZONES: { zone: Zone; label: string; x: number; y: number; w: number; h: number; fill: string; stroke: string }[] = [
  { zone: "ot", label: "OT · plant floor network", x: 20, y: 120, w: 330, h: 590, fill: "#F3F7F4", stroke: "#CDE3D4" },
  { zone: "ai", label: "PlantGuard AI", x: 380, y: 120, w: 440, h: 460, fill: "#F2F6FD", stroke: "#C9D9F4" },
  { zone: "it", label: "IT · business systems", x: 850, y: 60, w: 330, h: 540, fill: "#FAF6EF", stroke: "#EADBC2" },
  { zone: "people", label: "People", x: 380, y: 600, w: 440, h: 120, fill: "#F7F5FB", stroke: "#DDD6EE" },
];

/** One colour per incident (index from the server), same token shape for all. */
const PALETTE = ["#1D5FD1", "#D9480F", "#1F9D55", "#9C36B5", "#C2255C", "#0B7285", "#E67700", "#5F3DC4", "#2B8A3E", "#A61E4D"];
const colorOf = (i: number) => PALETTE[((i % PALETTE.length) + PALETTE.length) % PALETTE.length]!;

const DWELL_MS = 900;
const NODE_R = 30;

// ---- live feed ---------------------------------------------------------------------------------

interface Move { incident_id: string; node: string; activity: string; ts: string; color: number; seq: number }

function useGarden() {
  const [shown, setShown] = useState<Record<string, GardenAgent>>({});
  const [moves, setMoves] = useState<Move[]>([]);
  const [connected, setConnected] = useState(false);
  const pending = useRef<Record<string, GardenAgent>>({});
  const lastMove = useRef<Record<string, number>>({});
  const seq = useRef(0);

  useEffect(() => {
    let ws: WebSocket | null = null;
    let closed = false;
    let retry = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const take = (g: GardenAgent) => {
      pending.current[g.incident_id] = g;
    };

    const open = async () => {
      try {
        const snap = await backend.garden();
        for (const g of snap.agents) take(g);
      } catch {
        /* the socket sends the snapshot too */
      }
      if (closed) return;
      ws = openStream<GardenAgent>(API, "/garden/stream", getToken() ?? "", take);
      ws.onopen = () => {
        retry = 0;
        setConnected(true);
      };
      ws.onerror = () => ws?.close();
      ws.onclose = () => {
        setConnected(false);
        if (closed) return;
        retry += 1;
        timer = setTimeout(open, Math.min(10_000, 1000 * 2 ** Math.min(retry, 4)));
      };
    };
    void open();

    // pacing loop: apply the newest pending position per incident once it has dwelt long enough
    const tick = setInterval(() => {
      const now = Date.now();
      const ready: GardenAgent[] = [];
      for (const [id, g] of Object.entries(pending.current)) {
        if (now - (lastMove.current[id] ?? 0) >= DWELL_MS) {
          ready.push(g);
          delete pending.current[id];
          lastMove.current[id] = now;
        }
      }
      if (!ready.length) return;
      setShown((prev) => {
        const next = { ...prev };
        for (const g of ready) next[g.incident_id] = g;
        return next;
      });
      setMoves((prev) =>
        [
          ...ready.map((g) => ({ incident_id: g.incident_id, node: g.node, activity: g.activity, ts: g.ts, color: g.color, seq: ++seq.current })),
          ...prev,
        ].slice(0, 60),
      );
    }, 150);

    return () => {
      closed = true;
      clearInterval(tick);
      if (timer) clearTimeout(timer);
      ws?.close();
    };
  }, []);

  return { agents: shown, moves, connected };
}

/** Several tokens on one node sit on a small ring around it, in a stable order. */
function layout(agents: GardenAgent[]): Record<string, { x: number; y: number }> {
  const byNode: Record<string, string[]> = {};
  for (const a of agents) (byNode[a.node] ??= []).push(a.incident_id);
  const out: Record<string, { x: number; y: number }> = {};
  for (const [nodeId, ids] of Object.entries(byNode)) {
    const n = BY_ID[nodeId] ?? BY_ID.agents!;
    ids.sort();
    ids.forEach((id, i) => {
      const k = ids.length;
      const r = NODE_R + 20 + (k > 6 ? 10 : 0);
      const angle = -Math.PI / 2 + (k === 1 ? Math.PI / 4 : (2 * Math.PI * i) / k);
      out[id] = { x: n.x + r * Math.cos(angle), y: n.y + r * Math.sin(angle) };
    });
  }
  return out;
}

// ---- page ------------------------------------------------------------------------------------

export default function GardenPage() {
  const { agents, moves, connected } = useGarden();
  const { plantId } = useAppState();
  const [plantFilter, setPlantFilter] = useState<"all" | "this">("all");
  const [focus, setFocus] = useState<string | null>(null);

  const list = useMemo(
    () =>
      Object.values(agents)
        .filter((a) => plantFilter === "all" || a.plant_id === plantId)
        .sort((a, b) => a.incident_id.localeCompare(b.incident_id)),
    [agents, plantFilter, plantId],
  );
  const pos = useMemo(() => layout(list), [list]);
  const busy = useMemo(() => new Set(list.map((a) => a.node)), [list]);
  const focused = focus ? agents[focus] : null;

  return (
    <>
      <PageTitle
        title="PlantGuard garden"
        sub="The whole system, live. Each coloured token is one issue’s agent, sitting on the system it is using right now."
        right={
          <div className="flex flex-wrap items-center gap-2 text-[13px]">
            <span className={cn("flex items-center gap-1.5", connected ? "text-ok-text" : "text-ink-5")}>
              <span className={cn("h-2 w-2 rounded-full", connected ? "animate-pulse bg-ok" : "bg-ink-5")} />
              {connected ? "Live" : "Connecting…"}
            </span>
            {(["all", "this"] as const).map((k) => (
              <button
                key={k}
                type="button"
                aria-pressed={plantFilter === k}
                onClick={() => setPlantFilter(k)}
                className={cn("min-h-[34px] rounded-full px-3", plantFilter === k ? "bg-ink font-semibold text-white" : "border border-[#D6DEE7] text-ink-3 hover:bg-subtle")}
              >
                {k === "all" ? "All my plants" : `${plantId.replace("PLANT-", "Plant ")} only`}
              </button>
            ))}
          </div>
        }
      />

      <div className="flex flex-wrap items-start gap-4">
        <Card className="min-w-0 flex-[3_1_640px] p-2 sm:p-3">
          <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full" role="img" aria-label="System architecture with live agent positions">
            <defs>
              <filter id="tok-shadow" x="-50%" y="-50%" width="200%" height="200%">
                <feDropShadow dx="0" dy="1.5" stdDeviation="1.5" floodOpacity="0.25" />
              </filter>
            </defs>
            {ZONES.map((z) => (
              <g key={z.zone}>
                <rect x={z.x} y={z.y} width={z.w} height={z.h} rx={16} fill={z.fill} stroke={z.stroke} strokeWidth={1.5} />
                <text x={z.x + 14} y={z.y + 22} fontSize={13} fontWeight={600} fill="#5B6B7F" fontFamily="IBM Plex Sans, sans-serif">
                  {z.label}
                </text>
              </g>
            ))}
            {EDGES.map(([a, b]) => {
              const na = BY_ID[a]!;
              const nb = BY_ID[b]!;
              const hot = list.some((t) => (t.node === a || t.node === b) && (a === "agents" || b === "agents"));
              return (
                <line
                  key={`${a}-${b}`}
                  x1={na.x} y1={na.y} x2={nb.x} y2={nb.y}
                  stroke={hot ? "#8DA9DA" : "#CBD4DF"}
                  strokeWidth={hot ? 1.8 : 1.2}
                  strokeDasharray={a === "agents" || b === "agents" ? "4 5" : undefined}
                />
              );
            })}
            {NODES.map((n) => (
              <NodeView key={n.id} n={n} active={busy.has(n.id)} />
            ))}
            {list.map((a) => {
              const p = pos[a.incident_id]!;
              const start = BY_ID[a.node] ? { x: BY_ID.floor!.x, y: BY_ID.floor!.y } : p;
              return (
                <Token
                  key={a.incident_id}
                  a={a}
                  x={p.x}
                  y={p.y}
                  startX={start.x}
                  startY={start.y}
                  dim={!!focus && focus !== a.incident_id}
                  onClick={() => setFocus((f) => (f === a.incident_id ? null : a.incident_id))}
                />
              );
            })}
          </svg>
          {!list.length && (
            <p className="m-0 px-3 pb-2 text-sm text-ink-4">
              No issue is being worked on right now. When the plant raises one, its token appears on the shop floor and walks through the
              systems the agents use.
            </p>
          )}
        </Card>

        <div className="flex min-w-0 flex-[1_1_300px] flex-col gap-4">
          <Card className="flex flex-col gap-3">
            <CardTitle>Issues on the map</CardTitle>
            {list.length ? (
              <ul className="m-0 flex list-none flex-col gap-1 p-0">
                {list.map((a) => (
                  <li key={a.incident_id}>
                    <button
                      type="button"
                      onClick={() => setFocus((f) => (f === a.incident_id ? null : a.incident_id))}
                      aria-pressed={focus === a.incident_id}
                      className={cn("flex w-full items-start gap-2.5 rounded-md p-2 text-left hover:bg-subtle", focus === a.incident_id && "bg-subtle ring-1 ring-line-strong")}
                    >
                      <TokenGlyph color={colorOf(a.color)} size={22} />
                      <span className="flex min-w-0 flex-1 flex-col gap-0.5">
                        <span className="flex flex-wrap items-center gap-2 text-sm">
                          <Mono className="font-medium">{a.incident_id}</Mono>
                          <span className="text-ink-5">{a.asset_id} · {a.plant_id.replace("PLANT-", "Plant ")}</span>
                        </span>
                        <span className="truncate text-[13px] text-ink-3">
                          {BY_ID[a.node]?.label ?? a.node} · {a.activity}
                        </span>
                        <StageBadge stage={a.stage} className="self-start" />
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            ) : (
              <EmptyState title="Nothing moving right now." />
            )}
            {focused && (
              <div className="flex flex-wrap gap-2 border-t border-line pt-3 text-[13px]">
                <Link href={`/investigation/${focused.incident_id}`}>Watch the agents</Link>
                <span className="text-ink-5">·</span>
                <Link href={`/jobs/${focused.incident_id}`}>Open the job</Link>
              </div>
            )}
          </Card>

          <Card className="flex flex-col gap-2">
            <CardTitle>Latest moves</CardTitle>
            <ol className="m-0 flex max-h-[360px] list-none flex-col gap-1.5 overflow-y-auto p-0">
              <AnimatePresence initial={false}>
                {moves
                  .filter((m) => !focus || m.incident_id === focus)
                  .slice(0, 30)
                  .map((m) => (
                    <motion.li
                      key={m.seq}
                      layout
                      initial={{ opacity: 0, y: -6 }}
                      animate={{ opacity: 1, y: 0 }}
                      className="flex items-start gap-2 text-[13px]"
                    >
                      <span className="mt-1 h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: colorOf(m.color) }} />
                      <span className="min-w-0 flex-1">
                        <Mono className="text-ink-4">{m.incident_id}</Mono> → <strong className="font-semibold">{BY_ID[m.node]?.label ?? m.node}</strong>
                        <span className="block truncate text-ink-4">{m.activity}</span>
                      </span>
                      <Mono className="shrink-0 text-xs text-ink-5">{formatTime(m.ts)}</Mono>
                    </motion.li>
                  ))}
              </AnimatePresence>
            </ol>
          </Card>
        </div>
      </div>
    </>
  );
}

function NodeView({ n, active }: { n: SysNode; active: boolean }) {
  const Icon = n.icon;
  return (
    <g>
      <circle cx={n.x} cy={n.y} r={NODE_R} fill="#FFFFFF" stroke={active ? "#1D5FD1" : "#B7C3D1"} strokeWidth={active ? 2.2 : 1.4} />
      {active && (
        <circle cx={n.x} cy={n.y} r={NODE_R + 6} fill="none" stroke="#1D5FD1" strokeOpacity={0.25} strokeWidth={4}>
          <animate attributeName="r" values={`${NODE_R + 3};${NODE_R + 9};${NODE_R + 3}`} dur="2s" repeatCount="indefinite" />
        </circle>
      )}
      <Icon x={n.x - 13} y={n.y - 13} width={26} height={26} color={active ? "#1D5FD1" : "#3F4E60"} strokeWidth={1.7} aria-hidden />
      <text x={n.x} y={n.y + NODE_R + 17} textAnchor="middle" fontSize={13} fontWeight={600} fill="#1B2733" fontFamily="IBM Plex Sans, sans-serif">
        {n.label}
      </text>
      <text x={n.x} y={n.y + NODE_R + 32} textAnchor="middle" fontSize={11} fill="#6B7A8C" fontFamily="IBM Plex Sans, sans-serif">
        {n.sub}
      </text>
    </g>
  );
}

/** The shared token shape: a shield with a small bot face. Only the colour changes per issue. */
const SHIELD = "M0 -14 L11 -9 V0 C11 7 6 12 0 15 C-6 12 -11 7 -11 0 V-9 Z";

function TokenGlyph({ color, size = 22 }: { color: string; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="-13 -16 26 32" className="shrink-0" aria-hidden>
      <path d={SHIELD} fill={color} stroke="#fff" strokeWidth={1.5} />
      <circle cx={-4} cy={-1} r={1.8} fill="#fff" />
      <circle cx={4} cy={-1} r={1.8} fill="#fff" />
    </svg>
  );
}

function Token({
  a, x, y, startX, startY, dim, onClick,
}: { a: GardenAgent; x: number; y: number; startX: number; startY: number; dim: boolean; onClick: () => void }) {
  const color = colorOf(a.color);
  return (
    <motion.g
      initial={{ x: startX, y: startY, opacity: 0 }}
      animate={{ x, y, opacity: dim ? 0.25 : 1 }}
      transition={{ x: { type: "spring", stiffness: 60, damping: 16 }, y: { type: "spring", stiffness: 60, damping: 16 }, opacity: { duration: 0.3 } }}
      style={{ cursor: "pointer" }}
      onClick={onClick}
      role="button"
      aria-label={`${a.incident_id}: ${a.activity}`}
    >
      <title>{`${a.incident_id} · ${a.asset_id}\n${agentName(a.agent)} — ${a.activity}`}</title>
      <g filter="url(#tok-shadow)">
        <path d={SHIELD} fill={color} stroke="#fff" strokeWidth={1.8} transform="scale(1.15)" />
        <circle cx={-4.5} cy={-1} r={2} fill="#fff" />
        <circle cx={4.5} cy={-1} r={2} fill="#fff" />
      </g>
      <g transform="translate(0 -26)">
        <rect x={-30} y={-9} width={60} height={17} rx={8.5} fill="#fff" stroke={color} strokeWidth={1.2} />
        <text x={0} y={3.5} textAnchor="middle" fontSize={10.5} fontWeight={600} fill={color} fontFamily="IBM Plex Mono, monospace">
          {a.incident_id.replace("INC-", "")}
        </text>
      </g>
    </motion.g>
  );
}
