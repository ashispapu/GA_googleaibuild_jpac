# P1 backend — folder and file guide

What every folder, file and function in `p1-backend/` does. For tasks and status see `README.md`.

Every function and class also has a docstring in the code, so hovering over it in the editor shows the same text.

## Contents

1. [Big picture](#1-big-picture)
2. [`container.py` explained step by step](#2-containerpy-explained-step-by-step) ← start here if the wiring is confusing
3. [Reference: every file and function](#3-reference-every-file-and-function)
   - [app/ top level](#31-app-top-level) · [core/](#32-appcore) · [routers/](#33-approuters) · [services/](#34-appservices) · [repositories/](#35-apprepositories) · [connectors/](#36-appconnectors) · [middleware/ and infrastructure/](#37-appmiddleware-and-appinfrastructure)
4. [Tests](#4-tests)
5. [Common questions](#5-common-questions)

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
