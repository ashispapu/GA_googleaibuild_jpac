// Plain-language words for everything the API sends as codes.
// The product is used by people on the shop floor, so no ML or IT jargon on screen.

export type AgentKey =
  | "orchestrator"
  | "monitor"
  | "diagnosis"
  | "spare_parts"
  | "scheduler"
  | "energy"
  | "decision"
  | "executor";

/** C3 agent order: orchestrator → monitor → diagnosis → spare_parts → scheduler → energy → decision */
export const AGENT_ORDER: AgentKey[] = [
  "orchestrator",
  "monitor",
  "diagnosis",
  "spare_parts",
  "scheduler",
  "energy",
  "decision",
];

export const AGENTS: Record<AgentKey, { name: string; job: string }> = {
  orchestrator: { name: "Coordinator", job: "Runs the team" },
  monitor: { name: "Monitor", job: "Watches the sensors" },
  diagnosis: { name: "Diagnosis", job: "Finds the cause" },
  spare_parts: { name: "Spare parts", job: "Checks stock" },
  scheduler: { name: "Scheduler", job: "Plans the repair" },
  energy: { name: "Energy", job: "Saves power" },
  decision: { name: "Decision", job: "Compares options" },
  executor: { name: "Executor", job: "Carries out approved steps" },
};

export const agentName = (a: string) => AGENTS[a as AgentKey]?.name ?? a.replace(/_/g, " ");

/** C2 event types → what the card is called on screen */
export const EVENT_KIND_LABEL: Record<string, string> = {
  thought: "Thinking",
  tool_call: "Checking data",
  tool_result: "Data received",
  evidence: "Evidence",
  result: "Finding",
  status: "Status",
  error: "Problem",
};

/** C4 tool names → plain phrases. Unknown tools fall back to their name with spaces. */
const TOOL_LABEL: Record<string, string> = {
  get_asset_health: "Read the machine's health report",
  get_signals: "Looked at the sensor history",
  get_asset_signals: "Looked at the sensor history",
  get_alarms: "Checked machine alarms",
  get_asset_history: "Checked past repairs",
  get_maintenance_history: "Checked past repairs",
  search_docs: "Searched the manuals",
  get_bom: "Checked the machine's parts list",
  get_asset_bom: "Checked the machine's parts list",
  get_part: "Checked spare-part stock",
  get_stock: "Checked spare-part stock",
  get_supplier: "Checked the supplier's delivery time",
  get_orders: "Checked the customer orders",
  get_line_schedule: "Checked the line schedule",
  get_oee: "Checked line efficiency",
  get_defects: "Checked quality rejects",
  get_inspections: "Checked quality inspections",
  get_shifts: "Checked who is on shift",
  get_technicians: "Checked available technicians",
  get_consumption: "Checked energy use",
  get_tariffs: "Checked electricity prices",
  get_co2_factor: "Checked the CO2 factor",
  predict: "Asked the prediction model",
  create_work_order: "Created a repair job",
  create_stock_transfer: "Created a part transfer",
};
export const toolLabel = (tool: string) => TOOL_LABEL[tool] ?? `Used ${tool.replace(/_/g, " ")}`;

/** C5 predicted_failure_mode */
export const FAILURE_MODE_LABEL: Record<string, string> = {
  bearing_wear: "Worn bearing",
  overheating: "Overheating",
  misalignment: "Parts out of line",
};
export const failureModeLabel = (m: string | null | undefined) =>
  m ? FAILURE_MODE_LABEL[m] ?? m.replace(/_/g, " ") : "No clear problem";

/** C7 roles */
export const ROLE_LABEL: Record<string, string> = {
  operator: "Operator",
  maintenance_lead: "Maintenance Lead",
  plant_manager: "Plant Manager",
  admin: "Admin",
};
export const roleLabel = (r: string) => ROLE_LABEL[r] ?? r;

/** C7 permissions */
export const PERMISSION_LABEL: Record<string, string> = {
  view_plant: "See the plant",
  view_costs: "See costs",
  run_agents: "Ask the AI team",
  approve: "Approve repairs",
  submit_feedback: "Send feedback",
  view_audit: "See activity record",
  run_simulator: "Run the demo",
};

/** C7 redaction kinds (<KIND_n>) */
export const REDACTION_LABEL: Record<string, string> = {
  SERIAL: "Serial numbers",
  NAME: "People's names",
  TAG: "Machine signal names",
  EMAIL: "Email addresses",
  IP: "Network addresses",
};
export const redactionLabel = (kind: string) => REDACTION_LABEL[kind.toUpperCase()] ?? kind;

/** C4 write actions, as the approval dialog explains them */
export const ACTION_LABEL: Record<string, string> = {
  work_order: "A repair job is created",
  stock_transfer: "A part is sent over",
  speed_change: "The line changes speed",
  create_work_order: "A repair job is created",
  create_stock_transfer: "A part is sent over",
};
export const actionLabel = (kind: string) => ACTION_LABEL[kind] ?? kind.replace(/_/g, " ");

/** Audit actions → plain sentence. Unknown actions fall back to their name. */
const AUDIT_LABEL: Record<string, string> = {
  "incident.opened": "Opened an issue",
  "incident.created": "Opened an issue",
  "options.ready": "Suggested options",
  "approval.signed": "Approved and signed",
  "approval.created": "Approved and signed",
  "work_order.created": "Created a repair job",
  "stock_transfer.created": "Created a part transfer",
  "speed_change.created": "Changed the line speed",
  "speed_change.applied": "Changed the line speed",
  "feedback.submitted": "Sent feedback",
  "tool.call": "Used a data tool",
  "llm.call": "Asked the AI model",
  "guard.blocked": "Blocked a hidden instruction",
};
export const auditLabel = (action: string) => AUDIT_LABEL[action] ?? action.replace(/[._]/g, " ");

/** Health bands (C0): ok < 0.3 ≤ warning < 0.7 ≤ critical */
export type Band = "ok" | "warning" | "critical";
export const bandOf = (risk: number | null | undefined): Band =>
  risk === null || risk === undefined ? "ok" : risk >= 0.7 ? "critical" : risk >= 0.3 ? "warning" : "ok";
export const BAND_LABEL: Record<Band, string> = {
  ok: "Normal",
  warning: "Keep an eye on",
  critical: "Act now",
};

/** C0: confidence below 0.70 needs a person to review */
export const REVIEW_THRESHOLD = 0.7;

/**
 * Pitch claim for the before/after table, not an API value:
 * how long a maintenance team needs to put the same plan together by hand.
 */
export const MANUAL_PLANNING_TIME = "4+ hours";

/** Vibration rule from C5: RMS > 4.5 mm/s sustained 30 min */
export const RULE_LIMIT_MM_S = 4.5;
