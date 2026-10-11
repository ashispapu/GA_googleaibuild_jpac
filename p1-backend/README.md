# P1 — Backend core + security

**Your job:** turn the mock server into the real backend: secure, live, and able to execute approved actions.

## Start here
1. `make test` and `make mock` from the repo root. Open http://localhost:8000/docs; that is exactly what you're building.
2. Read `CONTRACTS.md` sections **C1, C2, C4, C6, C7, C8**.
3. Copy routers from `../mock_server/app.py` into `app/routers/` one at a time, then swap fixture reads for connectors.

## Your tasks
| ID | Task | Pts | Done when |
|---|---|---|---|
| BE-1 | Docker Compose: api, postgres+timescale, redis, qdrant, mosquitto | 3 | `make up` starts everything |
| BE-2 | Auth + RBAC + plant scope using `plantguard_contracts/auth.py` (both modes) | 4 | security tests in `make test-p1` pass |
| BE-10 | Keycloak on-prem: run `config/keycloak/docker-compose.yml`, add to main Compose, wire `.env` | 3 | `check_keycloak.py` prints OK for all 4 users |
| BE-3 | Domain routers + fixture connectors (`app/routers/`, `app/connectors/`) | 5 | `make test-p1` passes |
| BE-4 | OT gateway: MQTT in, tag map, quality flags, WS out (`app/services/ot_gateway.py`, `app/connectors/telemetry/`) | 5 | UI chart moves live |
| BE-5 | Scoring loop: buffer → P4 features → P4 model → alerts (`app/services/scoring.py`) | 3 | alert fires in "warning" scene |
| BE-6 | Tool gateway: allow-list, validation, timeout, audit (`app/services/tool_gateway.py`) | 3 | P2 can call all 15 read tools |
| BE-7 | Incidents, approval token, executor, feedback (`app/services/incident_service.py`, `action_executor.py`) | 4 | full-story test passes |
| BE-8 | Audit hash chain + verify + redaction log API | 2 | `/audit/verify` = valid |
| BE-9 | Rate limit, TLS proxy (Caddy), secrets in `.env` | 2 | security slide items demoable |

## You provide → you use
- **Provide:** C1 REST API (to P2, P3) · C2 live streams (to P3) · C4 tool gateway (to P2) · C7 security incl. Keycloak SSO · C8 bus
- **Use:** C5 model from P4 (`p4-ml-data/ml/serving.py`) · C6 MQTT feed from P4 · C3 agent runtime from P2

## SSO in one minute
- `AUTH_MODE=local`: you issue tokens (dev, tests, demo fallback). `AUTH_MODE=keycloak`: Keycloak issues them, you only verify.
- Use `TokenVerifier.from_env().verify(token)` everywhere, which gives the same `AccessClaims` in both modes. RBAC code doesn't change.
- Approval tokens are always yours (HS256, `JWT_SECRET`).
- Check: `docker compose -f config/keycloak/docker-compose.yml up`, then `PYTHONPATH=. AUTH_MODE=keycloak python config/keycloak/check_keycloak.py`.

## Until others are ready
- No model yet? Return `fixtures/story/health_EQ-3012.json`.
- No simulator yet? Use the telemetry generator in `mock_server/app.py` (`ot_stream`).
- No agents yet? Stream `p2-agents/agents/replay.py`.

## Status (what is built)
Run: `make run-p1` · Tests: `make test-p1` (contract) and `make test-p1-unit` (own tests).

| Task | State |
|---|---|
| BE-2 auth, RBAC, plant scope (both modes) | done |
| BE-3 routers + fixture connectors | done (same 37 endpoints as the mock) |
| BE-4 OT gateway: tag map, quality flags, WS out | done; MQTT input not yet (synthetic source until P4's simulator publishes) |
| BE-5 scoring loop | done and unit-tested with fakes; off by default (`SCORING_ENABLED=true` once P4's model works) |
| BE-6 tool gateway | done |
| BE-7 incidents, approval token, executor, feedback | done; agents are the replay runtime until P2's runtime is plugged in |
| BE-8 audit chain, verify, redaction log | done |
| BE-9 rate limit | done; TLS proxy (Caddy) and `.env` secrets are not |
| BE-1 Docker Compose, BE-10 Keycloak in Compose | not done |

## Where to plug in other tracks (only `app/container.py` changes)
- **P2 runtime:** set `runtime_factory` to `lambda speed: FallbackAgentRuntime(P2Runtime(), replay_factory(speed))`.
  P2 calls `ToolGateway.call(...)` (or the REST API) and, per LLM call, `container.redactions.add(report)`.
- **P4 model:** `SCORING_ENABLED=true` loads `p4-ml-data/ml/serving.load_model` + `WindowFeatureExtractor`.
- **Real systems:** replace a `Fixture*` class with one implementing the same Protocol in `interfaces.py`.

## Design notes
- One class per job: `HashChainAuditLog`, `ApprovalTokenService`, `WriteAuthorizer`, `AccessPolicy`, `RunCoordinator`,
  `IncidentService`, `ActionExecutor` (one handler per action kind), `OTGateway` / `QualityPolicy`, `HealthScorer`.
- Everything depends on Protocols (`interfaces.py`, `app/core/ports.py`); `container.py` is the only place that names concrete classes.
- Agent runs start when the first viewer opens `WS /incidents/{id}/stream` (its `speed` applies); later viewers and
  reconnects replay the recorded events, so nothing runs twice.
