# PlantGuard frontend: what's here and what to check

Next.js 14 (App Router) · Tailwind · shadcn/ui-style components · Framer Motion · React Flow (`@xyflow/react`) · Recharts · Auth.js v5.
Light theme only, plain words for shop-floor users. It matches the "PlantGuard AI — UI Designs" canvas.

## Run it

```bash
npm install
cp .env.example .env.local      # API URL, demo incident id, Keycloak settings
make mock                       # from the repo root (backend on :8000)
npm run dev                     # http://localhost:3000 — log in as op / lead / manager / admin, password demo
```

`npm run typecheck` and `npm run build` must both pass before you push.

## Screens ↔ README tasks

| Route | Screen | Task |
|---|---|---|
| `/login` | Sign in. Calls `GET /auth/config` first: local form, or Keycloak via Auth.js | FE-1 |
| `/` | Overview: plant switch, floor map with moving parts and a pulsing red machine, alert banner, KPIs | FE-2 |
| `/machines`, `/machines/[assetId]` | Machine health: live vibration chart, high-risk band, "AI warning" vs "old alarm" markers, "AI warned ~N h earlier", risk ring, time left, "Why the AI is worried" (SHAP) | FE-3 |
| `/investigation/[incidentId]` | AI team graph (React Flow), live event cards, chat answer with clickable evidence, replay speed | FE-4 |
| `/options/[incidentId]` | Options with cost, deadline, kWh/CO2, confidence banner, timeline, approval dialog (approve or reject); an operator gets the lock dialog | FE-5 |
| `/fixed/[incidentId]` | Receipts, before/after, activity record, technician feedback | FE-6 |
| `/security`, `/accuracy` | Roles table, hidden-from-AI log, audit chain "Untouched"; warning accuracy in plain words (no confusion matrix) | FE-7 |
| Ctrl/Cmd + K, `?presenter=1` | Command menu; demo controls (separate admin token → `POST /simulator/scenario/{name}`) | FE-8 |
| `/[...anything]` | Evidence viewer for any evidence that has no screen of its own | — |

## How it uses your files
- **`generated/api.d.ts`**: every type comes from it, imported the same way `api.ts` does (`../../../generated/api`). Nothing is typed by hand. All API field names are read in **one file, `src/lib/contract.ts`**, which turns them into small view models for the screens. If the backend changes a field, `npm run typecheck` points at that one file.
- **`src/lib/api.ts`** (unchanged): every REST call goes through its `call()`, `login()`, `setToken()` and the example functions (`getPlants`, `getHealth`, `getOptions`, `approve`). `src/lib/backend.ts` wraps them with the C1 error rules. The one exception is the presenter's admin token: `api.ts` always sends the session token, so `backend.ts` sends those few calls with `fetch` directly.
- **`src/lib/ws.ts`** (unchanged): both sockets are opened with `openStream()`. `src/lib/stream.ts` adds de-duplication by `seq`, catch-up and reconnect.
- **`src/lib/auth.ts`** (one change): the `TODO FE-1` is done. It refreshes the Keycloak token a minute before it expires. The UI reads the new token every minute and sends you to login if the refresh fails.

## Things the UI works out itself (no field for them in the contract)
Each one is a small function in `src/lib/contract.ts`, so it is easy to change if the team decides differently.
- **Which incident is open.** There is no "list incidents" call, so the app polls `GET /incidents/{NEXT_PUBLIC_DEMO_INCIDENT_ID}` every 5 s. NOT_FOUND means "no open issue" (also after a demo reset).
- **When the AI team is finished.** `AgentEvent` has no status field. A `status` event whose message contains "finished" / "done" / "complete" counts as finished, and the stream stops after the coordinator's finished event.
- **"Old alarm would go off" on the vibration chart.** `AssetHealth` has no rule ETA, so the marker is placed at the AI warning time plus the model's tested `lead_time_gain_hours` (C5), and is labelled "(estimate)".
- **Option deadline badge.** `deadline_met: true` → "Meets the deadline". If false, an option with 0 h downtime and a breakdown risk of 50% or more shows "Deadline at risk"; otherwise "Misses the deadline".
- **"Likely cost".** When an option's `failure_risk_until_repair` is 50% or more, the cost label says "Likely cost" instead of "Total cost", because the total then contains an expected breakdown cost (C9 option C).
- **Energy.** A negative `energy_kwh` / `co2_kg` is shown as a saving ("140 kWh less electricity").
- **Evidence without a link.** When `evidence.link` is empty, the UI opens `/evidence/{kind}/{ref_id}` and shows the title and snippet it already has.
- **"Without PlantGuard" on the Fixed screen.** It is the costliest other option that misses the deadline (C9: stop now). "4+ hours to make a plan" is a pitch claim, not API data (`MANUAL_PLANNING_TIME` in `labels.ts`).
- **Feedback "before" number.** `Feedback` only returns `new_similar_case_confidence`; the "was 60%" comes from the diagnosis confidence of that incident.

## Other things to know
- The approval result (`Approval`) is kept in the browser tab after you approve (there is no `GET` for it). Someone who opens the Fixed screen in another browser sees the chosen option from `Incident.selected_option_id`, but not the receipts.
- The feedback result is kept only until the page reloads, for the same reason.
- "Ask a follow-up" sends `POST /incidents {asset_id, trigger: "user_question", question}` and opens the new incident.
- There is no "notify lead" call; the operator's lock dialog offers "Copy link for my lead" instead.
- Presenter scene 3 ("Investigation") opens `INC-0042` before scene 4 ("Crisis"), so it expects the simulator to open the incident at the "warning" scene. If it isn't open yet, the page says so instead of breaking.
- Live chart signal names come from the `Signal` type: `vibration_rms`, `bearing_temp`, `vibration_kurtosis`, `power_kw`, `spindle_rpm` (`SIGNALS` in `contract.ts`).

## Contract rules that are built in
- Errors switch on `error.code`: 401 → login · FORBIDDEN/PLANT_SCOPE → "Not allowed for your role" · APPROVAL_REQUIRED/INVALID → approval dialog again · 404 → empty state · 409 → refresh incident · 429 → wait 2 s and retry · LLM_UNAVAILABLE → "Running from recorded analysis" banner.
- Agent stream: de-duplicated by `seq`; on reconnect it calls `GET /incidents/{id}/events?after_seq=` first, then reopens the socket.
- Every AI claim is clickable (evidence, plus result cards that link to their screen).
- Confidence < 0.70 → amber "a person must decide", with the server's `reason` underneath.
- Costs are hidden (`€ ••••`) without `view_costs`; feedback is locked without `submit_feedback`; the activity record needs `view_audit`. The UI uses the `permissions` list from `/auth/me` first and the C7 table as a fallback; the server still decides.
- Times arrive in UTC and are shown in the viewer's local time (C0), formatted as "Tue 13 Oct, 23:40".
- Demo controls use a second admin token, kept only for the "Next scene" button.

## Where the words live
`src/lib/labels.ts` holds every plain-language label: agent names, tool phrases, failure modes, roles, permissions, action kinds and audit actions. Unknown values fall back to their raw name with underscores turned into spaces, so nothing breaks when the backend adds a new one.
