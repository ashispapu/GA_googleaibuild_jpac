# P3 — Frontend

**Your job:** the clean, control-room UI that tells the story: floor map → warning → agents thinking → options → approval → resolution.

## Start here
1. `make mock` from the repo root. Login as `op`, `lead`, `manager` or `admin` (password `demo`). On start, call `GET /auth/config`: `local` → show the login form; `keycloak` → `signIn("keycloak")`, then `setToken(session.accessToken)`.
2. Read `CONTRACTS.md` sections **C1, C2, C7**.
3. Use `src/lib/api.ts` (typed client) and `src/lib/ws.ts` (live streams). Types come from `../generated/api.d.ts`; never write API types by hand.
4. Create the Next.js app in this folder (Next.js 14, Tailwind, shadcn/ui, Framer Motion, React Flow, Recharts).

## Your tasks
| ID | Task | Pts | Done when |
|---|---|---|---|
| FE-1 | Shell, theme tokens, typed client, login: local form + Keycloak SSO via Auth.js (`src/lib/auth.ts`) | 4 | all 4 users log in, in both modes |
| FE-2 | Plant floor map: plants → lines → stations → assets, animated flow, pulsing fault | 6 | switch Plant A/B, drill down |
| FE-3 | Asset health: live chart + risk band, rule-vs-ML marker, RUL gauge, SHAP bars | 4 | shows "ML 31 h earlier" |
| FE-4 | Agent war room: React Flow agent graph + streaming thought/evidence cards | 6 | replay looks great at speed=1 |
| FE-5 | Decision board (cost, deadline, kWh/CO2, confidence) + approval modal | 4 | operator sees lock, lead approves |
| FE-6 | Resolution screen (receipts, before/after) + technician feedback form | 2 | scenes 7–8 done |
| FE-7 | Security panel (roles, redactions, audit chain) + model page | 3 | audit "valid" badge |
| FE-8 | Presenter mode ("next scene" → `/simulator/scenario/{name}`), Cmd+K, polish | 3 | whole story with one button |

## Key behaviours
- Switch on `error.code`, never the message (401 → login, FORBIDDEN → lock badge, LLM_UNAVAILABLE → banner).
- Agent stream: de-duplicate by `seq`; after reconnecting, call `GET /incidents/{id}/events?after_seq=`.
- `evidence.link` is a UI route; every AI claim must be clickable.
- Confidence < 0.70 → amber "needs human review".
- Presenter mode: only `admin` may change scenes. Keep a second admin token just for the "next scene" button, while the main session is `op` or `lead`.
- Scenes: `calm` (all green) → `warning` (EQ-3012 red, incident INC-0042 opens) → open the stream on `/incidents/INC-0042/stream` → lead approves → `resolved`.

## You use
C1 REST API · C2 live streams · C7 roles + SSO (from P1). Nothing else: the mock server gives you everything on day 1.
