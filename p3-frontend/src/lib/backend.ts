/**
 * Every REST call the screens make, built on the team's typed client (src/lib/api.ts).
 *
 * api.ts does the HTTP and holds the session token. This file adds what the
 * contract asks the UI to do on errors (C1), so screens never repeat it:
 *   401 UNAUTHENTICATED → back to login · 429 RATE_LIMITED → wait 2 s, retry
 *   503 LLM_UNAVAILABLE → "Running from recorded analysis" banner
 */
import { API, ApiError, call, getAuthConfig, getHealth, getOptions, getPlants, getToken, login as apiLogin, setToken, approve as apiApprove } from "./api";
import type {
  AgentEvent,
  AlarmPage,
  Approval,
  ApprovalRequest,
  AuditPage,
  AuditVerify,
  ErrorCode,
  Feedback,
  FeedbackRequest,
  Incident,
  IncidentCreate,
  LineOEE,
  ModelInfo,
  ProductionOrder,
  RedactionReport,
  Scenario,
  ScenarioResponse,
  Signal,
  SignalSeries,
  TokenResponse,
  User,
  IncidentOverview,
  IncidentPage,
  JobView,
  WorkUpdate,
  MaintenanceLogEntry,
  SessionInfo,
  DocumentInfo,
  DocumentDetail,
  DocSearchResponse,
  GardenSnapshot,
  SimStatus,
  FaultResponse,
  WorkCompleteRequest,
  ResolveRequest,
  OptionChatMessage,
  OptionChatResponse,
  SignoffTask,
} from "./contract";
import { sleep } from "./utils";

export { API, ApiError, getToken, setToken };

// ---- handlers registered by the providers --------------------------------------
let onUnauthenticated: (() => void) | null = null;
export const setUnauthenticatedHandler = (fn: (() => void) | null) => {
  onUnauthenticated = fn;
};
let onLlmUnavailable: (() => void) | null = null;
export const setLlmUnavailableHandler = (fn: (() => void) | null) => {
  onLlmUnavailable = fn;
};

// ---- error codes ----------------------------------------------------------------
export type ClientErrorCode = ErrorCode | "NETWORK" | "UNKNOWN";

/** fetch itself failed (server down, CORS, offline) */
export class NetworkError extends Error {
  constructor() {
    super("Can't reach the PlantGuard server.");
    this.name = "NetworkError";
  }
}

function codeFromStatus(status: number): ClientErrorCode {
  return ({ 401: "UNAUTHENTICATED", 403: "FORBIDDEN", 404: "NOT_FOUND", 409: "CONFLICT", 422: "VALIDATION", 429: "RATE_LIMITED", 503: "LLM_UNAVAILABLE" } as Record<number, ClientErrorCode>)[status] ?? "UNKNOWN";
}

/** C1: switch on the error code, never on the message. */
export function errorCode(e: unknown): ClientErrorCode | null {
  if (e instanceof ApiError) return (e.body?.code as ClientErrorCode | undefined) ?? codeFromStatus(e.status);
  if (e instanceof NetworkError) return "NETWORK";
  return null;
}

/** Runs one api.ts call and applies the C1 error behaviour. */
async function run<T>(fn: () => Promise<T>, attempt = 0): Promise<T> {
  try {
    return await fn();
  } catch (e) {
    if (e instanceof TypeError) throw new NetworkError(); // fetch() could not connect
    const code = errorCode(e);
    if (code === "RATE_LIMITED" && attempt < 2) {
      await sleep(2000);
      return run(fn, attempt + 1);
    }
    if (code === "UNAUTHENTICATED") onUnauthenticated?.();
    if (code === "LLM_UNAVAILABLE") onLlmUnavailable?.();
    throw e;
  }
}

const get = <T,>(path: string) => run(() => call<T>("GET", path));
const post = <T,>(path: string, body?: unknown) => run(() => call<T>("POST", path, body));
const del = <T,>(path: string) => run(() => call<T>("DELETE", path));

/** Query string; arrays repeat the key (FastAPI list params: ?signals=a&signals=b). */
function withQuery(path: string, q: Record<string, string | number | boolean | string[] | undefined | null>) {
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(q)) {
    if (v === undefined || v === null) continue;
    if (Array.isArray(v)) v.forEach((x) => params.append(k, x));
    else params.set(k, String(v));
  }
  const s = params.toString();
  return s ? `${path}?${s}` : path;
}

/**
 * A call with a DIFFERENT token than the session's — only the presenter's admin
 * token uses this (README: keep a second admin token for "next scene").
 * api.ts always sends the session token, so this one uses fetch directly.
 */
async function callAs<T>(token: string | null, method: string, path: string, body?: unknown): Promise<T> {
  let r: Response;
  try {
    r = await fetch(API + path, {
      method,
      headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new NetworkError();
  }
  if (!r.ok) throw new ApiError(r.status, await r.json().catch(() => ({ code: "FORBIDDEN", message: r.statusText, trace_id: "" })));
  return r.json();
}

const enc = encodeURIComponent;

// ---- endpoints (C1) ----------------------------------------------------------------
export const backend = {
  authConfig: () => run(getAuthConfig),
  /** Session sign-in: api.ts stores the token for every later call. */
  login: (username: string, password: string) => run(() => apiLogin(username, password)),
  me: () => get<User>("/auth/me"),

  plants: () => run(getPlants),
  alarms: (plantId: string) => get<AlarmPage>(withQuery("/ot/alarms", { plant_id: plantId, active: true })),
  signals: (assetId: string, signals: Signal[], minutes: number) =>
    get<SignalSeries[]>(withQuery(`/ot/assets/${enc(assetId)}/signals`, { signals, minutes })),

  orders: (lineId?: string, plantId?: string) => get<ProductionOrder[]>(withQuery("/mes/orders", { line_id: lineId, plant_id: plantId })),
  oee: (lineId: string, plantId?: string) => get<LineOEE>(withQuery(`/mes/lines/${enc(lineId)}/oee`, { plant_id: plantId })),

  health: (assetId: string) => run(() => getHealth(assetId)),
  models: () => get<ModelInfo[]>("/ml/models"),

  incident: (id: string) => get<Incident>(`/incidents/${enc(id)}`),
  /** Same call, but NOT_FOUND gives null (C1: empty state) so an issue that was reset never lingers on screen. */
  incidentOrNull: async (id: string): Promise<Incident | null> => {
    try {
      return await backend.incident(id);
    } catch (e) {
      if (errorCode(e) === "NOT_FOUND") return null;
      throw e;
    }
  },
  createIncident: (body: IncidentCreate) => post<Incident>("/incidents", body),
  events: (id: string, afterSeq?: number) => get<AgentEvent[]>(withQuery(`/incidents/${enc(id)}/events`, { after_seq: afterSeq })),
  /** Every event so far, following after_seq until nothing new comes back. */
  allEvents: async (id: string) => {
    const all: AgentEvent[] = [];
    let after: number | undefined;
    for (let page = 0; page < 50; page++) {
      const batch = await backend.events(id, after);
      const fresh = batch.filter((e) => after === undefined || e.seq > after);
      if (!fresh.length) break;
      all.push(...fresh);
      after = Math.max(...fresh.map((e) => e.seq));
    }
    return all;
  },
  options: (id: string) => run(() => getOptions(id)),
  approve: (id: string, body: ApprovalRequest): Promise<Approval> => run(() => apiApprove(id, body)),
  feedback: (id: string, body: FeedbackRequest) => post<Feedback>(`/incidents/${enc(id)}/feedback`, body),

  // ---- v1.2: incident lists, the repair, technician jobs, maintenance log ----
  incidents: (q: { scope?: "open" | "done" | "all"; mine?: boolean; kind?: "incident" | "question" | "all"; plantId?: string; parentId?: string } = {}) =>
    get<IncidentPage>(withQuery("/incidents", { scope: q.scope, mine: q.mine, kind: q.kind, plant_id: q.plantId, parent_id: q.parentId })),
  overview: (id: string) => get<IncidentOverview>(`/incidents/${enc(id)}/overview`),
  timeline: (id: string) => get<WorkUpdate[]>(`/incidents/${enc(id)}/timeline`),
  job: (id: string) => get<JobView>(`/incidents/${enc(id)}/job`),
  workStart: (id: string, note?: string) => post<JobView>(`/incidents/${enc(id)}/work/start`, { note: note || null }),
  workNote: (id: string, note: string) => post<JobView>(`/incidents/${enc(id)}/work/note`, { note }),
  workComplete: (id: string, body: WorkCompleteRequest) => post<JobView>(`/incidents/${enc(id)}/work/complete`, body),
  resolve: (id: string, body: ResolveRequest) => post<Feedback>(`/incidents/${enc(id)}/resolve`, body),
  reanalyse: (id: string, reason: string) => post<IncidentOverview>(`/incidents/${enc(id)}/reanalyse`, { reason }),
  maintenanceLog: () => get<MaintenanceLogEntry[]>("/incidents/log"),
  signoffs: () => get<SignoffTask[]>("/incidents/signoffs"),
  optionChat: (id: string) => get<OptionChatMessage[]>(`/incidents/${enc(id)}/options/chat`),
  /** onlyIfPlan: a message from another chat — the options change only if it asks for a different plan */
  postOptionChat: (id: string, message: string, onlyIfPlan = false) =>
    post<OptionChatResponse>(`/incidents/${enc(id)}/options/chat`, { message, only_if_plan: onlyIfPlan }),
  ask: (assetId: string, question: string, parentId?: string | null) =>
    post<Incident>("/incidents", { asset_id: assetId, trigger: "user_question", question, parent_id: parentId ?? null }),

  // ---- v1.2: manuals (FAISS), sessions, garden, simulator ----
  docs: (kind?: string) => get<DocumentInfo[]>(withQuery("/engineering/docs", { kind })),
  doc: (id: string) => get<DocumentDetail>(`/engineering/docs/${enc(id)}`),
  docSearch: (query: string, assetId?: string | null, topK = 8) =>
    post<DocSearchResponse>("/engineering/docs/search", { query, asset_id: assetId ?? null, top_k: topK }),
  sessions: (allUsers = false) => get<SessionInfo[]>(withQuery("/auth/sessions", { all_users: allUsers })),
  endSession: (sid: string) => del<void>(`/auth/sessions/${enc(sid)}`),
  refresh: () => post<TokenResponse>("/auth/refresh"),
  logoutServer: () => post<void>("/auth/logout"),
  garden: () => get<GardenSnapshot>("/garden"),
  simStatus: () => get<SimStatus>("/simulator/status"),
  /** needs run_simulator: the presenter passes its separate admin token */
  injectFault: (assetId?: string | null, mode?: string | null, adminToken?: string | null) =>
    callAs<FaultResponse>(adminToken ?? getToken() ?? null, "POST", "/simulator/faults", { asset_id: assetId || null, failure_mode: mode || null }),
  /** admin, plant manager, maintenance lead: switch machine breakdowns on/off (off by default) */
  setBreakdowns: (enabled: boolean) => callAs<SimStatus>(getToken() ?? null, "POST", "/simulator/breakdowns", { enabled }),
  setPlantSpeed: (scale: number, adminToken?: string | null) =>
    callAs<SimStatus>(adminToken ?? getToken() ?? null, "POST", "/simulator/speed", { scale }),

  audit: (incidentId?: string) => get<AuditPage>(withQuery("/audit", { incident_id: incidentId })),
  auditVerify: () => get<AuditVerify>("/audit/verify"),
  redactions: () => get<RedactionReport[]>("/security/redactions"),

  // ---- presenter (separate admin token; never touches the session token) ----
  adminLogin: (username: string, password: string) => callAs<TokenResponse>(null, "POST", "/auth/token", { username, password }),
  setScene: (name: Scenario, adminToken: string) => callAs<ScenarioResponse>(adminToken, "POST", `/simulator/scenario/${enc(name)}`),
};
