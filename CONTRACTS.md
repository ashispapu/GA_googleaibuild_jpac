# PlantGuard AI — Contracts v1.2

**v1.2 (additive, current).** The backend now runs a **live simulated plant** (`DATA_MODE=sim`, default) instead of only
the scripted INC-0042 story, and an incident no longer ends at approval: a **technician** does the repair, the machine is
checked, and only that technician closes it. Everything below marked *v1.2* is new; v1 / v1.1 rules still hold.
The scripted story (`DATA_MODE=fixtures`) stays for tests, the mock server and the C9 golden numbers.

Code is the source of truth (`plantguard_contracts/`). This file explains it. If they disagree, the code wins and this file gets fixed.

Owners: **BE** Backend core · **AG** Agents & RAG · **FE** Frontend · **ML** ML & Data

| # | Contract | Provider → Consumer | Code |
|---|---|---|---|
| C1 | REST API | BE → FE, AG | `generated/openapi.json`, `generated/api.d.ts` |
| C2 | Realtime streams (WS) | BE → FE | `schemas/ot.py:TelemetryMessage`, `schemas/incidents.py:AgentEvent`, `generated/ws.ts` |
| C3 | Agent runtime | AG → BE | `interfaces.py:AgentRuntime` → implemented in `p2-agents/agents/runtime.py` |
| C4 | Tools | BE → AG | `tools.py`, `generated/tools.json` |
| C5 | ML model | ML → BE | `interfaces.py:FeatureExtractor, PredictiveModel`, `schemas/ml.py` |
| C6 | OT data feed | ML (simulator) → BE (gateway) | `config/tag_map.yaml`, `schemas/ot.py` |
| C7 | Security | BE → all | `schemas/security.py` |
| C8 | Event bus | all | `schemas/bus.py` |
| C9 | Golden data | ML → all | `fixtures/build_fixtures.py`, `fixtures/story/` (fixtures mode only) |
| C10 *v1.2* | Simulated plant & data model | BE → all | `config/simulation.ini`, `config/plant.yaml`, `p1-backend/app/sim/` |
| C11 *v1.2* | Repair workflow | BE → FE, AG | `schemas/workflow.py`, `p1-backend/app/services/repair_workflow.py` |

---

## C0. Conventions (everyone)

- Time: UTC, ISO-8601 with `Z`/offset. *v1.2*: the UI shows **plant time** (`NEXT_PUBLIC_PLANT_TZ`, default UTC = the
  simulated plant's clock), not the viewer's zone, so it matches shifts, work orders and the agents' wording.
- Money: `{"amount": 85.0, "currency": "EUR"}`. Units are in field comments (`mm/s`, `°C`, `kW`, `kWh`, `kg`).
- Probabilities: `0..1` floats. The UI shows them as percentages.
- *v1.2*: line IDs repeat across plants (`L1` exists in Plant A and B). Anything addressed by line also carries `plant_id`
  (query parameter on /mes, /energy, /quality, /workforce; `plant_id` in tool args; the ToolGateway fills it from the
  incident when an agent leaves it out). Asset IDs stay globally unique.
- IDs (regex enforced): `PLANT-A`, `L3`, `L3-ST2`, `EQ-3012`, `SKF-6205`, `ORD-5521`, `WO-2024-0871`, `INC-0042`, `OPT-B`, `TECH-104`, `USR-lead`, `STO-4500012`.
- Unknown fields are **rejected** (`extra="forbid"`), so a typo fails fast instead of silently.
- Health bands: `ok` < 0.3 ≤ `warning` < 0.7 ≤ `critical` (from `risk_72h`).
- Confidence < **0.70** ⇒ `needs_human_review = true`. Any write action always needs approval anyway.

## C1. REST API (BE → FE, AG)

- Base URL: `http://localhost:8000` (mock and real backend are the same).
- Auth: `POST /auth/token` → `access_token` (JWT, 15 min). Send `Authorization: Bearer <jwt>` on every call.
- Errors: always `ErrorResponse {code, message, trace_id, details?}`. FE switches on `code`, never on the message text.

| HTTP | code | FE behaviour |
|---|---|---|
| 401 | UNAUTHENTICATED | go to login |
| 403 | FORBIDDEN / PLANT_SCOPE | show lock badge "Not allowed for your role" |
| 403 | APPROVAL_REQUIRED / APPROVAL_INVALID | show the approval modal again |
| 404 | NOT_FOUND | empty state |
| 409 | CONFLICT | refresh incident |
| 429 | RATE_LIMITED | back off 2 s |
| 503 | LLM_UNAVAILABLE | banner "Running from recorded analysis" |

- Lists that can grow use `Page {items, total, next_cursor}` (alarms, audit). Small lists are plain arrays.
- Routers and the permission each needs:

| Router | Endpoints | Permission |
|---|---|---|
| /auth | POST /token, GET /me | — |
| /plants | GET /plants, GET /plants/{plant}/lines/{line} | view_plant + plant scope |
| /ot | GET /assets/{id}/signals, GET /alarms, WS /stream | view_plant |
| /erp | GET /parts/{sku}, GET /suppliers/{id}, POST /stock-transfers | view_plant; POST needs approval token |
| /mes | GET /orders, GET /lines/{id}/schedule, GET /lines/{id}/oee | view_plant |
| /maintenance | GET /assets/{id}/history, POST /work-orders | view_plant; POST needs approval token |
| /quality | GET /lines/{id}/defects, GET /inspections | view_plant |
| /engineering | GET /assets/{id}/bom, POST /docs/search | view_plant |
| /workforce | GET /shifts, GET /technicians | view_plant + plant scope |
| /energy | GET /lines/{id}/consumption, GET /tariffs, GET /co2-factor | view_plant |
| /ml | GET /assets/{id}/health, POST /predict, GET /models | view_plant |
| /incidents | POST, GET /{id}, GET /{id}/events, WS /{id}/stream, GET /{id}/options | run_agents / view_plant |
| /incidents | POST /{id}/approve | **approve** (lead, manager) |
| /incidents | POST /{id}/feedback | submit_feedback |
| /audit | GET, GET /verify | view_audit |
| /security | GET /redactions | view_plant |
| /simulator | POST /scenario/{name} | run_simulator (admin) — 409 in sim mode (the plant runs on its own) |

*v1.2 endpoints (all additive):*

| Router | Endpoints | Permission |
|---|---|---|
| /auth | POST /refresh (new token for the same server session), POST /logout (ends the session), GET /sessions[?all_users=true], DELETE /sessions/{sid}, GET /demo-users (no auth; sim + local mode only, else `[]`) | signed in; all_users needs view_audit; ending someone else's session needs admin |
| /incidents | GET `?scope=open\|done\|all&kind=incident\|question\|all&mine=&plant_id=&parent_id=` → `Page[IncidentOverview]`, newest activity first | view_plant + plant scope |
| /incidents | GET /log → `MaintenanceLogEntry[]` (closed incidents with the fix that worked) | view_plant |
| /incidents | GET /{id}/overview, /{id}/timeline, /{id}/context, /{id}/job | view_plant |
| /incidents | POST /{id}/work/start, /{id}/work/note, /{id}/work/complete | **perform_work, assigned technician only** |
| /incidents | POST /{id}/resolve (after the machine check passed) | **perform_work, assigned technician only** |
| /incidents | POST /{id}/reanalyse (after `repair_failed`) | perform_work (assignee) or approve |
| /incidents | POST (create) also takes `parent_id`: a question about an open incident | run_agents |
| /incidents | GET /signoffs → `SignoffTask[]` (approval steps waiting for you) | view_plant |
| /incidents | GET /{id}/options/chat → `OptionChatMessage[]`; POST /{id}/options/chat `{message}` → `OptionChatResponse` (only while `awaiting_approval`, else 409) | view_plant / **approve** + own plant |
| /engineering | GET /docs[?kind=manual\|work_order], GET /docs/{id} (sections with page numbers) | view_plant |
| /garden | GET /garden → `GardenSnapshot`, WS /garden/stream → `GardenAgent` frames | view_plant (own plants only) |
| /simulator | GET /status → `SimStatus`; POST /faults `{asset_id?, failure_mode?}`; POST /speed `{scale}` (0 pauses) | status: view_plant; faults/speed: run_simulator |

## C2. Realtime streams (BE → FE)

- `WS /ot/stream?token=<jwt>&line_id=L3`: one `TelemetryMessage` per asset per second.
- `WS /incidents/{id}/stream?token=<jwt>&speed=1`: `AgentEvent` frames in `seq` order. The server closes the socket after the orchestrator sends `finished`.
- Reconnect: call `GET /incidents/{id}/events?after_seq=<last seq>` to catch up, then reopen the socket. FE de-duplicates events by `seq`.
- Rendering rules for FE: `THOUGHT` becomes a thinking card, `TOOL_CALL` a chip with the tool name, `EVIDENCE` a clickable card (`evidence.link` is the UI route), `RESULT` a summary card per agent, `STATUS started/finished` lights the agent node on and off.
- **v1.1 (additive) – live streaming and hand-offs.** New `EventType`s and optional `AgentEvent` fields; old recordings stay valid.
  - `thought_delta` / `message_delta`: chunks of the model's live reasoning / answer. Join chunks with the same `stream_id` in `seq` order. The stream is closed by exactly one regular event with the **same `stream_id`**: a `THOUGHT` (≤ 200 chars, plain language) or a `MESSAGE`. FE replaces the live card with the closing event. A client that ignores deltas still sees the v1 shape.
  - `message`: an agent's full answer text (Ask panel, hand-off answers, coordinator summary). Not limited to 200 chars.
  - `handoff`: the task is **diverted** to another agent, `args = {"to": <agent>, "task": <text>, "reason"?}`. The diverting agent then says the task is with the other agent (a `MESSAGE`) and stops; the target runs next and answers. The Ask panel shows the target's answer.
  - `sub_agent`: set when a helper sub-agent (e.g. `manual_researcher`) works on behalf of `agent`; it reports back to its caller. FE shows "Diagnosis › Manual researcher" and does not change the node state for these events. Contract agents used as sub-agents emit under their own `agent` with `STATUS started/finished` and no `RESULT`.
  - `Incident.question` (optional): the question of a `trigger=user_question` incident. A question with text always opens its own incident (thread); question threads don't block the asset's alert incident.

- *v1.2*: `WS /garden/stream?token=`: one `GardenAgent` per move (incident id, the agent, the system **node** it is using —
  `erp`, `cmms`, `vdb`, `llm`, `historian`, `lead`, `technician` …, a plain-words `activity`, the `stage`, a stable
  `color` index per incident). The snapshot comes first. FE keys tokens by incident id so they move, never re-mount.
- *v1.2*: `AgentEvent.round` (default 1). An incident can be analysed again after a failed repair; only the **current
  round's** orchestrator `finished` ends the stream (`isRoundFinished`). Earlier rounds stay in the history.

## C3. Agent runtime (AG → BE)

```python
class AgentRuntime(Protocol):
    def run(self, incident: Incident) -> AsyncIterator[AgentEvent]: ...
    async def on_feedback(self, incident, feedback) -> None: ...
    async def on_approval(self, incident, approval) -> None: ...
```
- BE calls `run()` when an incident opens and publishes every event to `incident.{id}.events`.
- Agent order: orchestrator → monitor → diagnosis → spare_parts → scheduler → energy → decision → orchestrator `finished`.
- Each agent emits `STATUS started`, then 1–6 `THOUGHT`/`TOOL_CALL`/`TOOL_RESULT`/`EVIDENCE` events, then exactly one `RESULT`.
- RESULT payload type by agent: monitor → `MonitorResult`, diagnosis → `Diagnosis`, spare_parts → `SparePartsResult`, scheduler → `SchedulerResult`, energy → `EnergyResult`, decision → `OptionsResponse`.
- `THOUGHT.message` is plain language, ≤ 200 characters, with no raw JSON.
- The whole run takes < 60 s. Each tool call takes < 3 s (the gateway times out after that).
- **Costs and numbers are computed in code, not by the LLM.** The LLM chooses tools and writes explanations. The Decision agent's cost model is deterministic Python, so the demo shows the same numbers every time.
- If the LLM fails, replay `fixtures/story/agent_events_INC-0042.jsonl` and set `LLM_UNAVAILABLE` on the incident.
- Configuration: everything about the agents (flows per trigger, agents, tools, sub-agents, hand-off targets, model, thinking, streaming, business numbers) is in `p2-agents/config/agents.ini` + `p2-agents/agents/<name>/agent.ini`. LLM calls go through LiteLLM (Gemini by default, key in `GEMINI_API_KEY`). Without the key the backend keeps the recorded replay.
- Agents **never** call write tools. The backend executor runs the approved option's `actions`.
- *v1.2* LLM failure in sim mode: `on_llm_failure = degrade` — the run **finishes on rules** (same tools, deterministic
  wording from `agents.ini` policies) and emits one `ERROR LLM_UNAVAILABLE`; it never replays the recorded INC-0042 run.
  Rate limits are retried with exponential backoff; a **daily-quota** 429 pauses that model for everyone until the
  server's retry time (`AI paused until …`), so later agents go rule-based at once with no network call.
- *v1.2* rounds: `Incident.round`, `excluded_failure_modes` (a mode is excluded only if its recommended part was
  actually fitted and the check still failed), `previous_findings` (the technician's notes). Diagnosis skips excluded
  modes and says why (`retry_note`). Planning picks the technician by the **skill** the failure mode needs
  (`policy_map`) and prefers stock at the incident's own plant before a transfer.
- *v1.2* **options chat** (`agents/decision/refine.py`, `PlantAgentRuntime.refine_options`). An approver's message →
  `PlanRequest {timing now|shift|keep_running, shift, day_offset, speed_pct, source, technician}` (LLM tool call
  `plan_request` when available, else keyword rules; merged with the earlier messages of the thread, "start over" resets).
  Live data is re-read through the tool gateway (as decision / spare_parts / scheduler / energy), the option is built with
  the **same** `build_option` + `CostModel`, risk until repair = survival over the waiting time with wear ∝ speed^1.5.
  It is appended as the next letter with `origin="approver"`, `suggested_by`, `recommended=false`; the AI's options are
  never changed; an identical plan is not added twice; max 12 options. Thread stored per incident and round (`option_chat`).
- *v1.2* **purchase orders**: `PurchaseOrderAction {kind: purchase_order, draft: PurchaseOrderDraft}`. With
  `[policy.planning] purchase_orders = true` (always on in sim mode) an option that needs the supplier carries one:
  "keep running" orders the part and plans the swap on arrival; "stop now" / "slow then swap" fall back to the supplier when
  no plant has stock. The executor's `PurchaseOrderHandler` checks the approval token like a write tool, creates
  `PO-…` in ERP; the plant engine books the goods receipt (`stock_movements.reason=purchase_receipt`) at the ETA.
- *v1.2* questions: `parent_id` links a question to its incident; the orchestrator calls `get_incident` first, so answers
  know the diagnosis, approved option, technician notes and check result.

## C4. Tools (BE → AG)

- 18 tools are listed in `tools.py` (*v1.2*: `get_incident` → `IncidentContext`, for orchestrator/diagnosis/decision;
  `plant_id` added to `LineArgs` and `TechArgs`). Each has a typed input, a typed output, an endpoint and an **agent allow-list**.
- `ToolGateway.call(agent, tool, args, incident_id)` checks the allow-list and validates input and output against the schemas. It also writes an audit entry and applies the timeout.
- Write tools (`create_work_order`, `create_stock_transfer`) have `writes=True` and only the `executor` may call them. They require an approval token.
- Function-calling schemas for the LLM: `generated/tools.json` or `openai_tool_schema(spec)`. Agents call models only through `p2-agents/llm/client.py: chat()`.

## C5. ML model (ML → BE)

- Delivery: `p4-ml-data/ml/serving.py` exposes `load_model(path) -> PredictiveModel` and `FeatureExtractor`. Artifacts live in `p4-ml-data/models/<version>/`:
  `failure_clf.joblib`, `anomaly.joblib`, `feature_list.json` (= `schemas.ml.FEATURES` keys, in order), `metrics.json` (`ModelInfo.metrics`), `shap_background.npy`.
- Input: `FeatureWindow` with exactly the `FEATURES` keys, computed over a 10-minute window.
- Output: `AssetHealth`, including the top 5 `ShapFeature`, each with a human-readable `description`.
- `score()` returns in < 50 ms and is deterministic. `predicted_failure_mode` is one of `bearing_wear | overheating | misalignment | null`.
- `rule_threshold_breached` = vibration RMS > 4.5 mm/s sustained for 30 min. It stays **false** while ML already warns, which is the demo moment.
- Scoring loop (BE): every 60 s per asset, buffer → `extract()` → `score()` → publish `ml.health.{asset}`. When risk_72h ≥ 0.7, publish `alerts.raised` (debounced at one per asset per 30 min).
- The required metric on the hold-out set is `lead_time_gain_hours` > 0 compared with the threshold rule.
- *v1.2* sim mode: `SignatureHealthModel` (physics-informed signatures per failure mode, `app/sim/health_model.py`) with a
  backtest on simulated history. The scorer window is **count-based** (last 12 readings while the machine runs, max 1 h)
  so detection works at any plant-clock speed; it restarts when the machine stops or restarts. A repair check is judged
  **only on readings taken after the restart** (`health_since`), never on the cached pre-repair score.

## C6. OT data feed (ML simulator → BE gateway)

- MQTT topic `plant/{plant}/{line}/{asset}/telemetry`, payload `TelemetryMessage` JSON at 1 Hz, QoS 0. Alarms are published on `.../alarm`.
- Raw tags map to `asset_id` and `signal` through `config/tag_map.yaml`. Both sides read the same file.
- Gateway rules: a value outside `range` gets `quality=out_of_range`; no update for 5 s gets `stale`. Data flows one way only, and nothing writes to the OT side.
- The simulator listens to `sim.scene` and changes its signals: `calm` is baseline; `warning` ramps EQ-3012 vibration from 2.1 to 4.3 mm/s and temperature by 9 °C over 60 s; at 70% speed, load drops.

- *v1.2* sim mode: no MQTT. `PlantEngine` (`app/sim/engine.py`) produces readings in-process on the same
  `ot.telemetry.{plant}.{line}` channel at `[telemetry] interval_s`, writes the historian every
  `history_interval_min` plant minutes, and raises/clears alarms on the model thresholds in `plant.yaml`. Scenes are off.

## C7. Security (BE → all)

- **Two auth modes, same API** (`AUTH_MODE` in `.env`, code in `plantguard_contracts/auth.py`):

| | `local` (dev, tests, demo fallback) | `keycloak` (on-prem SSO) |
|---|---|---|
| Who logs the user in | Backend `POST /auth/token` (fixtures users) | Keycloak login page (can federate to AD / LDAP / Entra ID) |
| Access token | HS256, signed with `JWT_SECRET` | RS256, signed by Keycloak |
| Backend checks | signature | signature (Keycloak public keys), issuer, audience `plantguard-api`, expiry |
| Who decides permissions | **Backend** (`ROLE_PERMISSIONS` + plant scope) | **Backend**, unchanged |
| Approval token | Backend, HS256 | Backend, HS256, unchanged |

- `GET /auth/config` (no auth) tells the UI which mode is on and the Keycloak URLs. In keycloak mode `POST /auth/token` returns 501.
- Keycloak token claims are `KeycloakClaims {iss, aud, exp, iat, sub, preferred_username, name, user_id, plant_ids, roles}`, mapped by `to_access_claims()`: `user_id` becomes `sub` (so audit IDs stay `USR-…`), the strongest realm role wins, and no PlantGuard role means 401.
- Everywhere else the backend uses `AccessClaims {sub, name, role, plant_ids, iat, exp}`. WebSockets pass the token as `?token=`.
- Realm: `config/keycloak/plantguard-realm.json` (roles, 4 demo users matching `fixtures/story/users.json`, mappers for `user_id`, `plant_ids`, `roles`, audience). Clients: `plantguard-web` (UI, Authorization Code + PKCE), `plantguard-dev` (password grant for scripts), `plantguard-api` (audience only).
- Permission matrix (`ROLE_PERMISSIONS`):

| Role | view_plant | view_costs | run_agents | approve | submit_feedback | view_audit | run_simulator | perform_work *v1.2* |
|---|---|---|---|---|---|---|---|---|
| operator | ✓ | | ✓ | | ✓ | | | |
| maintenance_lead | ✓ | ✓ | ✓ | ✓ | ✓ | | | |
| plant_manager | ✓ | ✓ | ✓ | ✓ | | ✓ | | |
| admin | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| technician *v1.2* | ✓ | | ✓ | | ✓ | | | ✓ |

- Approval flow: `POST /incidents/{id}/approve` returns an `Approval` with an `approval_token`. This is an `ApprovalClaims` JWT valid for 10 min and tied to the incident, the option and the allowed action kinds; `jti` makes it single use. The backend then executes the option's actions and returns `executed_actions`.
- Redaction: `Redactor.redact()` runs on **every** prompt and replaces serial numbers, names, emails, PLC tags and IPs with `<KIND_n>`. Each LLM call logs a `RedactionReport`, which stores hashes only.
- Audit chain: `hash = sha256(prev_hash + canonical_json(entry))`, starting from 64 zeros. `GET /audit/verify` checks the whole chain.
- Demo users (password `demo`, same in both modes): `op`, `lead`, `manager`, `admin`.
- *v1.2* role **technician** with permission **perform_work** (start / note / finish a repair, close the incident).
  The backend also checks that the caller **is the assigned technician** (`users.technician_id = incidents.assigned_technician_id`);
  other technicians see the job read-only. `ROLE_PRIORITY`: admin > plant_manager > maintenance_lead > technician > operator.
  Sim users (all `demo`): `leadb` (Plant B lead), technicians `suresh` `meera` `priya` `arjun` (A), `vikram` `lakshmi` `rahul` (B).
- *v1.2* server-side sessions (sim, local mode): `AccessClaims.sid`. Sessions expire after 12 h, or 60 min idle; logout or
  DELETE /auth/sessions/{sid} revokes at once. The UI refreshes its 15-min token every 10 min. Audit log is SQL, same hash chain.
- *v1.2* **approval chain** (sim mode, `config/simulation.ini [approvals]`, `app/services/approval_chain.py`). Each option
  lists `approvals_needed: ApprovalStep[]`:
  1. `requesting_plant` — maintenance lead / plant manager of the incident's plant (always, signs first);
  2. `source_plant:<PLANT>` — that plant's lead must **release** a part taken from its stock (stock transfer from another plant);
  3. `manager` — plant manager co-sign when the option costs > `manager_cosign_over_eur` (10,000) or stops the line
     > `manager_cosign_stop_h` (4 h).
  The first `POST /approve` starts the chain (`status=awaiting_signoff`, `Incident.signoff: SignoffState`); the response
  has `approval_token=null` and nothing is executed. Each signer calls `POST /approve` with the **same option**; a signature
  fills every pending step the signer is entitled to (a manager of both plants can sign several). The **last** signature
  gets the token and the actions run; `Approval.signoffs` lists every signature. `decision=reject` by any pending signer ends
  the chain (`signoff.status=declined`, incident back to `awaiting_approval`, nothing created). 409 for another option while
  one is being signed; 403 "Not yours to sign" otherwise. A lead of the source plant may read that incident while it
  concerns their step. `GET /incidents/signoffs` → `SignoffTask[]` (what waits for me). Fixtures mode: v1 single approval.
- The approval token authorises exactly the option's action kinds (speed change, work order, stock transfer, **purchase
  order**). Nothing is created before the final signature.

## C8. Event bus

| Channel | Payload | Publisher → Subscriber |
|---|---|---|
| `ot.telemetry.{plant}.{line}` | TelemetryMessage | gateway → WS fan-out, scorer |
| `ml.health.{asset}` | AssetHealth | scorer → WS, monitor |
| `alerts.raised` | AlertRaised | scorer → incidents, UI |
| `incident.{id}.events` | AgentEvent | agent runtime → WS |
| `incident.{id}.status` | Incident | BE → UI |
| `sim.scene` | SceneChanged | simulator API → simulator, UI (fixtures mode) |
| `ot.alarm` *v1.2* | OtAlarm | engine → repair workflow (alarm while a job is open), UI |
| `sim.fault` *v1.2* | FaultStarted | engine → log / admin UI |
| `sim.verification` *v1.2* | RepairVerified | engine → repair workflow (→ ready_to_close or repair_failed) |
| `garden.activity` *v1.2* | GardenAgent | garden service → WS /garden/stream |

## C9. Golden numbers (do not change without telling everyone)

Valid in **fixtures mode** (tests, mock server, scripted demo). In sim mode the plant starts from the same picture
(EQ-3012 bearing wear at 30 %, ORD-5521, SKF-6205 0 in A / 4 in B) but numbers then follow the simulation.

| Fact | Value |
|---|---|
| Alert time / deadline | Tue 2026-10-13 21:40 UTC / Fri 2026-10-16 18:00 UTC |
| Order | ORD-5521, 5,000 drills, 2,900 done |
| ML | risk 78% in 72 h, RUL 70 h, rule not yet breached (4.3 < 4.5 mm/s), ~31 h earlier |
| Diagnosis | Rear bearing SKF-6205 wear, **60%** confidence (the twist) |
| Stock | Plant A 0, Plant B 4, supplier 21 days |
| Option A (stop now) | €22,385, 8 h down, deadline missed |
| Option B (70%, night swap) | **€2,485**, deadline met, −140 kWh, −58.8 kg CO2 |
| Option C (wait supplier) | €33,485 expected, 78% failure risk |
| Night swap | Wed 22:00, TECH-104, 2.5 h |
| Created | STO-4500012, WO-2026-0153 |
| Feedback | confirmed, similar-case confidence 0.60 → 0.82 |

## C10. Simulated plant & data model (v1.2)

- **Clock**: `SimClock`, `[clock] scale` plant minutes per real minute (60 = one real minute is one plant hour). Plant time
  is stored, so it survives restarts. All plant timestamps (incidents, work orders, transfers) are plant time.
- **Two databases, kept separate like a real plant** (SQLite, WAL, foreign keys enforced, `[storage] data_dir`):
  - `ot.db` — plants, lines, stations, equipment, `asset_conditions` (developing faults), historian, alarms, machine state.
  - `it.db` — ERP (parts, stock, stock_transfers, stock_movements, suppliers), MES (orders, schedule_slots, production,
    speed plans), CMMS (work_orders, work_order_parts, work_order_updates, repair_verifications), PLM (BOM), QMS
    (defects), workforce (technicians, skills, shifts), energy, identity (users, user_plants, sessions, audit), incidents,
    events, options, approvals, feedback, purchase_orders, option_chat. Every table links by foreign key; nothing stands alone.
  - FAISS index (`IndexIDMap2` over inner product, hashing TF-IDF embeddings + lexical re-score) of the manuals in
    `p4-ml-data/corpus/manuals/` and of every closed work order (added when the technician closes it).
- **Seed** from `config/plant.yaml` (models, signatures, BOM, suppliers, parts, plants, technicians, users, orders, tariffs)
  on first start, or every start with `SIM_RESET=1` / `[storage] reset_on_start`.
- **Faults**: random (Poisson, `mean_interval_h`, at most `max_concurrent`), or injected (POST /simulator/faults). Wear
  speed ∝ line speed^`speed_exponent`. Some misalignments look like bearing wear (`misalignment_looks_like_bearing`), and a
  correct repair can still be installed badly (`bad_install_probability`) — so repairs can fail, realistically.
- **Repair physics**: a fault clears only when the fitted parts include the part that actually failed.

## C11. Repair workflow (v1.2)

```
AI analysing → awaiting approval → (approve) executed = waiting_technician → in_repair (technician started)
  → verifying (repair done, machine restarted, watched [repair] verify_min plant minutes)
  → ready_to_close (risk ≤ verify_max_risk, no alarm) → resolved (technician closes, maintenance log + FAISS)
  → repair_failed → technician asks PlantGuard and/or POST /reanalyse → round n+1 → awaiting approval …
```

- `Stage` (UI tag) = ai_analysing · awaiting_approval · awaiting_signoff · waiting_technician · technician_working · verifying ·
  ready_to_close · repair_failed · rejected · resolved · answered. `status_tag` = `in_progress` | `done`.
- Start is blocked until the work order's parts are at the incident's plant (`blocked_reason`, `[repair] require_parts_in_stock`).
  Starting stops the machine for maintenance; completing restarts it and starts the check.
- In sim mode a **rejected** option keeps the incident awaiting approval (the lead can choose another); a closed incident
  needs the technician — `POST /feedback` is fixtures-mode only.
- `MaintenanceLogEntry`: solution, parts, technician, approver, actual vs AI root cause, rounds, time to fix, downtime.

## Change process

1. Open a PR with the `contract` label. It needs approval from the provider **and** every consumer of that contract.
2. Run `make export` (regenerates `generated/` and fixtures) and `make test`. Commit all generated files.
3. Use additive changes only after the M1 checkpoint (new optional fields are fine; renames and removals are not).
4. Post one line in the team chat: "Contract C4: added `get_alarms` to monitor".
