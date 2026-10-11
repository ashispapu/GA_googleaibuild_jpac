/**
 * The ONE place that knows API field names (generated/api.d.ts).
 *
 * Screens never touch API objects directly; they get small "view" objects from
 * the mappers below. If the contract changes, `npm run typecheck` points here.
 */
import type { components } from "../../../generated/api"; // same path api.ts uses
import { bandOf, type Band, failureModeLabel, REVIEW_THRESHOLD } from "./labels";

type S = components["schemas"];

// ---- API types (aliases only) -------------------------------------------------
export type ErrorResponse = S["ErrorResponse"];
export type ErrorCode = S["ErrorCode"];
export type Money = S["Money"];
export type AuthConfig = S["AuthConfig"];
export type TokenResponse = S["TokenResponse"];
export type User = S["User"]; // GET /auth/me and TokenResponse.user
export type Plant = S["Plant"];
export type Line = S["Line"];
export type Asset = S["Asset"];
export type Signal = S["Signal"];
export type SignalSeries = S["SignalSeries"];
export type TelemetryMessage = S["TelemetryMessage"];
export type AlarmPage = S["Page_Alarm_"];
export type ProductionOrder = S["ProductionOrder"];
export type LineOEE = S["LineOEE"];
export type AssetHealth = S["AssetHealth"];
export type ModelInfo = S["ModelInfo"];
export type Incident = S["Incident"];
export type IncidentCreate = S["IncidentCreate"];
export type AgentEvent = S["AgentEvent"];
export type Evidence = S["Evidence"];
export type Diagnosis = S["Diagnosis"];
export type SparePartsResult = S["SparePartsResult"];
export type MonitorResult = S["MonitorResult"];
export type OptionsResponse = S["OptionsResponse"];
export type Option = S["Option"];
export type ApprovalRequest = S["ApprovalRequest"];
export type Approval = S["Approval"];
export type FeedbackRequest = S["FeedbackRequest"];
export type Feedback = S["Feedback"];
export type AuditPage = S["Page_AuditEntry_"];
export type AuditEntry = S["AuditEntry"];
export type AuditVerify = S["AuditVerify"];
export type RedactionReport = S["RedactionReport"];
export type Scenario = S["Scenario"];
export type ScenarioResponse = S["ScenarioResponse"];

// ---- Plant layout --------------------------------------------------------------
export interface MachineView {
  id: string;
  name: string;
  type: string;
  stationId: string;
  band: Band;
  risk: number;
  state: Asset["state"];
}
export interface StationView {
  id: string;
  name: string;
  machines: MachineView[];
}
export interface LineView {
  id: string;
  name: string;
  product: string;
  speedPct: number;
  oee: number;
  state: Line["state"];
  band: Band;
  currentOrderId: string | null;
  stations: StationView[];
}
export interface PlantView {
  id: string;
  name: string;
  location: string;
  lines: LineView[];
}

const bandFrom = (h: S["Health"] | undefined, risk: number): Band => h ?? bandOf(risk);

export function plantView(p: Plant): PlantView {
  return {
    id: p.id,
    name: p.name,
    location: p.location,
    lines: p.lines.map(lineView),
  };
}

export function lineView(l: Line): LineView {
  return {
    id: l.id,
    name: l.name,
    product: l.product,
    speedPct: l.speed_pct,
    oee: l.oee,
    state: l.state,
    band: l.health,
    currentOrderId: l.current_order_id ?? null,
    stations: [...l.stations]
      .sort((a, b) => a.order - b.order)
      .map((s) => ({
        id: s.id,
        name: s.name,
        machines: s.assets.map((a) => ({
          id: a.id,
          name: a.name,
          type: a.type,
          stationId: s.id,
          band: bandFrom(a.health, a.risk_72h),
          risk: a.risk_72h,
          state: a.state,
        })),
      })),
  };
}

// ---- Machine health (C5) -----------------------------------------------------------
export interface Reason {
  text: string;
  /** 0..1, relative to the strongest reason */
  weight: number;
  strength: "Strong" | "Medium" | "Small";
}

export interface HealthView {
  assetId: string;
  risk: number;
  band: Band;
  hoursLeft: number | null;
  problem: string;
  ruleBreached: boolean;
  reasons: Reason[];
  scoredAt: string;
}

export function healthView(h: AssetHealth): HealthView {
  const feats = h.top_features ?? [];
  const max = Math.max(1e-9, ...feats.map((f) => Math.abs(f.contribution)));
  const reasons: Reason[] = feats
    .filter((f) => f.contribution > 0)
    .slice(0, 5)
    .map((f) => {
      const weight = Math.abs(f.contribution) / max;
      return { text: f.description, weight, strength: weight >= 0.7 ? "Strong" : weight >= 0.4 ? "Medium" : "Small" };
    });
  return {
    assetId: h.asset_id,
    risk: h.risk_72h,
    band: bandFrom(h.health, h.risk_72h),
    hoursLeft: h.rul_hours ?? null,
    problem: failureModeLabel(h.predicted_failure_mode),
    ruleBreached: h.rule_threshold_breached,
    reasons,
    scoredAt: h.evaluated_at,
  };
}

// ---- Telemetry -----------------------------------------------------------------
export interface SignalPoint {
  t: number;
  value: number;
}

/** Signal names (C6 tag map → `Signal` enum). */
export const SIGNALS = {
  vibration: "vibration_rms",
  bearingTemp: "bearing_temp",
  knocks: "vibration_kurtosis",
  power: "power_kw",
  rpm: "spindle_rpm",
} as const satisfies Record<string, Signal>;

/** One TelemetryMessage carries several values: {values: {vibration_rms: 4.3, …}}. */
export function telemetryPairs(m: TelemetryMessage) {
  const t = new Date(m.ts).getTime();
  return Object.entries(m.values ?? {}).map(([signal, value]) => ({ asset: m.asset_id, signal, value, t, quality: m.quality }));
}

export function seriesPoints(series: SignalSeries[], signal: Signal): SignalPoint[] {
  const s = series.find((x) => x.signal === signal);
  return (s?.points ?? []).map((p) => ({ t: new Date(p.ts).getTime(), value: p.value })).sort((a, b) => a.t - b.t);
}

// ---- Incident + agent events ---------------------------------------------------
export interface EvidenceView {
  kind: string;
  title: string;
  snippet: string;
  /** a UI route — the backend's link, or our evidence viewer when it has none */
  link: string;
}

export const evidenceView = (e: Evidence): EvidenceView => ({
  kind: e.kind,
  title: e.title,
  snippet: e.snippet,
  link: e.link ?? `/evidence/${encodeURIComponent(e.kind)}/${encodeURIComponent(e.ref_id)}`,
});

export interface EventView {
  seq: number;
  ts: string;
  agent: AgentEvent["agent"];
  kind: AgentEvent["type"];
  /** for kind "status": did the agent start or finish? */
  status: "started" | "finished" | null;
  message: string;
  tool: string | null;
  evidence: EvidenceView[];
  confidence: number | null;
  result: AgentEvent["result"];
}

export function eventView(e: AgentEvent): EventView {
  return {
    seq: e.seq,
    ts: e.ts,
    agent: e.agent,
    kind: e.type,
    // AgentEvent has no separate status field; a "status" event says it in its message.
    status: e.type === "status" ? (/finish|done|complete/i.test(e.message) ? "finished" : "started") : null,
    message: e.message,
    tool: e.tool ?? null,
    evidence: (e.evidence ?? []).map(evidenceView),
    confidence: e.confidence ?? null,
    result: e.result ?? null,
  };
}

/** The orchestrator's `finished` ends the run (C2/C3). */
export const isRunFinished = (e: EventView) => e.agent === "orchestrator" && e.kind === "status" && e.status === "finished";

/** Short summary of an agent result: the agent's own message, else built from its result. */
export function resultSummary(e: EventView): string {
  if (e.message) return e.message;
  const r = e.result as Record<string, unknown> | null;
  if (!r) return "Done";
  if ("summary" in r && typeof r.summary === "string") return r.summary; // MonitorResult
  if ("root_cause" in r) {
    const d = r as unknown as Diagnosis;
    return `${d.root_cause} · ${Math.round(d.confidence * 100)}% sure`;
  }
  if ("local_stock" in r) {
    const sp = r as unknown as SparePartsResult;
    return `${sp.sku}: ${sp.local_stock} here`;
  }
  if ("options" in r) return "Options are ready";
  return "Done";
}

/** Confidence of the diagnosis (C9: 0.60). */
export function diagnosisConfidence(events: EventView[]): number | null {
  const r = [...events].reverse().find((e) => e.agent === "diagnosis" && e.kind === "result");
  if (!r) return null;
  return r.confidence ?? (r.result && "confidence" in r.result ? (r.result as Diagnosis).confidence : null);
}

/** Amber node/card: the spare-parts agent found no stock at this plant. */
export function resultNeedsAttention(e: EventView): boolean {
  if (e.kind !== "result" || e.agent !== "spare_parts" || !e.result) return false;
  return "local_stock" in e.result && (e.result as SparePartsResult).local_stock === 0;
}

// ---- Options -------------------------------------------------------------------
export type Deadline = "met" | "missed" | "at_risk";

export interface OptionView {
  id: string;
  letter: string;
  title: string;
  summary: string;
  cost: Money;
  /** risk-weighted cost (C9: option C "€33,485 expected") */
  costIsExpected: boolean;
  downtimeHours: number;
  deadline: Deadline;
  energyKwh: number;
  co2Kg: number;
  failureRisk: number;
  confidence: number;
  recommended: boolean;
  risks: string[];
  actions: { kind: Option["actions"][number]["kind"]; description: string }[];
  repair: { start: string; hours: number; technician: string } | null;
  speed: { pct: number; start: string; end: string | null } | null;
}

export interface OptionsView {
  incidentId: string;
  confidence: number;
  needsHumanReview: boolean;
  reason: string;
  options: OptionView[];
}

export function optionView(o: Option): OptionView {
  let repair: OptionView["repair"] = null;
  let speed: OptionView["speed"] = null;
  for (const a of o.actions) {
    if (a.kind === "work_order") repair = { start: a.draft.planned_start, hours: a.draft.duration_hours, technician: a.draft.technician_id };
    if (a.kind === "speed_change") speed = { pct: a.speed_pct, start: a.start, end: a.end ?? null };
  }
  const deadline: Deadline = o.deadline_met ? "met" : o.downtime_hours === 0 && o.failure_risk_until_repair >= 0.5 ? "at_risk" : "missed";
  return {
    id: o.id,
    letter: o.id.replace(/^OPT-/, ""),
    title: o.label,
    summary: o.summary,
    cost: o.cost.total,
    costIsExpected: o.failure_risk_until_repair >= 0.5,
    downtimeHours: o.downtime_hours,
    deadline,
    energyKwh: o.energy_kwh,
    co2Kg: o.co2_kg,
    failureRisk: o.failure_risk_until_repair,
    confidence: o.confidence,
    recommended: o.recommended,
    risks: o.risks ?? [],
    actions: o.actions.map((a) => ({ kind: a.kind, description: a.description })),
    repair,
    speed,
  };
}

export function optionsView(r: OptionsResponse): OptionsView {
  const options = r.options.map(optionView);
  const rec = options.find((o) => o.recommended) ?? options[0];
  const confidence = rec?.confidence ?? 1;
  return {
    incidentId: r.incident_id,
    confidence,
    needsHumanReview: r.needs_human_review || confidence < REVIEW_THRESHOLD,
    reason: r.reason,
    options,
  };
}

// ---- Approval / resolution -----------------------------------------------------
export interface ReceiptView {
  kind: string;
  ref: string | null;
  ok: boolean;
  message: string;
}
export const receiptsView = (a: Approval): ReceiptView[] =>
  a.executed_actions.map((x) => ({ kind: x.kind, ref: x.ref_id ?? null, ok: x.ok, message: x.message }));

/** "USR-lead" → "lead" */
export const userShortName = (id: string) => id.replace(/^USR-/, "");

// ---- Model metrics -------------------------------------------------------------
export interface AccuracyView {
  version: string;
  recall: number | null;
  precision: number | null;
  f1: number | null;
  leadTimeGainHours: number | null;
}
export function accuracyView(models: ModelInfo[]): AccuracyView | null {
  // The failure classifier carries the metrics (C5: failure_clf + anomaly models).
  const m = models.find((x) => /fail|clf|classif/i.test(x.kind) && x.metrics) ?? models.find((x) => x.metrics?.recall !== undefined) ?? models[0];
  if (!m) return null;
  const k = m.metrics ?? {};
  return {
    version: m.version,
    recall: k.recall ?? null,
    precision: k.precision ?? null,
    f1: k.f1 ?? null,
    leadTimeGainHours: k.lead_time_gain_hours ?? null, // C5 required metric
  };
}
