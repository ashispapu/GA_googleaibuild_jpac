# PlantGuard AI — Contracts v1

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
| C9 | Golden data | ML → all | `fixtures/build_fixtures.py`, `fixtures/story/` |

---

## C0. Conventions (everyone)

- Time: UTC, ISO-8601 with `Z`/offset. UI converts to local time.
- Money: `{"amount": 85.0, "currency": "EUR"}`. Units are in field comments (`mm/s`, `°C`, `kW`, `kWh`, `kg`).
- Probabilities: `0..1` floats. The UI shows them as percentages.
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
| /simulator | POST /scenario/{name} | run_simulator (admin) |

## C2. Realtime streams (BE → FE)

- `WS /ot/stream?token=<jwt>&line_id=L3`: one `TelemetryMessage` per asset per second.
- `WS /incidents/{id}/stream?token=<jwt>&speed=1`: `AgentEvent` frames in `seq` order. The server closes the socket after the orchestrator sends `finished`.
- Reconnect: call `GET /incidents/{id}/events?after_seq=<last seq>` to catch up, then reopen the socket. FE de-duplicates events by `seq`.
- Rendering rules for FE: `THOUGHT` becomes a thinking card, `TOOL_CALL` a chip with the tool name, `EVIDENCE` a clickable card (`evidence.link` is the UI route), `RESULT` a summary card per agent, `STATUS started/finished` lights the agent node on and off.

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
- Agents **never** call write tools. The backend executor runs the approved option's `actions`.

## C4. Tools (BE → AG)

- 17 tools are listed in `tools.py`. Each has a typed input, a typed output, an endpoint and an **agent allow-list**.
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

## C6. OT data feed (ML simulator → BE gateway)

- MQTT topic `plant/{plant}/{line}/{asset}/telemetry`, payload `TelemetryMessage` JSON at 1 Hz, QoS 0. Alarms are published on `.../alarm`.
- Raw tags map to `asset_id` and `signal` through `config/tag_map.yaml`. Both sides read the same file.
- Gateway rules: a value outside `range` gets `quality=out_of_range`; no update for 5 s gets `stale`. Data flows one way only, and nothing writes to the OT side.
- The simulator listens to `sim.scene` and changes its signals: `calm` is baseline; `warning` ramps EQ-3012 vibration from 2.1 to 4.3 mm/s and temperature by 9 °C over 60 s; at 70% speed, load drops.

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

| Role | view_plant | view_costs | run_agents | approve | submit_feedback | view_audit | run_simulator |
|---|---|---|---|---|---|---|---|
| operator | ✓ | | ✓ | | ✓ | | |
| maintenance_lead | ✓ | ✓ | ✓ | ✓ | ✓ | | |
| plant_manager | ✓ | ✓ | ✓ | ✓ | | ✓ | |
| admin | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |

- Approval flow: `POST /incidents/{id}/approve` returns an `Approval` with an `approval_token`. This is an `ApprovalClaims` JWT valid for 10 min and tied to the incident, the option and the allowed action kinds; `jti` makes it single use. The backend then executes the option's actions and returns `executed_actions`.
- Redaction: `Redactor.redact()` runs on **every** prompt and replaces serial numbers, names, emails, PLC tags and IPs with `<KIND_n>`. Each LLM call logs a `RedactionReport`, which stores hashes only.
- Audit chain: `hash = sha256(prev_hash + canonical_json(entry))`, starting from 64 zeros. `GET /audit/verify` checks the whole chain.
- Demo users (password `demo`, same in both modes): `op`, `lead`, `manager`, `admin`.

## C8. Event bus

| Channel | Payload | Publisher → Subscriber |
|---|---|---|
| `ot.telemetry.{plant}.{line}` | TelemetryMessage | gateway → WS fan-out, scorer |
| `ml.health.{asset}` | AssetHealth | scorer → WS, monitor |
| `alerts.raised` | AlertRaised | scorer → incidents, UI |
| `incident.{id}.events` | AgentEvent | agent runtime → WS |
| `incident.{id}.status` | Incident | BE → UI |
| `sim.scene` | SceneChanged | simulator API → simulator, UI |

## C9. Golden numbers (do not change without telling everyone)

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

## Change process

1. Open a PR with the `contract` label. It needs approval from the provider **and** every consumer of that contract.
2. Run `make export` (regenerates `generated/` and fixtures) and `make test`. Commit all generated files.
3. Use additive changes only after the M1 checkpoint (new optional fields are fine; renames and removals are not).
4. Post one line in the team chat: "Contract C4: added `get_alarms` to monitor".
