# P1 backend — folder and file guide

What every folder, file and function in `p1-backend/` does. For tasks and status see `README.md`.

Every function and class also has a docstring in the code, so hovering over it in the editor shows the same text.

## Contents

0. [Architecture at a glance](#0-architecture-at-a-glance) ← start here if you are new: diagrams of the whole system
   - [whole system](#03-the-whole-system-on-one-page) · [backend layers](#04-inside-the-backend-the-layers) · [one click](#05-what-happens-on-one-click-approve) · [incident story](#06-the-incident-story-from-start-to-end) · [states](#07-incident-status-the-states) · [live data](#08-live-data-and-alerts-the-scoring-path) · [event bus](#09-the-event-bus-who-posts-who-listens) · [security](#010-security-the-gates-a-request-passes) · [data](#011-where-the-data-lives-today-and-tomorrow) · [Docker](#012-docker-services) · [glossary](#013-words-you-will-meet)
1. [Big picture](#1-big-picture)
2. [`container.py` explained step by step](#2-containerpy-explained-step-by-step) ← start here if the wiring is confusing
3. [Reference: every file and function](#3-reference-every-file-and-function)
   - [app/ top level](#31-app-top-level) · [core/](#32-appcore) · [routers/](#33-approuters) · [services/](#34-appservices) · [repositories/](#35-apprepositories) · [connectors/](#36-appconnectors) · [middleware/ and infrastructure/](#37-appmiddleware-and-appinfrastructure)
4. [Tests](#4-tests)
5. [Common questions](#5-common-questions)

---

## 0. Architecture at a glance

This part is for someone who has never seen the project. Read it top to bottom; every later section adds detail.

### 0.1 The problem in one paragraph

A factory has machines (PLCs, sensors). **PlantGuard AI** watches them and warns before something breaks. When a machine looks unhealthy, the
system opens an **incident**, a team of AI **agents** investigates (reads sensor history, manuals, spare-part stock, the production plan,
energy prices) and proposes **options** (for example "slow the line to 70 % and order a bearing"). A **human** approves one option.
Only then does the backend create the work order and stock transfer. The technician later says whether the diagnosis was right.
Everything that matters is written to a tamper-evident **audit log**.

### 0.2 The four people and how their parts connect

| Who | Folder | Builds | Contracts (C1 to C9) |
|---|---|---|---|
| P1 (this folder) | `p1-backend/` | The backend: API, security, live data, approvals, audit | Provides C1, C2, C4, C7, C8. Uses C3 (agents), C5 (model), C6 (live feed) |
| P2 | `p2-agents/` | The AI agents, manual search (RAG), privacy redactor | Provides C3 (agent runtime). Uses C4 (tools) |
| P3 | `p3-frontend/` | The web UI | Uses C1 (REST) and C2 (WebSocket) |
| P4 | `p4-ml-data/` | ML model, machine simulator, documents, sample data | Provides C5 (model), C6 (live feed), C9 (golden data) |

> **contract** - an agreed shape of data or list of methods (see `CONTRACTS.md` and `plantguard_contracts/`) so four people can build in parallel.
> **REST** - ordinary request/response calls over HTTP, such as `GET /plants`.
> **WebSocket** - a connection that stays open so the server can push updates (live charts, agent steps).

Important: P2's agents and P4's model are **not separate servers**. They are Python code that the one backend process loads and calls.
That is why `container.py` is the single place where they plug in.

### 0.3 The whole system on one page

```
┌──────────────────────────────────┐                    ┌──────────────────────────────────────────┐
│ 1. USERS (browser, P3)           │                    │ 2. KEYCLOAK (optional login server)      │
├──────────────────────────────────┤                    ├──────────────────────────────────────────┤
│ operator, lead, manager, admin   │    login page      │ Logs people in and signs their token     │
│ Log in, watch live charts,       │◄──────────────────►│ Only used when AUTH_MODE=keycloak        │
│ read the agents' reasoning,      │  (SSO mode only)   │ Docker service 'keycloak' (profile sso)  │
│ click Approve / Reject           │                    │ The backend still decides permissions    │
└──────────────────────────────────┘                    └──────────────────────────────────────────┘
                 │                                                           ▲
                 │ REST + WebSocket,                                         │ fetches public keys (JWKS)
                 │ token on every call                                       │ to check token signatures
                 ▼                                                           │
┌──────────────────────────────────────────────────────────────────────────────────────────────────┐
│ 3. BACKEND - one Python process (FastAPI, P1) - http://localhost:8000                            │
├──────────────────────────────────────────────────────────────────────────────────────────────────┤
│ Each request: rate limit > token > permission > router > service > connector/repository          │
│ container.py builds every object once at start-up; an in-memory bus passes messages around       │
│                                                                                                  │
│  ┌──────────────────────────────────────────────┐ ┌────────────────────────────────────────────┐ │
│  │ 4. AGENTS (P2: p2-agents/)                   │ │ 5. ML MODEL (P4: p4-ml-data/ml/)           │ │
│  ├──────────────────────────────────────────────┤ ├────────────────────────────────────────────┤ │
│  │ Orchestrator + 6 agents: monitor, diagnosis, │ │ features.py: raw readings -> numbers       │ │
│  │ spare_parts, scheduler, energy, decision     │ │ serving.py: numbers -> risk_72h, RUL       │ │
│  │ RAG (manual search), redactor, cost model    │ │ Today: stubs (not written yet),            │ │
│  │ Today: ReplayAgentRuntime plays back a       │ │ so live scoring is OFF; fixture health     │ │
│  │ recorded run; P2's runtime plugs in later    │ │ is used instead                            │ │
│  └──────────────────────────────────────────────┘ └────────────────────────────────────────────┘ │
│ Agents reach plant data ONLY through the Tool Gateway (allow-list, approval on writes, audit)    │
└──────────────────────────────────────────────────────────────────────────────────────────────────┘
                 :                               :                         │
                 :                               :                         │
                 : redacted prompts (planned)    :                         │ reads JSON files
                 ▼                               :                         ▼
┌──────────────────────────────────────────────┐ :  ┌──────────────────────────────────────────────┐
│ 6. LLM PROVIDER (outside, via P2)            │ :  │ 7. DATA TODAY (inside the repo)              │
├──────────────────────────────────────────────┤ :  ├──────────────────────────────────────────────┤
│ Gets prompts AFTER the redactor removed      │ :  │ fixtures/story/*.json (JSON files):          │
│ serial numbers, names and PLC tags           │ :  │   ERP, MES, maintenance, quality,            │
│ The replay never calls it                    │ :  │   energy, workforce, plants, users           │
│ If it fails: fall back to the replay         │ :  │ config/tag_map.yaml: sensor ranges           │
└──────────────────────────────────────────────┘ :  │ Fake live readings: generated in code        │
                                                 :  │ Agent replay: agent_events_INC-0042          │
                                                 :  └──────────────────────────────────────────────┘
                                                 : dotted = planned,
                                                 ▼ not connected yet
┌──────────────────────────────────────────────────────────────────────────────────────────────────┐
│ 8. DOCKER SERVICES (docker-compose.yml) - running, but the backend does not use them yet         │
├──────────────────────────────────────────────────────────────────────────────────────────────────┤
│ Timescale/Postgres :5432   Redis :6379   Qdrant :6333   Mosquitto (MQTT) :1883                   │
│ Keycloak :8080 (only with --profile sso)                                                         │
│ Planned: Postgres = incidents + audit log, Redis = shared bus,                                   │
│          Qdrant = manual search (RAG), MQTT = live sensor readings                               │
└──────────────────────────────────────────────────────────────────────────────────────────────────┘
                              ▲
                              : MQTT messages (planned)
                              :
┌────────────────────────────────────────────────────────────────┐
│ 9. OT SIMULATOR (P4: p4-ml-data/simulator/)                    │
├────────────────────────────────────────────────────────────────┤
│ Pretends to be the factory floor: publishes machine readings   │
│ publisher.py is an empty stub today                            │
│ Meanwhile the backend makes its own fake readings              │
└────────────────────────────────────────────────────────────────┘

solid line = works today          dotted line = planned / not connected yet
```


How to read it: boxes 1 and 2 are people-facing. Box 3 is the program you are reading about (P1), with P2's agents (4) and P4's model (5) plugged
into the same process. Boxes 6 to 9 are outside things: the AI provider, data files, Docker services and the machine simulator.
The dotted lines are the integrations that are **designed but not connected yet**.

### 0.4 Inside the backend: the layers

```
Browser (P3) or P2 agents  --  HTTP / WebSocket
                              ▼
┌────────────────────────────────────────────────────────────────┐
│ MIDDLEWARE  (middleware/, main.py)                             │
├────────────────────────────────────────────────────────────────┤
│ Rate limit: token bucket per user/IP, else 429 RATE_LIMITED    │
│ CORS: which websites may call the API (main.py)                │
│ Error handlers: every error -> {code, message, trace_id}       │
└────────────────────────────────────────────────────────────────┘
                              ▼                                         ┌──────────────────────────────────────┐
┌────────────────────────────────────────────────────────────────┐      │ container.py                         │
│ ROUTERS  (routers/ - 15 files)                                 │      ├──────────────────────────────────────┤
├────────────────────────────────────────────────────────────────┤      │ Creates every object ONCE when       │
│ Read the token, check the permission (routers/deps.py)         ◄──────│ the server starts and connects       │
│ Validate input. NO business rules. One file per area:          │      │ them to each other                   │
│ auth plants ot erp mes maintenance quality engineering         │      │ (build_container).                   │
│ workforce energy ml incidents audit security simulator         │      │                                      │
└────────────────────────────────────────────────────────────────┘      │ Routers receive it through           │
                              ▼                                         │ Depends(container).                  │
┌────────────────────────────────────────────────────────────────┐      │                                      │
│ SERVICES  (services/ - the rules)                              │      │ Services never create their          │
├────────────────────────────────────────────────────────────────┤      │ own helpers; they are handed         │
│ incident_service    open, approve, feedback, status            │      │ what they need.                      │
│ run_coordinator     starts agents, stores + broadcasts events  │      │                                      │
│ tool_gateway        the only door agents use                   ◄──────│ To swap a fixture for a real         │
│ action_executor     carries out approved actions               │      │ system, change ONE line here.        │
│ ot_gateway          marks readings good/stale/out_of_range     │      │                                      │
│ scoring             ML risk loop and alerts                    │      │                                      │
│ auth, approval_tokens, write_authorizer, audit_log             │      │                                      │
└────────────────────────────────────────────────────────────────┘      │                                      │
                              ▼                                         │                                      │
┌────────────────────────────────────────────────────────────────┐      │                                      │
│ CONNECTORS and REPOSITORIES                                    │      │                                      │
├────────────────────────────────────────────────────────────────┤      │                                      │
│ connectors/fixtures.py   ERP, MES, maintenance... (JSON)       ◄──────│                                      │
│ connectors/telemetry     fake live sensor readings             │      └──────────────────────────────────────┘
│ repositories/            incidents (in memory), users          │
└────────────────────────────────────────────────────────────────┘
                              ▼
fixtures/story/*.json     Python lists/dicts in memory     later: Postgres, SAP, MQTT
```


The rule is: **each layer only talks to the layer below it**. `container.py` (right) is not a layer; it builds the objects in every layer and hands
them to whoever needs them.

### 0.5 What happens on one click: "Approve"

```
┌──────────────────────────────────────────────────────────────────────────────────────────────────┐
│ 1. BROWSER                                                                                       │
├──────────────────────────────────────────────────────────────────────────────────────────────────┤
│ Lead clicks Approve:  POST /incidents/INC-0042/approve                                           │
│ body {option_id: OPT-B, decision: approve}  +  Authorization: Bearer <token>                     │
└──────────────────────────────────────────────────────────────────────────────────────────────────┘
                                                  │
                                                  ▼
┌──────────────────────────────────────────────────────────────────────────────────────────────────┐
│ 2. RATE LIMIT  (middleware/rate_limit.py)                                                        │
├──────────────────────────────────────────────────────────────────────────────────────────────────┤
│ Does this user still have tokens in the bucket?  No -> 429                                       │
└──────────────────────────────────────────────────────────────────────────────────────────────────┘
                                                  │
                                                  ▼
┌──────────────────────────────────────────────────────────────────────────────────────────────────┐
│ 3. ROUTER  (routers/incidents.py: approve)                                                       │
├──────────────────────────────────────────────────────────────────────────────────────────────────┤
│ current_user: verify the token (local HS256 or Keycloak RS256) -> AccessClaims                   │
│ require(APPROVE): is role 'maintenance_lead' allowed to approve?  No -> 403                      │
└──────────────────────────────────────────────────────────────────────────────────────────────────┘
                                                  │
                                                  ▼
┌──────────────────────────────────────────────────────────────────────────────────────────────────┐
│ 4. INCIDENT SERVICE  (decide)                                                                    │
├──────────────────────────────────────────────────────────────────────────────────────────────────┤
│ Plant scope ok?  Status must be AWAITING_APPROVAL, else 409.  Flip status to APPROVED first,     │
│ so a second click cannot run the actions twice                                                   │
└──────────────────────────────────────────────────────────────────────────────────────────────────┘
                                                  │
                                                  ▼
┌──────────────────────────────────────────────────────────────────────────────────────────────────┐
│ 5. APPROVAL TOKEN SERVICE                                                                        │
├──────────────────────────────────────────────────────────────────────────────────────────────────┤
│ Issue a 10-minute token tied to INC-0042 + OPT-B + its action kinds                              │
└──────────────────────────────────────────────────────────────────────────────────────────────────┘
                                                  │
                                                  ▼
┌──────────────────────────────────────────────────────────────────────────────────────────────────┐
│ 6. ACTION EXECUTOR                                                                               │
├──────────────────────────────────────────────────────────────────────────────────────────────────┤
│ speed_change -> MES (line speed 70 %)                                                            │
│ work_order, stock_transfer -> Tool Gateway (write tools)                                         │
└──────────────────────────────────────────────────────────────────────────────────────────────────┘
                                                  │
                                                  ▼
┌──────────────────────────────────────────────────────────────────────────────────────────────────┐
│ 7. TOOL GATEWAY + WRITE AUTHORIZER                                                               │
├──────────────────────────────────────────────────────────────────────────────────────────────────┤
│ Agent allowed? Arguments valid? Token valid AND draft identical to the approved draft?           │
│ Run with a timeout; write an audit entry (never storing the token)                               │
└──────────────────────────────────────────────────────────────────────────────────────────────────┘
                                                  │
                                                  ▼
┌──────────────────────────────────────────────────────────────────────────────────────────────────┐
│ 8. CONNECTORS                                                                                    │
├──────────────────────────────────────────────────────────────────────────────────────────────────┤
│ FixtureMaintenance creates WO-2026-0153                                                          │
│ FixtureERP creates STO-4500012                                                                   │
└──────────────────────────────────────────────────────────────────────────────────────────────────┘
                                                  │
                                                  ▼
┌──────────────────────────────────────────────────────────────────────────────────────────────────┐
│ 9. INCIDENT SERVICE (end of decide)                                                              │
├──────────────────────────────────────────────────────────────────────────────────────────────────┤
│ All actions ok -> status EXECUTED (one failed -> stays APPROVED)                                 │
│ Audit 'approve'; publish incident.INC-0042.status                                                │
└──────────────────────────────────────────────────────────────────────────────────────────────────┘
                                                  │
                                                  ▼
┌──────────────────────────────────────────────────────────────────────────────────────────────────┐
│ 10. RESPONSE                                                                                     │
├──────────────────────────────────────────────────────────────────────────────────────────────────┤
│ Approval APR-0007 with executed_actions goes back to the browser                                 │
└──────────────────────────────────────────────────────────────────────────────────────────────────┘
```


### 0.6 The incident story from start to end

This is the demo story the contracts call "golden" (ids such as `INC-0042`, `APR-0007`, `WO-2026-0153` are fixed on purpose).

| # | Who | Does what | Backend part that handles it |
|---|---|---|---|
| 1 | Admin | Sets the scene to `reset`, then `warning` (`POST /simulator/scenario/...`) | `SceneController` |
| 2 | Telemetry | EQ-3012's vibration ramps from 2.1 to 4.3 mm/s and bearing temperature rises | `SyntheticTelemetrySource` |
| 3 | System | The scene listener (or the scoring loop) opens incident `INC-0042`, status `analysing` | `SceneIncidentLifecycle` / `AlertIncidentOpener` -> `IncidentService.open` |
| 4 | Operator | Opens `WS /incidents/INC-0042/stream`; the first viewer starts the agent run | `RunCoordinator.ensure_started` |
| 5 | Agents | Orchestrator, monitor, diagnosis, spare_parts, scheduler, energy, decision work in turn; each step is an `AgentEvent` (thought, tool_call, tool_result, evidence, result) | `ReplayAgentRuntime` (P2 later) |
| 6 | Agents | Every data request goes through the Tool Gateway (allow-list, validation, audit) | `ToolGateway` |
| 7 | Backend | Each event is stored and broadcast on `incident.INC-0042.events`; the viewer sees them live | `RunCoordinator`, `EventBus` |
| 8 | Backend | The orchestrator says `finished`; the incident becomes `awaiting_approval`; options OPT-A/B/C are ready | `RunCoordinator._record` |
| 9 | Lead | Reads the options and approves OPT-B (`POST .../approve`) | see 0.5 |
| 10 | Backend | Executes the actions: line speed 70 %, work order `WO-2026-0153`, stock transfer `STO-4500012`; status `executed` | `ActionExecutor`, `ToolGateway` |
| 11 | Technician | Sends feedback "diagnosis correct" | `IncidentService.feedback` |
| 12 | Backend | Similar-case confidence rises 0.60 -> 0.82; incident `resolved`; audit chain still valid | `ConfidenceUpdater`, `HashChainAuditLog` |

### 0.7 Incident status: the states

```
                  ┌──────────────┐  agents finished,    ┌─────────────────────┐
open ────────────►│ ANALYSING    │─────────────────────►│ AWAITING_APPROVAL   │
                  └──────────────┘  options ready       └─────────────────────┘
                                                              │         │
                                                              │ approve │ reject
                                                              ▼         ▼
                                                    ┌──────────────┐  ┌──────────────┐
              if one action fails, it stays here ──►│ APPROVED     │  │ REJECTED     │
                                                    └──────────────┘  └──────────────┘
                                                           │                 │
                                                           │ all actions ok  │
                                                           ▼                 │
                                                    ┌──────────────┐         │
                                                    │ EXECUTED     │         │
                                                    └──────────────┘         │ feedback
                                                           │                 │
                                      technician feedback  │                 │
                                                           ▼                 │
                                                    ┌──────────────┐         │
                                                    │ RESOLVED     │◄────────┘
                                                    └──────────────┘


Also: the simulator scene 'resolved' marks active incidents RESOLVED; 'calm' and 'reset' delete them.
```


### 0.8 Live data and alerts (the "scoring" path)

```
┌──────────────────────┐    ┌──────────────────────┐    ┌──────────────────────┐    ┌──────────────────────┐
│ TELEMETRY SOURCE     │    │ OT GATEWAY           │    │ HEALTH SCORER        │    │ EVENT BUS            │
├──────────────────────┤    ├──────────────────────┤    ├──────────────────────┤    ├──────────────────────┤
│ 1 reading / second   │───►│ tag map: valid range │───►│ keeps the last 10 min│───►│ ot.telemetry.*       │
│ per machine          │    │ marks each reading:  │    │ features -> model    │    │ ml.health.EQ-3012    │
│ fake now, MQTT later │    │ good, stale or       │    │ -> risk_72h (0 to 1) │    │ alerts.raised        │
└──────────────────────┘    │ out_of_range         │    └──────────────────────┘    └──────────────────────┘
                            └──────────────────────┘                                    │      │
           ┌────────────────────────────────────────────────────────────────────────────┘      │ no listener yet
           ▼                  alerts.raised: the alert opener listens                          ▼
┌──────────────────────┐    ┌──────────────────────────┐    ┌──────────────────────────────────────────────┐
│ ALERT OPENER         │    │ INCIDENT SERVICE         │    │ OTHER CHANNELS (posted, not used yet)        │
├──────────────────────┤    ├──────────────────────────┤    ├──────────────────────────────────────────────┤
│ opens an incident    │───►│ INC-0042 starts as       │    │ ot.telemetry, ml.health, incident status and │
│ for the machine      │    │ 'analysing'. Agents run  │    │ sim.scene are posted, but no backend part    │
│ (one per machine)    │    │ when the 1st viewer opens│    │ listens yet. The UI gets live readings from  │
└──────────────────────┘    │ the WebSocket            │    │ WS /ot/stream directly instead.              │
                            └──────────────────────────┘    └──────────────────────────────────────────────┘

Alert rule: risk_72h >= 0.7 and no alert for that machine in the last 30 minutes (debounce).
The alert travels over the bus: scoring posts it, the alert opener listens. (lifespan starts both loops.)
```


This path only runs when `SCORING_ENABLED=true` and needs P4's model. Until then, the incident is opened by the simulator scene instead.

### 0.9 The event bus: who posts, who listens

| Channel | Posted by | Listened to by | Carries |
|---|---|---|---|
| `alerts.raised` | `HealthScorer._maybe_alert` | `AlertIncidentOpener` | "Machine X is at risk" |
| `incident.{id}.events` | `RunCoordinator._record` | `RunCoordinator.follow` -> the incident WebSocket | Agent steps |
| `ot.telemetry.{plant}.{line}` | `ScoringService` (ingest and stale loops) | Nobody yet (the UI reads `WS /ot/stream` directly) | One reading per machine |
| `ml.health.{asset}` | `HealthScorer.evaluate` | Nobody yet | Health, risk, remaining life |
| `incident.{id}.status` | `IncidentService` (open, decide, feedback, resolve), `RunCoordinator` | Nobody yet | Status changes |
| `sim.scene` | `SceneController.set` | Nobody via the bus (scene listeners are called directly) | Scene changed |

Only two things listen to the bus today (`AlertIncidentOpener` and `RunCoordinator.follow`). The other channels are posted for the contract (C8) and for future listeners such as the UI or Redis.

> **bus** - a message board inside the backend. A part posts a message to a named channel; every part listening to that channel receives a copy.
> The poster does not know who is listening. A message with no listener is lost, so listeners must start first (that is what `lifespan` is for).
> Today it is memory only (`InMemoryEventBus`); Redis can replace it later without changing the callers.

### 0.10 Security: the gates a request passes

```
┌────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│ 1. NETWORK (TLS)                                                                                       │
├────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ Today plain http inside localhost / one Docker network. Caddy proxy for https is planned (BE-9)        │
└────────────────────────────────────────────────────────────────────────────────────────────────────────┘
                                                     │
                                                     ▼
┌────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│ 2. RATE LIMIT                                                                                          │
├────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ Token bucket per user or IP: 1200 per minute, bursts of 200 -> 429                                     │
└────────────────────────────────────────────────────────────────────────────────────────────────────────┘
                                                     │
                                                     ▼
┌────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│ 3. AUTHENTICATION                                                                                      │
├────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ Who are you? local: backend login, HS256 token.  keycloak: RS256 token checked with public keys        │
└────────────────────────────────────────────────────────────────────────────────────────────────────────┘
                                                     │
                                                     ▼
┌────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│ 4. PERMISSION (RBAC)                                                                                   │
├────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ What may your ROLE do? operator, maintenance_lead, plant_manager, admin.  The backend decides, always  │
└────────────────────────────────────────────────────────────────────────────────────────────────────────┘
                                                     │
                                                     ▼
┌────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│ 5. PLANT SCOPE                                                                                         │
├────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ Which PLANTS may you see? A token for PLANT-A cannot read PLANT-B -> 403 PLANT_SCOPE                   │
└────────────────────────────────────────────────────────────────────────────────────────────────────────┘
                                                     │
                                                     ▼
┌────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│ 6. APPROVAL TOKEN  (writes only)                                                                       │
├────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ 10 minutes, one incident, one option, one use per action kind. A login token is NOT one                │
└────────────────────────────────────────────────────────────────────────────────────────────────────────┘
                                                     │
                                                     ▼
┌────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│ 7. WRITE AUTHORIZER  (writes only)                                                                     │
├────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ The draft being written must be identical to the approved option's draft                               │
└────────────────────────────────────────────────────────────────────────────────────────────────────────┘
                                                     │
                                                     ▼
┌────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│ 8. TOOL GATEWAY  (agents only)                                                                         │
├────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ Allow-list per agent, input and output validated, timeout. Agents never write directly                 │
└────────────────────────────────────────────────────────────────────────────────────────────────────────┘
                                                     │
                                                     ▼
┌────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│ 9. AUDIT LOG  (everything above)                                                                       │
├────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ Every login, denial, tool call and decision is chained with sha256; GET /audit/verify detects edits    │
└────────────────────────────────────────────────────────────────────────────────────────────────────────┘
```


Two auth modes feed gate 3:

| | `AUTH_MODE=local` | `AUTH_MODE=keycloak` |
|---|---|---|
| Who logs the user in | The backend (`POST /auth/token`, users from `fixtures/story/users.json`) | The Keycloak login page |
| Token type | HS256 (shared secret `JWT_SECRET`) | RS256 (Keycloak's public key, fetched as JWKS) |
| Who decides permissions | The backend (`ROLE_PERMISSIONS`) | The backend (same code); Keycloak only says who you are and which role names you have |
| Approval tokens | Backend, HS256 | Backend, HS256 (never Keycloak) |

Roles and what they may do: **operator** view, run agents, submit feedback; **maintenance_lead** also view costs and approve; **plant_manager** view, costs, run agents, approve, view audit; **admin** everything (including the simulator).

### 0.11 Where the data lives today and tomorrow

| Data | Today | Planned |
|---|---|---|
| ERP, MES, maintenance history, quality, energy, workforce, plants, alarms | JSON files in `fixtures/story/` | Real SAP / MES / CMMS connectors (replace a `Fixture*` class) |
| Live machine readings | Generated by `SyntheticTelemetrySource` | MQTT from P4's simulator (Mosquitto) |
| Machine health score | `health_EQ-3012.json` | P4's model (`SCORING_ENABLED=true`) |
| Agent run | Replay of `agent_events_INC-0042.jsonl` | P2's real runtime + LLM |
| Manual search | `docsearch_bearing.json` | P2's RAG on Qdrant |
| Incidents, approvals, feedback | Memory (lost on restart) | Postgres |
| Audit log | Memory, seeded from `audit_seed.json` (lost on restart) | Postgres (keep the hash chain) |
| Created work orders and stock transfers | Memory lists on top of the JSON history | The real ERP / CMMS |
| Event bus | Memory | Redis |
| Users and passwords | `users.json` (local mode) | Keycloak (SSO mode) |

### 0.12 Docker services

| Service | Port | Purpose | Used by the backend today? |
|---|---|---|---|
| `api` | 8000 | This backend | (it is the backend) |
| `timescale` | 5432 | Postgres + time-series storage | No (planned: incidents, audit, telemetry history) |
| `redis` | 6379 | Cache / shared bus | No (planned) |
| `qdrant` | 6333 | Vector search for manuals | No (planned, P2's RAG) |
| `mosquitto` | 1883 | MQTT broker: the mailbox for machine readings | No (planned, P4 simulator publishes here) |
| `keycloak` | 8080 | Login server (profile `sso`: `make up-sso`) | Yes, when `AUTH_MODE=keycloak` |

> **MQTT** - a small publish/subscribe protocol for sensors. Machines publish readings to topics on a broker (Mosquitto). It is a mailbox, not a database.

### 0.13 Words you will meet

| Word | Meaning |
|---|---|
| asset | One machine, such as `EQ-3012` (a spindle). |
| line / plant | A production line (`L3`) inside a plant (`PLANT-A`). |
| telemetry | Live sensor readings from a machine. |
| risk_72h | The model's chance (0 to 1) that the asset fails within 72 hours. |
| incident | A problem being investigated and decided on (`INC-0042`). |
| agent | An AI worker with one job (monitor, diagnosis, spare parts, scheduler, energy, decision) coordinated by the orchestrator. |
| option | A proposed plan with actions, cost and risk (`OPT-A`, `OPT-B`, `OPT-C`). |
| action | One step of an option: speed change, work order or stock transfer. |
| tool | A function agents may call to read plant data (15 reads) or, via the executor, write (2 writes). |
| approval token | A 10-minute, one-use pass proving a human approved a specific plan; required for every write. |
| fixture | A saved sample data file used instead of a real system. |
| connector | A class that reads from or writes to one system (ERP, MES...). |
| Protocol | A list of method names a class must have; lets parts be swapped (a Python "interface"). |
| composition root | The one place where objects are created and connected (`container.py`). |
| audit log | An append-only record of who did what, chained with hashes so edits are detectable. |
| RBAC | Role-based access control: what you may do depends on your role. |
| JWT | A signed token (a text string) that proves who you are. |
| SSO | Single sign-on: one login (Keycloak) for many apps. |

---

## 1. Big picture

```
p1-backend/
├── README.md
├── STRUCTURE.md           (this file)
├── app/
│   ├── main.py            builds the FastAPI app
│   ├── container.py       composition root: creates and wires every object
│   ├── core/              settings, errors, clock/ids, backend Protocols
│   ├── routers/           HTTP + WebSocket endpoints, one file per area
│   ├── services/          business logic
│   ├── repositories/      storage behind Protocols
│   ├── connectors/        plant data sources (fixtures today)
│   │   └── telemetry/     live telemetry source + tag map
│   ├── middleware/        request-level middleware (rate limit)
│   └── infrastructure/    event bus
└── tests/
    ├── conftest.py        fixtures (fresh backend, logged-in users)
    ├── helpers.py         login + "run the whole story" helpers
    ├── data/              sample payloads used by tests
    ├── unit/              one class at a time, no HTTP
    └── integration/       through the real API
```

### Request flow

```
HTTP / WebSocket
  → middleware/        rate limit (+ CORS in main.py)
  → routers/           validate input, check permission (routers/deps.py)
  → services/          rules and orchestration
  → repositories/ and connectors/    data in and out
```

**Layer rule:** routers call services, services call repositories and connectors, nothing calls upward.

**Who creates objects?** Only `container.py`. Routers and services never write `SomeService()` themselves; they receive what they need. That is what makes parts replaceable (a fixture connector can become a real SAP connector by changing one line).

**Ports and adapters in one sentence:** a *port* is a `Protocol` (a list of method names, no code). An *adapter* is a class that has those methods. Services only know the port. Examples: `HealthProvider` (port) → `FixtureHealthProvider` / `LiveHealthProvider` (adapters); `IncidentRepository` (port) → `InMemoryIncidentRepository` (adapter).

---

## 2. `container.py` explained step by step

### What it is

`container.py` is the **composition root**: the one place where the backend is assembled.

- It defines `Container`, a plain dataclass whose fields are all the long-lived objects of the app (audit log, bus, connectors, services...).
- It defines `build_container()`, which creates every one of those objects in the right order, hands each its dependencies, and returns the `Container`.
- `main.py` calls `build_container()` once, stores the result in `app.state.container`, and routers read it through the dependency `routers/deps.py::container`. Tests call `build_container()` too, to get a fresh backend per test.

It contains **no business logic**. If you want to know *what* something does, look at the class; `container.py` only says *which* class is used and *what it is connected to*.

### Imports (top of the file)

The long import list is a table of every concrete class the backend uses. It is the only file that imports both the `Fixture*` adapters and the services, which is how the other files stay independent of each other.

### The `Container` dataclass

Fields grouped by purpose:

| Group | Fields | Meaning |
|---|---|---|
| Config | `settings` | The `Settings` object (paths, limits, flags). |
| Messaging / scene | `bus`, `scene` | In-memory event bus; current simulator scene. |
| Security | `verifier`, `authenticator`, `login`, `policy`, `tokens`, `write_authorizer`, `audit`, `redactions` | Token verification (local/Keycloak), login, RBAC + plant scope, approval tokens, write check, audit log, redaction reports. |
| Plant data (connectors) | `plants`, `directory`, `ot`, `ot_gateway`, `erp`, `mes`, `maintenance`, `quality`, `engineering`, `retriever`, `workforce`, `energy` | Where data comes from. |
| ML | `health`, `scores` | Health provider and the cache of live scores. |
| Agents / incidents | `tool_gateway`, `incidents`, `runs` | The tool door for agents, the incident service, the agent-run coordinator. |
| Telemetry | `telemetry_source` | The fake 1 Hz telemetry source. |
| Optional | `scoring`, `alert_opener` | `None` unless `SCORING_ENABLED=true`. |

### `build_container(settings=None, verifier=None)` — line by line

The order matters: an object can only be created after the objects it needs.

**A. Basics**

```python
s = settings or Settings.from_env()
clock = utcnow
store = FixtureStore(s.fixtures_dir)
verifier = verifier or TokenVerifier.from_env()
```
- `s`: settings, from the environment unless a test passes its own.
- `clock`: the function that returns "now". Everything receives it so tests can fake time.
- `store`: reads the JSON files in `fixtures/story/`.
- `verifier`: checks bearer tokens. `TokenVerifier.from_env()` reads `AUTH_MODE` (`local` = HS256 secret, `keycloak` = RS256 via JWKS). Tests can pass their own (the Keycloak test does).

**B. Core infrastructure**

```python
bus = InMemoryEventBus()
scene = SceneController(bus, s.initial_scenario, clock)
audit = HashChainAuditLog(clock, seed=store.load("audit_seed"))
policy = AccessPolicy(audit)
tokens = ApprovalTokenService(verifier.secret)
speeds = LineSpeeds()
```
- `bus`: pub/sub between parts (telemetry, health, incident events, scene changes).
- `scene`: holds the current simulator scene (calm / warning / crisis...) and announces changes on the bus.
- `audit`: the tamper-evident log, pre-filled with the historic entries from `audit_seed.json`.
- `policy`: "may this user do X in plant Y?"; it audits denials, hence it gets `audit`.
- `tokens`: issues and checks approval tokens; signed with the same secret as local tokens but always HS256, even in Keycloak mode.
- `speeds`: current speed percent per line, shared by the MES connector (writer) and the telemetry source (reader).

**C. Plant data connectors**

```python
plants = FixturePlants(store, scene)
directory = PlantDirectory(plants)
telemetry_source = SyntheticTelemetrySource(directory, scene, speeds, s.hero_asset_id, s.telemetry_interval_s, clock)
ot_gateway = OTGateway(telemetry_source, QualityPolicy(TagMap.load(s.tag_map_path)), clock)
ot = FixtureOT(store, scene, directory, ot_gateway)
erp, mes = FixtureERP(store, clock), FixtureMES(store, speeds)
maintenance, quality = FixtureMaintenance(store, clock), FixtureQuality(store)
engineering, retriever = FixtureEngineering(store), FixtureRetriever(store)
workforce, energy = FixtureWorkforce(store), FixtureEnergy(store)
scores = LatestScores()
health = FixtureHealthProvider(store, scene)
```
- `plants` reads the plant → line → station → asset tree; `directory` answers "where is asset X?" on top of it.
- `telemetry_source` makes fake live readings that follow the scene and line speed.
- `ot_gateway` wraps that source: loads the tag map (valid ranges) and marks each message `good`, `stale` or `out_of_range`.
- `ot` is the OT connector used by the routers; its live stream comes from `ot_gateway`.
- `erp`, `mes`, `maintenance`, `quality`, `engineering`, `retriever`, `workforce`, `energy` are one fixture class per domain. **To use a real system, replace one of these lines.**
- `scores` is an empty cache for live ML scores; `health` is the fixture health provider (replaced by `LiveHealthProvider` if scoring is enabled).

**D. Security and the tool gateway (what agents may call)**

```python
inc_ids = IdSequence("INC-", s.incident_id_start, width=4)
repo = InMemoryIncidentRepository(inc_ids.next, inc_ids.reset)
authorizer = WriteAuthorizer(tokens, repo)
handlers = build_handlers(ToolBackends(ot, erp, mes, maintenance, quality, engineering, retriever, workforce, energy, health))
tool_gateway = ToolGateway(handlers, audit, authorizer)
```
- `inc_ids`: generates INC-0042, INC-0043... (starts at 42 to match the golden story).
- `repo`: stores incidents in memory; it receives the id generator so it can allocate and reset ids.
- `authorizer`: a write is allowed only if a valid approval token covers it **and** the draft equals the approved option's draft (it asks `repo` which option was approved).
- `handlers`: dictionary "tool name → function that calls the right connector".
- `tool_gateway`: the single door through which agents reach plant data (allow-list, validation, approval on writes, timeout, audit).

**E. Agent runtime, executor and incident service**

```python
replay_factory = lambda speed: ReplayAgentRuntime(s.agent_events_file, speed, clock)
runtime_factory = replay_factory
executor = ActionExecutor([
    SpeedChangeHandler(mes, audit),
    ToolWriteHandler("work_order", "create_work_order", tool_gateway, audit),
    ToolWriteHandler("stock_transfer", "create_stock_transfer", tool_gateway, audit)])
incidents = IncidentService(repo, directory, health, audit, bus, tokens, executor, runtime_factory(1.0), policy,
                            ConfidenceUpdater(), IdSequence("APR-", 7, 4), IdSequence("FB-", 31, 4), clock)
runs = RunCoordinator(repo, bus, runtime_factory, audit, clock)
```
- `runtime_factory`: "given a playback speed, give me an agent runtime". Today it is the **replay** of the recorded INC-0042 run. **P2 plugs in here**, e.g. `lambda speed: FallbackAgentRuntime(their_runtime, replay_factory(speed))`.
- `executor`: when an option is approved, runs its actions: a speed change (through MES) and work orders / stock transfers (through the tool gateway, so they are validated and audited).
- `incidents`: the incident service gets every collaborator it needs; ids `APR-0007` (approval) and `FB-0031` (feedback) also start at golden-story numbers.
- `runs`: starts and records the agent run for an incident and fans events out on the bus.

**F. Scene reactions**

```python
lifecycle = SceneIncidentLifecycle(incidents, s.hero_asset_id)

async def reset_world(scenario):
    if scenario in (Scenario.RESET, Scenario.CALM):
        speeds.reset(), erp.reset(), maintenance.reset()
scene.add_listener(reset_world)
scene.add_listener(lifecycle.on_scene)
```
- When the demo scene changes, two things react: `reset_world` (on calm/reset it clears line speeds, created stock transfers and created work orders) and `lifecycle.on_scene` (calm/reset clears incidents; warning/crisis opens the hero incident; resolved closes it).

**G. Final assembly**

```python
redactions = RedactionLog()
c = Container(...all of the above...)
if s.scoring_enabled:
    _enable_scoring(c, clock)
return c
```
`redactions` stores the redaction reports P2 sends. The `Container` is built from everything above. If `SCORING_ENABLED=true`, `_enable_scoring` adds the real ML path.

### The other functions in `container.py`

| Function | What it does |
|---|---|
| `_enable_scoring(c, clock)` | Adds P4's `p4-ml-data` folder to `sys.path`, imports `ml.features.WindowFeatureExtractor` and `ml.serving.load_model`, loads the model from `MODEL_PATH`, replaces `c.health` with `LiveHealthProvider` (real scores, fixture fallback), and creates `c.scoring` (the background loop) and `c.alert_opener` (opens incidents from alerts). Only runs when scoring is on. |
| `_hours_since_service(c, asset_id)` | Hours since the asset's last finished work order ended. Feeds the model as a feature. Returns 0 if there is none. |
| `seed_demo_redaction(c)` | Adds one sample redaction report so the Security panel is not empty before the first LLM call. Called by `create_app`. |

### How to change what the backend uses

| You want to... | Change this |
|---|---|
| Use P2's real agent runtime | `runtime_factory` in section E |
| Use a real SAP / MES / CMMS | The matching `Fixture*` line in section C |
| Use a real ML model | Set `SCORING_ENABLED=true`; implement P4's `ml/serving.py` and `ml/features.py` |
| Store incidents in Postgres | Replace `InMemoryIncidentRepository` in section D (same Protocol) |
| Use Redis for the bus | Replace `InMemoryEventBus` in section B |
| Add a new kind of approved action | Add a handler class and put it in the `ActionExecutor([...])` list |

---

## 3. Reference: every file and function

Notation: a *method* is written `Class.method`. HTTP routes are written `METHOD /path`.

### 3.1 `app/` top level

#### `main.py` — builds the web app

| Name | What it does |
|---|---|
| `DEFAULT_SECRET` | The demo JWT secret; a warning is logged at startup if it is still in use. |
| `create_app(container=None, settings=None)` | Builds the container (or uses the one passed in by tests), seeds the demo redaction, adds rate-limit and CORS middleware, installs the error handlers and includes all 15 routers. Returns the FastAPI app. |
| `create_app.lifespan` | Startup/shutdown hook: warns about the default secret; starts the scoring loop and alert opener as background tasks if they exist; cancels them on shutdown. |
| `app` | `create_app()` called once at import time; this is what `uvicorn app.main:app` runs. |

#### `container.py` — see [section 2](#2-containerpy-explained-step-by-step)

---

### 3.2 `app/core/` — shared basics

#### `core/config.py` — settings

| Name | What it does |
|---|---|
| `ROOT` | Repo root folder; fixture, config and model paths are built from it. |
| `_flag(name, default)` | Reads a boolean env var (`1/true/yes/on` = true). |
| `Settings` | Frozen dataclass: `fixtures_dir`, `tag_map_path`, `model_path`, `cors_origins`, `rate_limit_per_min` (1200), `rate_limit_burst` (200), `hero_asset_id` (EQ-3012), `initial_scenario` (crisis), `incident_id_start` (42), `telemetry_interval_s` (1.0), `scoring_enabled` (false), `scoring_interval_s` (60), `agent_events_file`. |
| `Settings.from_env()` | Builds Settings from `CORS_ORIGINS`, `RATE_LIMIT_PER_MIN`, `RATE_LIMIT_BURST`, `INITIAL_SCENARIO`, `SCORING_ENABLED`, `SCORING_INTERVAL_S`, `MODEL_PATH`. Auth variables are read by `plantguard_contracts.auth` instead. |

#### `core/errors.py` — one error shape

| Name | What it does |
|---|---|
| `ApiError(status, code, message, **details)` | Exception any code can raise to return a contract error with a chosen HTTP status. |
| `error_response(status, code, message, details, headers)` | Builds the JSON `ErrorResponse {code, message, trace_id, details}` with a fresh trace id. |
| `install_error_handlers(app)` | Registers four handlers: `ApiError` → its own status; `KeyError` → 404 NOT_FOUND (connectors raise `KeyError` for unknown ids); framework HTTP errors → 404 or VALIDATION; request validation errors → 422 VALIDATION with the field errors. |

#### `core/ports.py` — backend-internal Protocols

| Name | What it does |
|---|---|
| `SceneView` | Read-only view of the scene: `scenario`, `changed_at`, `is_calm()`. Connectors get this instead of the full controller. |
| `HealthProvider` | `get_health(asset_id)`, `predict(window)`, `models()`. |
| `InspectionConnector` | The shared `QualityConnector` plus `get_inspections(line_id)`. |

#### `core/common.py` — clock and ids

| Name | What it does |
|---|---|
| `Clock` | Type: a function returning a datetime. |
| `utcnow()` | Current UTC time (default clock). |
| `IdSequence(prefix, start, width)` | Human-readable id generator (INC-0042, STO-4500012...). |
| `IdSequence.next()` | Returns the next id, thread-safe. |
| `IdSequence.reset()` | Goes back to the start number. |

---

### 3.3 `app/routers/` — the API (contracts C1, C2)

Routers are thin: validate input, check the permission, call a service or connector. No business rules. `VIEW` means the `VIEW_PLANT` permission.

#### `routers/deps.py` — shared FastAPI dependencies

| Name | What it does |
|---|---|
| `container(conn)` | Returns the `Container` stored on the app. |
| `current_user(request, c)` | Reads `Authorization: Bearer ...` and returns `AccessClaims` (401 if bad). |
| `require(permission)` | Returns a dependency that also checks the user's role has `permission` (403 otherwise). |
| `VIEW` | Ready-made `Depends(require(VIEW_PLANT))`. |
| `ws_user(ws, token, permission)` | WebSocket auth via `?token=`; closes with 4401 (not logged in) or 4403 (not allowed) and returns `None` when the caller must stop. |

#### `routers/write_guard.py`

| Name | What it does |
|---|---|
| `authorize_write(c, claims, body, kind)` | For the two REST write endpoints: checks the approval token covers exactly this draft and spends it. On failure audits `{kind}_rejected` and raises 403. |

#### `routers/auth.py` — `/auth`

| Function | Route | What it does |
|---|---|---|
| `auth_config` | `GET /auth/config` | No login needed. Tells the UI whether to show a login form (`local`) or redirect to Keycloak. |
| `login` | `POST /auth/token` | Local mode only: username + password → access token and user. 501 in Keycloak mode. |
| `me` | `GET /auth/me` | The logged-in user with role, plants and permissions. |

#### `routers/plants.py` — `/plants`

| Function | Route | What it does |
|---|---|---|
| `list_plants` | `GET /plants` | Plants the user may see. |
| `get_line` | `GET /plants/{plant}/lines/{line}` | One line with its stations and assets; 403 if the plant is out of scope, 404 if unknown. |
| `_missing` | — | Raises `KeyError` so the handler returns 404. |

#### `routers/ot.py` — `/ot`

| Function | Route | What it does |
|---|---|---|
| `signals` | `GET /ot/assets/{id}/signals` | Signal history for an asset (`signals`, `minutes` filters). |
| `alarms` | `GET /ot/alarms` | Alarms (active by default), optionally for one plant. |
| `stream` | `WS /ot/stream` | One `TelemetryMessage` per asset per second for a line (default L3). |

#### `routers/erp.py` — `/erp`

| Function | Route | What it does |
|---|---|---|
| `part` | `GET /erp/parts/{sku}` | Stock info for a part. |
| `supplier` | `GET /erp/suppliers/{id}` | Supplier details. |
| `create_transfer` | `POST /erp/stock-transfers` | Creates a stock transfer; needs a matching approval token. Audited. |

#### `routers/mes.py` — `/mes`

| Function | Route | What it does |
|---|---|---|
| `orders` | `GET /mes/orders` | Production orders, optionally per line. |
| `schedule` | `GET /mes/lines/{id}/schedule` | Line schedule. |
| `oee` | `GET /mes/lines/{id}/oee` | OEE figures. |

#### `routers/maintenance.py` — `/maintenance`

| Function | Route | What it does |
|---|---|---|
| `history` | `GET /maintenance/assets/{id}/history` | Work orders of an asset (including ones created in this run). |
| `create_work_order` | `POST /maintenance/work-orders` | Creates a work order; needs a matching approval token. Audited. |

#### `routers/quality.py` — `/quality`

| Function | Route | What it does |
|---|---|---|
| `defects` | `GET /quality/lines/{id}/defects` | Defect summary. |
| `inspections` | `GET /quality/inspections?line_id=` | Inspections of a line. |

#### `routers/engineering.py` — `/engineering`

| Function | Route | What it does |
|---|---|---|
| `bom` | `GET /engineering/assets/{id}/bom` | Bill of materials. |
| `doc_search` | `POST /engineering/docs/search` | Manual/document search (fixture until P2's RAG exists). |

#### `routers/workforce.py` — `/workforce`

| Function | Route | What it does |
|---|---|---|
| `shifts` | `GET /workforce/shifts?plant_id=` | Shift roster (plant scope enforced). |
| `technicians` | `GET /workforce/technicians` | Technicians, filter by `skill` and `on_shift`. |

#### `routers/energy.py` — `/energy`

| Function | Route | What it does |
|---|---|---|
| `consumption` | `GET /energy/lines/{id}/consumption` | Energy use of a line. |
| `tariffs` | `GET /energy/tariffs` | Tariff bands. |
| `co2_factor` | `GET /energy/co2-factor` | CO2 factor. |

#### `routers/ml.py` — `/ml`

| Function | Route | What it does |
|---|---|---|
| `health` | `GET /ml/assets/{id}/health` | Latest health of an asset. |
| `predict` | `POST /ml/predict` | Scores a supplied feature window. |
| `models` | `GET /ml/models` | Loaded model info. |

#### `routers/incidents.py` — `/incidents`

| Function | Route | What it does |
|---|---|---|
| `create_incident` | `POST /incidents` | Opens an incident (needs `RUN_AGENTS`); returns the existing one if the asset already has an active incident. |
| `get_incident` | `GET /incidents/{id}` | The incident. |
| `events` | `GET /incidents/{id}/events` | Stored agent events after `after_seq` (REST catch-up). |
| `stream` | `WS /incidents/{id}/stream` | Agent events in order until the orchestrator sends `finished`. The first viewer starts the agent run (`speed` param); later viewers and reconnects replay what was stored. |
| `options` | `GET /incidents/{id}/options` | Ranked options; 404 until the analysis has produced them. |
| `approve` | `POST /incidents/{id}/approve` | Approve or reject an option (needs `APPROVE`). Approving executes its actions. |
| `feedback` | `POST /incidents/{id}/feedback` | Technician verdict (needs `SUBMIT_FEEDBACK`). |

#### `routers/audit.py` — `/audit`

| Function | Route | What it does |
|---|---|---|
| `audit_list` | `GET /audit` | Audit entries newest first, paged with `cursor` (needs `VIEW_AUDIT`). |
| `audit_verify` | `GET /audit/verify` | Rechecks the hash chain and reports where it breaks, if it does. |

#### `routers/security.py` — `/security`

| Function | Route | What it does |
|---|---|---|
| `redactions` | `GET /security/redactions` | Redaction reports, newest first. |

#### `routers/simulator.py` — `/simulator`

| Function | Route | What it does |
|---|---|---|
| `scenario` | `POST /simulator/scenario/{name}` | Switches the demo scene (needs `RUN_SIMULATOR`) and audits it. |

---

### 3.4 `app/services/` — business logic

#### `auth.py` — who are you, what may you do

| Name | What it does |
|---|---|
| `ACCESS_TTL` | Local access tokens live 15 minutes. |
| `user_from_claims(claims)` | Token claims → `User` (with permission list from `ROLE_PERMISSIONS`). |
| `Authenticator.authenticate(token)` | Bearer token → `AccessClaims` in both auth modes; 401 if invalid. |
| `LoginService.login(req)` | Local mode: checks username/password (constant-time compare), audits success or failure, returns token + user. 501 in Keycloak mode. |
| `AccessPolicy.require(claims, permission)` | Role must have the permission; otherwise audits `…_denied` and raises 403. |
| `AccessPolicy.check_plant(claims, plant_id)` | 403 `PLANT_SCOPE` unless the user may see the plant. |

#### `approval_tokens.py` — permission slips for writes

| Name | What it does |
|---|---|
| `APPROVAL_TTL` | 10 minutes. |
| `ACTION_FOR_TOOL` | `create_work_order` → `work_order`, `create_stock_transfer` → `stock_transfer`. |
| `ApprovalError` | Exception with an error code (`APPROVAL_REQUIRED` / `APPROVAL_INVALID`). |
| `ApprovalTokenService.issue(approver, incident, option, actions)` | Creates an HS256 token tied to that incident, option and set of action kinds. |
| `ApprovalTokenService.verify(token, incident, action)` | Checks signature, type, expiry, incident and action coverage. Does not spend it. |
| `ApprovalTokenService.consume(claims, action)` | Marks (token id, action) as used; a second use raises an error. |

#### `write_authorizer.py` — may this write happen?

| Name | What it does |
|---|---|
| `ApprovedOptions` | Protocol: `approved_option(incident_id, option_id)`. |
| `WriteAuthorizer.authorize(token, incident, kind, payload, consume)` | Verifies the token, finds the approved option, and requires the draft being written to equal that option's draft for this action kind. Optionally spends the token. |

#### `audit_log.py` — tamper-evident log

| Name | What it does |
|---|---|
| `GENESIS` | The "previous hash" of the first entry (64 zeros). |
| `canonical_json(obj)` | Stable JSON so the same entry always hashes identically. |
| `entry_hash(prev_hash, entry)` | sha256 of previous hash + the entry's canonical JSON. |
| `HashChainAuditLog.log(...)` | Appends an entry linked to the previous hash; thread-safe. |
| `HashChainAuditLog.verify()` | Recomputes the chain; returns `(valid, checked, first_broken_seq)`. |
| `HashChainAuditLog.list(incident_id, limit, before_seq)` | A page of entries, newest first, with total and next cursor. |

#### `redaction_log.py`

| Name | What it does |
|---|---|
| `RedactionLog.add(report)` | Stores a report from P2's redactor (keeps the latest 500). |
| `RedactionLog.list()` | All stored reports, newest first. |

#### `incident_service.py` — the incident life cycle

| Name | What it does |
|---|---|
| `SYSTEM_ACTOR` | The actor name used when the system opens an incident. |
| `IncidentService.open(req, actor, claims)` | Finds where the asset is, checks plant scope, returns the existing active incident if any, otherwise creates one (severity from current risk), audits and publishes it. |
| `IncidentService.get(id, claims)` | The incident after the plant-scope check. |
| `IncidentService.scoped_record(id, claims)` | The full record (events, options...) after the same check. |
| `IncidentService.options(id, claims)` | The ranked options; 404 while analysis is still running. |
| `IncidentService.decide(id, req, approver)` | 409 unless AWAITING_APPROVAL. Flips state first (so a double-click cannot run twice). Approve: issues a token, runs the actions, status EXECUTED if all succeeded (stays APPROVED if one failed). Reject: status REJECTED. Audits, publishes status, notifies the runtime. |
| `IncidentService.feedback(id, req, user)` | Only after EXECUTED/REJECTED and only once. Computes new similar-case confidence, marks RESOLVED, audits, notifies the runtime. |
| `IncidentService.reset()` | Deletes all incidents (simulator calm/reset). |
| `IncidentService.resolve_active()` | Marks all incidents RESOLVED (simulator `resolved`). |
| `IncidentService._scoped(id, claims)` | Loads a record (404 if missing) and enforces plant scope. |

#### `run_coordinator.py` — runs the agents and records events

| Name | What it does |
|---|---|
| `IDLE_TIMEOUT_S` | A viewer waits at most 120 s for the next event. |
| `is_final(event)` | True for the orchestrator's `finished` status event. |
| `RunCoordinator.ensure_started(id, speed)` | Starts the agent run as a background task if not started yet (first viewer wins). |
| `RunCoordinator._run(id, speed)` | Drives the runtime, recording each event. If it crashes: audit, ERROR event, then `finished`, so viewers never hang. |
| `RunCoordinator._record(rec, event)` | Stores the event; copies a Diagnosis or OptionsResponse onto the incident; on `finished` moves ANALYSING → AWAITING_APPROVAL; publishes event (and status) on the bus. |
| `RunCoordinator.events(id, after_seq)` | Stored events after a sequence number. |
| `RunCoordinator.follow(id, after_seq, idle_timeout_s)` | Async stream: stored events first, then live ones, until `finished`. Subscribes before reading history so nothing is missed in between. |

#### `agent_runtime.py` — who produces agent events

| Name | What it does |
|---|---|
| `RECORDED_INCIDENT_ID` | `INC-0042`, the id inside the recording. |
| `RuntimeFactory` | Type: speed → runtime. |
| `ReplayAgentRuntime.run(incident)` | Replays the recorded events, rewriting `INC-0042` to this incident's id, waiting the original gaps divided by `speed`, stamping the current time. |
| `ReplayAgentRuntime.on_feedback / on_approval` | Do nothing (a replay does not learn). |
| `FallbackAgentRuntime.run(incident)` | Streams the primary runtime; if it raises, emits an ERROR event (`LLM_UNAVAILABLE`) and continues with the fallback, with sequence numbers continuing. |
| `FallbackAgentRuntime.on_feedback / on_approval` | Forward to the primary runtime. |

#### `action_executor.py` — executes an approved option

| Name | What it does |
|---|---|
| `EXECUTOR` | Actor name `"executor"` used in audit and tool calls. |
| `ExecutionContext` | incident id, approval token, approver id. |
| `ActionHandler` | Protocol: `kind` and `execute(action, ctx)`. |
| `SpeedChangeHandler.execute` | Sets the line speed through MES and audits it. |
| `ToolWriteHandler.execute` | Calls the write tool (`create_work_order` or `create_stock_transfer`) through the ToolGateway with the draft and token, audits, returns the new record id. |
| `ActionExecutor.execute(option, ctx)` | Runs every action with the matching handler. Unknown kind or exception becomes a failed result for that action; the rest still run. |

#### `tool_gateway.py` — the only door for agents

| Name | What it does |
|---|---|
| `ToolTimeout` | Raised when a tool exceeds its time. |
| `ToolGateway.call(agent, tool, args, incident_id)` | Denies unknown tools or agents not on the tool's allow-list (audited); validates arguments; for write tools checks approval; runs with a timeout; validates the result; always writes an audit entry (never including the approval token). |
| `ToolGateway._authorize_write(...)` | Draft's `incident_id` must match the caller and an approval must cover exactly this draft. |
| `ToolGateway._invoke(handler, inp, timeout)` | Runs async handlers directly and sync ones in a thread, under the timeout. |

#### `tool_handlers.py` — tool name → backend call

| Name | What it does |
|---|---|
| `ToolBackends` | Bundle of all data sources the tools use. |
| `build_handlers(backends)` | Dictionary of 17 tools (15 reads, 2 writes). A new tool = one line here + one entry in `plantguard_contracts/tools.py`. |
| `to_plain(result)` | Models/lists → plain dicts; a bare list becomes `{"items": [...]}`. |

#### `confidence_updater.py`

| Name | What it does |
|---|---|
| `ConfidenceUpdater.updated(confidence, confirmed)` | Confirmed: moves 55 % of the way to 1.0 (0.60 → 0.82). Rejected: halves it. `None` stays `None`. |

#### `incident_lifecycle.py` — automatic incident open/close

| Name | What it does |
|---|---|
| `SceneIncidentLifecycle.on_scene(scenario)` | calm/reset → clear incidents; warning/crisis/full_story → open the hero asset's incident; resolved → resolve active ones. |
| `AlertIncidentOpener.run()` | Background loop: for every `alerts.raised` message, opens an incident. Errors are logged, not fatal. |

#### `ot_gateway.py` — trust level of telemetry

| Name | What it does |
|---|---|
| `STALE_AFTER_S` | 5 seconds. |
| `TelemetrySource` | Protocol: `subscribe(plant, line)` → stream of messages. |
| `QualityPolicy.annotate(msg, now)` | Marks a good message `stale` if older than 5 s, or `out_of_range` if any value is outside the tag-map range. |
| `StalenessWatchdog.observe(msg)` | Remembers the latest message per asset. |
| `StalenessWatchdog.stale(now)` | Stale copies for assets that have been silent too long (once each). |
| `OTGateway.stream(plant, line)` | Source stream with quality applied. One-way: nothing writes to machines. |

#### `scoring.py` — ML health scoring loop (off by default)

| Name | What it does |
|---|---|
| `ALERT_RISK`, `DEBOUNCE`, `MIN_SAMPLES` | 0.7 risk threshold, 30 min between alerts per asset, 10 readings before scoring. |
| `severity_for(risk)` | ≥ 0.7 high, ≥ 0.3 medium, else low. |
| `HealthScorer.ingest(msg)` | Adds a GOOD reading to the asset's buffer and drops readings older than the window. |
| `HealthScorer.evaluate()` | For each asset with enough readings: extract features → model score → cache → publish `ml.health.{asset}` → maybe alert. |
| `HealthScorer._maybe_alert(health)` | Publishes `alerts.raised` if risk ≥ 0.7 and none in the last 30 min. |
| `ScoringService.run()` | Starts one ingest task per line plus the scoring and staleness loops. |
| `ScoringService._ingest(plant, line)` | Per message: watch staleness, feed the scorer, publish `ot.telemetry.*`. |
| `ScoringService._score_loop()` | Every interval runs `evaluate()`; one failed cycle is logged and skipped. |
| `ScoringService._stale_loop()` | Every second publishes stale copies for silent assets. |

#### `scene_controller.py` — the demo scene

| Name | What it does |
|---|---|
| `CALM_SCENES` | calm, reset, resolved. |
| `SceneController.scenario / changed_at / is_calm()` | Current scene, when it changed, whether everything should look green. |
| `SceneController.add_listener(fn)` | Registers a callback run on each change. |
| `SceneController.set(scenario)` | Changes scene, publishes `sim.scene`, runs the listeners in order. |

#### Small helpers

| File | Name | What it does |
|---|---|---|
| `plant_directory.py` | `AssetLocation` | plant, line, asset id, asset name. |
| | `PlantDirectory.locate(asset_id)` | Where an asset is; `KeyError` (404) if unknown. |
| | `PlantDirectory.line_assets(line_id, plant_ids)` | Plant and asset ids of a line (line ids repeat across plants, so allowed plants are passed in). |
| | `PlantDirectory.plant_of_asset(asset_id)` | Plant id of an asset. |
| `line_speeds.py` | `LineSpeeds.get/set/reset` | Speed percent per line (default 100); set after an approved speed change. |

---

### 3.5 `app/repositories/` — storage behind Protocols

#### `incident_repository.py`

| Name | What it does |
|---|---|
| `ACTIVE` | Statuses counted as "still active": open, analysing, awaiting approval, approved. |
| `IncidentRecord` | Incident + events + options + approval + feedback + `run_started`. |
| `IncidentRepository` | Protocol: `next_id`, `add`, `get`, `find_active`, `all`, `clear`, `approved_option`. |
| `InMemoryIncidentRepository` | Thread-safe dict implementation (lost on restart). |
| `…find_active(asset_id)` | The active incident of an asset, if any. |
| `…approved_option(incident, option)` | The option only if that exact option was approved and the incident is APPROVED/EXECUTED. Used by `WriteAuthorizer`. |

#### `user_directory.py`

| Name | What it does |
|---|---|
| `UserDirectory.find(username)` | Protocol. |
| `FixtureUserDirectory.find(username)` | Reads `fixtures/story/users.json`. Local mode only. |

---

### 3.6 `app/connectors/` — plant data sources

#### `store.py`

| Name | What it does |
|---|---|
| `FixtureStore.load(name)` | Reads `fixtures/story/{name}.json`. Names are validated against a safe pattern, so request data cannot escape the folder. Unknown or unsafe → `KeyError` → 404. |

#### `fixtures.py` — one class per domain

Each class implements a Protocol from `plantguard_contracts.interfaces`. Replace any of them with a real connector in `container.py`.

| Class.method | What it does |
|---|---|
| `FixturePlants.list_plants()` | Plant tree; in calm scenes forces health OK and risk 0.04. |
| `FixtureOT.get_signals(asset, signals, minutes)` | Stored signal history cut to the last N minutes (5-minute samples). |
| `FixtureOT.get_alarms(plant, active)` | Alarms; none in calm/reset; filter by plant and acknowledged flag. |
| `FixtureOT.subscribe(plant, line)` | Live stream from the OT gateway. |
| `FixtureERP.get_part / get_supplier` | Part and supplier from fixtures. |
| `FixtureERP.create_stock_transfer(req)` | Standard: EUR 350, 20 h. Express: EUR 900, 6 h. Ids from STO-4500012. |
| `FixtureERP.reset()` | Clears created transfers. |
| `FixtureMES.get_orders / get_schedule / get_oee` | Orders, schedule and OEE from fixtures. |
| `FixtureMES.set_line_speed(...)` | Stores the speed so the telemetry source follows it. |
| `FixtureMaintenance.get_history(asset)` | Historic work orders + ones created in this run. |
| `FixtureMaintenance.create_work_order(req)` | Creates a scheduled work order (ids from WO-2026-0153). |
| `FixtureMaintenance.reset()` | Clears created work orders. |
| `FixtureQuality.get_defects / get_inspections` | Defect summary; inspections (empty list). |
| `FixtureEngineering.get_bom(asset)` | Bill of materials. |
| `FixtureRetriever.search(req)` | Always returns the bearing-manual fixture (P2's RAG replaces it). |
| `FixtureWorkforce.get_shifts / find_technicians` | Shifts per plant; technicians by skill / on-shift. |
| `FixtureEnergy.get_consumption / get_tariffs / get_co2_factor` | Energy data. |
| `_parse(type, data)` | Validates raw JSON into typed models. |

#### `health.py`

| Name | What it does |
|---|---|
| `FixtureHealthProvider.get_health(asset)` | Health from fixtures; calm scene returns a healthy reading. |
| `FixtureHealthProvider.predict / models` | Fixture answers. |
| `LatestScores.put / get` | Thread-safe cache of the newest score per asset. |
| `LiveHealthProvider.get_health(asset)` | Newest live score, else the fixture fallback. |
| `LiveHealthProvider.predict / models` | Straight to the real model. |

#### `telemetry/synthetic.py`

| Name | What it does |
|---|---|
| `SyntheticTelemetrySource.subscribe(plant, line)` | Endless stream: one message per asset every interval. |
| `SyntheticTelemetrySource.sample(asset, line)` | One reading. The hero asset ramps vibration 2.1 → 4.3 mm/s and bearing temperature +8 °C over 60 s in warning scenes; lower line speed reduces load, power and vibration growth. |

#### `telemetry/tag_map.py`

| Name | What it does |
|---|---|
| `Tag` | One PLC tag: asset, signal, unit, protocol, valid range. |
| `TagMap.load(path)` | Reads `config/tag_map.yaml`. |
| `TagMap.resolve(raw_tag)` | Tag for a raw PLC name. |
| `TagMap.range_of(asset, signal)` | `(low, high)` valid range. |

---

### 3.7 `app/middleware/` and `app/infrastructure/`

#### `middleware/rate_limit.py`

| Name | What it does |
|---|---|
| `TokenBucketLimiter.acquire(key)` | Takes one token; returns 0 if allowed, otherwise seconds to wait. |
| `RateLimitMiddleware.__call__` | HTTP only (WebSockets pass). Key = Authorization header, else client IP. Over the limit → `429 RATE_LIMITED` with `Retry-After`. |

#### `infrastructure/event_bus.py`

| Name | What it does |
|---|---|
| `InMemoryEventBus.publish(channel, message)` | Delivers the message to every subscriber of the channel. |
| `InMemoryEventBus.subscribe(channel)` | Registers immediately and returns an async iterator (so nothing published afterwards is missed). |
| `Subscription.__anext__ / deliver / close` | Wait for next message; bus puts a message in; stop listening. |

Channels in use: `ot.telemetry.{plant}.{line}`, `ml.health.{asset}`, `alerts.raised`, `incident.{id}.events`, `incident.{id}.status`, `sim.scene`.

---

## 4. Tests

Run `make test-p1-unit` for these and `make test-p1` for the shared contract tests. Story fixtures stay in the repo-level `fixtures/story/` (shared by every team); `tests/data/` only holds small payloads these tests build on.

| Path | Covers |
|---|---|
| `conftest.py` | Fixtures `container` (fresh backend), `client` (TestClient), `users` (logged-in op, lead, manager, admin). |
| `helpers.py` | `login(client, user)`; `run_story(client, users, speed)` runs reset → warning → open incident → agents finish. |
| `data/__init__.py` | `NOW` (fixed timestamp) and `WORK_ORDER_DRAFT` (sample draft). |
| `unit/test_audit_log.py` | Tampering, deleted entries, newest-first paging. |
| `unit/test_approval_tokens.py` | Scope, expiry, single use, missing token. |
| `unit/test_rate_limit.py` | Burst, block, refill. |
| `unit/test_tool_gateway.py` | Allow-list, every read tool, bad args, timeout, write approval, token never in audit. |
| `unit/test_action_executor.py` | A failed action is reported and the rest still run. |
| `unit/test_agent_runtime.py` | Fallback to replay when the LLM fails. |
| `unit/test_ot_gateway.py` | `out_of_range` / `stale` flags, watchdog. |
| `unit/test_telemetry_source.py` | Synthetic telemetry follows scene and line speed. |
| `unit/test_scoring.py` | Health published, alert debounce, low risk and bad quality ignored (uses fake model/extractor/bus). |
| `integration/test_security_api.py` | Forged/mismatched/replayed writes, plant scope, audit, 429, path traversal, login failure. |
| `integration/test_incident_flow.py` | Golden numbers, approve/reject/feedback rules, idempotent open, reconnect catch-up. |
| `integration/test_keycloak_mode.py` | Keycloak-shaped tokens against the real backend (RBAC, scope, WebSocket). |

---

## 5. Common questions

**Where do I add a new endpoint?** A new function in the matching `routers/*.py` file (or a new router file, then include it in `main.py`). Call a service or connector; keep rules out of the router.

**Where do I add a new tool for agents?** One entry in `plantguard_contracts/tools.py` and one line in `services/tool_handlers.py::build_handlers`.

**Where do I add a new approvable action kind?** A handler class in `services/action_executor.py` and add it to the `ActionExecutor([...])` list in `container.py`.

**Why does `container.py` import so many things?** Because it is the only place allowed to know every concrete class. Everything else depends on Protocols, so it can be tested and swapped without touching callers.

**Which data is lost on restart?** Incidents, the audit log, the event bus, created work orders and stock transfers: all are in memory today. The Postgres, Redis and Qdrant containers in `docker-compose.yml` are not used by the backend yet.
