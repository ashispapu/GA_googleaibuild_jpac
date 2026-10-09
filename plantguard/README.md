# PlantGuard AI

```
pip install -r requirements.txt
make test    # contract tests (data + API + security + full story)
make mock    # full API on http://localhost:8000/docs with the story data
```

## Your folder (start with its README)
| Folder | Person | What |
|---|---|---|
| `p1-backend/` | P1 | Real backend: auth, routers, OT gateway, tool gateway, approvals, audit |
| `p2-agents/` | P2 | 7 agents, RAG, redactor, cost model, replay fallback |
| `p3-frontend/` | P3 | The UI |
| `p4-ml-data/` | P4 | ML model, OT simulator, document corpus |

## Shared (everyone uses, change only via the process in CONTRACTS.md)
| Path | What |
|---|---|
| `CONTRACTS.md` | The 9 hand-offs between people, in plain English |
| `WORK_SPLIT.md` | Points per person, flex pool, checkpoints |
| `plantguard_contracts/` | The rules in code: data shapes, interfaces, tool list |
| `fixtures/` | Story data (owner P4) + recorded agent run |
| `config/` | OT tag map (owner P4, used by P1) + `keycloak/` realm, Compose file, SSO check (owner P1) |
| `generated/` | Auto-made API spec + TypeScript types. Don't edit; run `make export` |
| `mock_server/` | Working fake backend, so everyone can start on day 1 |
| `tests/` | Contract tests the real system must pass |

Demo logins: `op`, `lead`, `manager`, `admin`, password `demo`.

**SSO (on-prem Keycloak):** `docker compose -f config/keycloak/docker-compose.yml up`, set `AUTH_MODE=keycloak` in `.env`. Keycloak logs people in; the backend still decides what they can do. Switch back to `AUTH_MODE=local` at any time (demo fallback).
