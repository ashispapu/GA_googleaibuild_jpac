# PlantGuard AI: Cloud deployment HLD (Google Cloud)

> **Superseded for production by [docs/GCP_DEPLOYMENT_GUIDE.md](docs/GCP_DEPLOYMENT_GUIDE.md)** (approach 2: Cloud SQL
> + Memorystore + a Qdrant vector service + Cloud Run roles, with scripts in `deploy/gcp/`). This document is kept
> for the earlier single-service options.

High-level design for running the PlantGuard AI demo online on Google Cloud. It has two parts:

| Part | Approach | When to use |
|---|---|---|
| **[A](#part-a-minimal-one-cloud-run-service-everything-in-memory)** | One Cloud Run service, everything in memory | Fastest path; nothing is saved across restarts |
| **[B](#part-b-google-native-architecture-recommended)** | Google-native: Cloud Run + Cloud SQL (pgvector) + Vertex AI + Firebase Hosting | **Recommended** for the Google hackathon; data is saved, RAG and LLM run on Google Cloud |

Part B builds on Part A: the same container and the same contract, plus managed Google services behind the existing ports.

---

# Part A: Minimal (one Cloud Run service, everything in memory)

**Decision:** deploy the backend as **one Cloud Run service** built from the root `Dockerfile`. The other services in `docker-compose.yml` (Timescale, Redis, Qdrant, Mosquitto, Keycloak) are not used by the code today, so they are **not deployed** in phase 1.

---

## 1. System context

Who uses the system, and what it talks to.

```mermaid
flowchart LR
    subgraph Users
        OP["👷 Operator / Planner<br/>(browser)"]
        MGR["👔 Plant manager<br/>(approves actions)"]
    end

    FE["🖥️ Frontend (P3, Next.js)<br/>Firebase Hosting or Vercel"]
    API["⚙️ PlantGuard API (P1)<br/>Cloud Run · FastAPI"]
    LLM["🤖 LLM provider<br/>(phase 2: real P2 agents)"]

    OP -->|HTTPS| FE
    MGR -->|HTTPS| FE
    FE -->|"REST over HTTPS<br/>Bearer JWT"| API
    FE <-->|"WebSocket (wss)<br/>?token=JWT"| API
    API -.->|phase 2| LLM

    classDef gcp fill:#e8f0fe,stroke:#1a73e8,color:#0b3d91
    classDef ext fill:#fef7e0,stroke:#f9ab00,color:#5f4500
    class API gcp
    class FE,LLM ext
```

---

## 2. Deployment view (phase 1: demo)

What runs where on Google Cloud.

```mermaid
flowchart TB
    DEV["💻 Developer laptop<br/>gcloud run deploy --source ."]

    subgraph GCP["☁️ Google Cloud project"]
        CB["Cloud Build<br/>builds Dockerfile"]
        AR["Artifact Registry<br/>stores image"]
        SM["Secret Manager<br/>JWT_SECRET (later: LLM key)"]
        LOG["Cloud Logging<br/>stdout / stderr"]

        subgraph CR["Cloud Run service: plantguard-api"]
            direction TB
            INST["Container instance (exactly 1)<br/>uvicorn app.main:app :8000<br/>min=1 · max=1 · CPU always on<br/>timeout 3600s · 1 GiB"]
        end
    end

    FE["Frontend<br/>Firebase Hosting / Vercel<br/>NEXT_PUBLIC_API_URL = Cloud Run URL"]
    BROWSER["🌐 Browser"]

    DEV --> CB --> AR --> CR
    SM -->|"env var at start"| INST
    INST --> LOG
    BROWSER --> FE
    BROWSER -->|"https:// + wss://<br/>(TLS by Google)"| INST

    classDef gcp fill:#e8f0fe,stroke:#1a73e8,color:#0b3d91
    class CB,AR,SM,LOG,INST gcp
```

### Cloud Run settings and why

| Setting | Value | Why |
|---|---|---|
| `--port` | `8000` | The Dockerfile's uvicorn listens on 8000 (Cloud Run default is 8080). |
| `--min-instances` | `1` | State lives in memory. If the service scales to zero, incidents and the audit log are lost. |
| `--max-instances` | `1` | Two instances would each have their own incidents and their own event bus. |
| `--no-cpu-throttling` | on | Keeps the `lifespan` background tasks (scoring, alert opener) running between requests. |
| `--timeout` | `3600` | WebSockets close at the timeout. The frontend reconnects with `after_seq`. |
| `--memory` | `1Gi` | Enough for FastAPI + fixtures + synthetic telemetry. |
| `AUTH_MODE` | `local` | Local HS256 personas. Keycloak is skipped in phase 1. |
| `CORS_ORIGINS` | frontend URL | Only the hosted frontend may call the API from a browser. |
| `JWT_SECRET` | from Secret Manager | Never baked into the image. |

---

## 3. Inside the API container

The same layers as local development (see `p1-backend/STRUCTURE.md`).

```mermaid
flowchart TB
    REQ["HTTP / WebSocket request"]

    MW["① Middleware<br/>CORS · rate limit 1200/min"]
    R["② Routers<br/>/auth /incidents /ot /mes /erp /maintenance /energy<br/>/quality /workforce /engineering /ml /plants /simulator /audit /security"]
    SEC["③ Security services<br/>Authenticator · RBAC · plant scope<br/>approval tokens · WriteAuthorizer"]

    subgraph CORE["④ Business services (built once in container.py)"]
        direction LR
        INC["IncidentService<br/>+ RunCoordinator"]
        RT["Agent runtime<br/>ReplayAgentRuntime"]
        ACT["ActionExecutor"]
        BG["Background tasks<br/>Scoring · AlertOpener<br/>(SCORING_ENABLED)"]
    end

    BUS["⑤ InMemoryEventBus (one, shared)"]
    TG["⑥ ToolGateway<br/>allow-list · validation · timeout"]

    subgraph DATA["⑦ Data"]
        direction LR
        FX["fixtures/story/*.json<br/>MES · ERP · CMMS · energy"]
        TEL["Synthetic OT telemetry"]
        MEM["In memory: incidents · audit log<br/>work orders · stock transfers"]
    end

    REQ --> MW --> R --> SEC --> CORE
    INC --> RT
    INC <--> BUS
    BG <--> BUS
    RT --> TG
    ACT --> TG
    TG --> FX
    TG --> TEL
    BG --> TEL
    INC --> MEM
    TG --> MEM

    classDef mem fill:#fce8e6,stroke:#d93025,color:#7a1c14
    class MEM,BUS mem
```

🔴 Red boxes are **lost on restart** (memory only). That is acceptable for the demo and is the reason for `min=max=1`.

---

## 4. Main flow: incident to approved action

```mermaid
sequenceDiagram
    autonumber
    actor U as Operator (browser)
    participant FE as Frontend
    participant API as Cloud Run API
    participant RT as Agent runtime
    participant BUS as Event bus

    U->>FE: Log in as persona
    FE->>API: POST /auth/token
    API-->>FE: JWT (HS256)

    U->>FE: Open incident
    FE->>API: POST /incidents (Bearer JWT)
    API-->>FE: Incident (status ANALYSING)

    FE->>API: WS /incidents/{id}/stream?token=JWT
    API->>RT: start run (first viewer)
    loop each agent event
        RT-->>API: AgentEvent
        API->>BUS: publish incident.{id}.events
        BUS-->>API: follow() receives it
        API-->>FE: event over wss
    end
    API-->>FE: "finished", then close
    Note over API: status AWAITING_APPROVAL

    FE->>API: GET /incidents/{id}/options
    API-->>FE: OptionsResponse
    U->>FE: Choose option, approve
    FE->>API: POST /incidents/{id}/approve
    API->>API: approval token → WriteAuthorizer →<br/>work order + stock transfer → audit
    API-->>FE: Approval (status EXECUTED)
```

If the WebSocket drops (for example at the 60-minute Cloud Run timeout), the frontend reconnects with `?after_seq=<last seq>` or calls `GET /incidents/{id}/events`.

---

## 5. Live sensor stream

```mermaid
sequenceDiagram
    actor U as Operator
    participant FE as Frontend
    participant API as Cloud Run API
    participant OT as OT gateway<br/>(synthetic telemetry)

    FE->>API: WS /ot/stream?token=JWT&line_id=L3
    loop every tick
        API->>OT: next reading
        OT-->>API: telemetry reading
        API-->>FE: reading over wss
    end
    U->>FE: Switch demo scene
    FE->>API: POST /simulator/scenario/{name}
    Note over OT: readings change (normal / crisis / resolved)
```

---

## 6. Security on the hosted demo

```mermaid
flowchart LR
    B["Browser"] -->|"① TLS<br/>(Google-managed cert)"| CR["Cloud Run edge"]
    CR --> RL["② Rate limit"]
    RL --> AU["③ JWT check<br/>(local HS256)"]
    AU --> RB["④ RBAC role"]
    RB --> PS["⑤ Plant scope"]
    PS --> AT["⑥ Approval token<br/>(writes only, 10 min, single use)"]
    AT --> WA["⑦ WriteAuthorizer<br/>(draft = approved draft)"]
    WA --> TG["⑧ ToolGateway<br/>(allow-list, timeout)"]
    TG --> AL["⑨ Hash-chained audit log"]
```

Phase 1 changes compared with the local plan: **Caddy is not needed** (Cloud Run gives HTTPS/WSS), and **Keycloak is off** (`AUTH_MODE=local`).

---

## 7. Phase 2: real persistence and scale (optional)

Only needed once the code actually uses a database, Redis or vectors.

```mermaid
flowchart TB
    FE["Frontend"] --> API

    subgraph GCP["Google Cloud"]
        API["Cloud Run: plantguard-api<br/>max-instances > 1"]
        KC["Cloud Run: keycloak<br/>(only if SSO demo)"]
        SM["Secret Manager"]
    end

    PG[("Postgres / Timescale<br/>Cloud SQL or Neon")]
    RD[("Redis event bus<br/>Memorystore or Upstash")]
    QD[("Vector search<br/>Qdrant Cloud")]
    LLM["LLM provider"]

    API --> PG
    API <--> RD
    API --> QD
    API --> LLM
    API -->|"JWKS (RS256)"| KC
    SM --> API
```

| Phase 1 (now) | Phase 2 (replacement) | Code change |
|---|---|---|
| `InMemoryEventBus` | Redis bus behind the same `EventBus` interface | new adapter in `infrastructure/`, swap in `container.py` |
| In-memory incidents / audit | Postgres repository | new repository class, swap in `container.py` |
| `fixtures/story/*.json` | Postgres tables or real connectors | new connector, swap in `container.py` |
| `ReplayAgentRuntime` | `PlantAgentRuntime` (P2) + LLM key | add `COPY p2-agents` to Dockerfile, swap in `container.py` |
| `AUTH_MODE=local` | `AUTH_MODE=keycloak` | config only |
| `min=max=1` instance | autoscale | only safe after the bus and repositories leave memory |

---

## 8. Deploy commands (phase 1)

```bash
# one-time
gcloud services enable run.googleapis.com cloudbuild.googleapis.com \
  artifactregistry.googleapis.com secretmanager.googleapis.com
printf '%s' "$(openssl rand -hex 32)" | gcloud secrets create plantguard-jwt --data-file=-

# deploy (from repo root)
gcloud run deploy plantguard-api \
  --source . --region europe-west1 --port 8000 \
  --min-instances 1 --max-instances 1 --no-cpu-throttling \
  --timeout 3600 --memory 1Gi --allow-unauthenticated \
  --set-env-vars AUTH_MODE=local,CORS_ORIGINS=https://your-frontend-url \
  --set-secrets JWT_SECRET=plantguard-jwt:latest
```

Then set `NEXT_PUBLIC_API_URL` in the frontend to the Cloud Run URL. The WebSocket base must be `wss://`.

---

## 9. Known limits (phase 1)

- **Restart wipes state:** a redeploy or crash clears incidents, the audit log and created work orders. Re-run the demo scenario after each deploy.
- **One instance only:** no horizontal scaling until the bus and repositories move out of memory.
- **WebSocket max 60 min:** the frontend must reconnect.
- **Cost:** one always-on instance with CPU always allocated is billed continuously. Delete the service, or set `--min-instances 0`, after the demo.

---
---

# Part B: Google-native architecture (recommended)

**Decision:** keep the API as **one Cloud Run service** (as in Part A), and replace each local Docker service with its managed Google Cloud equivalent. Data is saved in **Cloud SQL for PostgreSQL**, which also stores the RAG vectors with **pgvector**, so no separate vector database is needed. The agents' LLM and the embeddings run on **Vertex AI**. The frontend is served by **Firebase Hosting**.

> **managed service** - Google runs the server, applies updates and takes backups. You get a connection or an API.
> **pgvector** - a Postgres extension that stores vectors (lists of numbers that capture meaning) and finds the closest ones with SQL.
> **Vertex AI** - Google's AI platform. It serves Gemini (LLM) and embedding models over an API, with no API key: Cloud Run authenticates as its service account.

---

## B1. Local service → Google Cloud service

```mermaid
flowchart LR
    subgraph LOCAL["💻 Local (docker-compose)"]
        direction TB
        L1["api (FastAPI)"]
        L2["timescale (Postgres)"]
        L3["qdrant (vectors)"]
        L4["redis (bus)"]
        L5["mosquitto (MQTT)"]
        L6["keycloak (SSO)"]
        L7["Next.js dev server"]
        L8[".env secrets"]
        L9["LLM API key<br/>(anthropic / openai / ollama)"]
    end

    subgraph GCP["☁️ Google Cloud"]
        direction TB
        G1["Cloud Run"]
        G2["Cloud SQL for PostgreSQL"]
        G3["pgvector inside Cloud SQL"]
        G4["InMemoryEventBus (kept)<br/>later: Memorystore"]
        G5["synthetic telemetry (kept)<br/>later: Pub/Sub"]
        G6["AUTH_MODE=local (kept)<br/>later: Identity Platform"]
        G7["Firebase Hosting"]
        G8["Secret Manager"]
        G9["Vertex AI<br/>Gemini + text embeddings"]
    end

    L1 --> G1
    L2 --> G2
    L3 --> G3
    L4 -.-> G4
    L5 -.-> G5
    L6 -.-> G6
    L7 --> G7
    L8 --> G8
    L9 --> G9

    classDef gcp fill:#e8f0fe,stroke:#1a73e8,color:#0b3d91
    classDef kept fill:#f1f3f4,stroke:#5f6368,color:#202124
    class G1,G2,G3,G7,G8,G9 gcp
    class G4,G5,G6 kept
```

Solid arrows are replaced now. Dashed arrows stay as in Part A for the hackathon and have a named Google upgrade path.

---

## B2. Deployment view

```mermaid
flowchart TB
    BROWSER["🌐 Browser"]

    subgraph GCP["☁️ Google Cloud project"]
        direction TB
        FH["Firebase Hosting<br/>Frontend (P3, Next.js static export)<br/>global CDN · HTTPS"]

        subgraph RUN["Cloud Run service: plantguard-api"]
            API["FastAPI container (1 instance)<br/>P1 backend + P2 agents + RAG<br/>service account: plantguard-api@"]
        end

        subgraph DATA["Data"]
            SQL[("Cloud SQL · PostgreSQL 16<br/>incidents · audit log · work orders<br/>stock transfers · telemetry history<br/>doc_chunks + pgvector")]
            GCS[("Cloud Storage<br/>manuals (PDF) · ML model file")]
        end

        subgraph AI["Vertex AI"]
            GEM["Gemini<br/>agent reasoning + tool calls"]
            EMB["Text embeddings<br/>for RAG"]
        end

        SM["Secret Manager<br/>JWT_SECRET · DB password"]
        OBS["Cloud Logging + Monitoring"]
        CB["Cloud Build → Artifact Registry"]
    end

    BROWSER -->|HTTPS| FH
    BROWSER -->|"REST https:// + WebSocket wss://"| API
    API -->|"Cloud SQL connector<br/>(Unix socket, no public IP)"| SQL
    API -->|"chat()"| GEM
    API -->|"embed(query)"| EMB
    API --> GCS
    SM --> API
    API --> OBS
    CB --> RUN

    classDef gcp fill:#e8f0fe,stroke:#1a73e8,color:#0b3d91
    classDef store fill:#e6f4ea,stroke:#1e8e3e,color:#0d652d
    classDef ai fill:#f3e8fd,stroke:#9334e6,color:#5b1d99
    class FH,API,SM,OBS,CB gcp
    class SQL,GCS store
    class GEM,EMB ai
```

**Why this is better than Postgres on a VM:**

| Concern | Postgres on a VM | Cloud SQL |
|---|---|---|
| Network setup | VPC egress + firewall rules | one flag: `--add-cloudsql-instances` |
| Backups | your own `pg_dump` job | automatic daily backups |
| Updates and patches | you | Google |
| Vectors for RAG | install pgvector yourself | `CREATE EXTENSION vector;` |
| Password in transit | you set up TLS | connector encrypts, IAM-checked |

---

## B3. Who may talk to what (IAM)

Cloud Run runs as its own **service account** and gets only the roles it needs. There are no API keys for Google services.

```mermaid
flowchart LR
    SA["🔑 Service account<br/>plantguard-api@PROJECT.iam"]
    SA -->|roles/cloudsql.client| SQL["Cloud SQL"]
    SA -->|roles/aiplatform.user| VX["Vertex AI"]
    SA -->|roles/secretmanager.secretAccessor| SM["Secret Manager"]
    SA -->|roles/storage.objectViewer| GCS["Cloud Storage"]
    SA -->|roles/logging.logWriter| LOG["Cloud Logging"]
```

> **service account** - an identity for a program instead of a person. Google checks its roles on every call, so the container needs no stored keys.

---

## B4. Main flow with real agents, Gemini and Cloud SQL

```mermaid
sequenceDiagram
    autonumber
    actor U as Operator
    participant FE as Frontend<br/>(Firebase Hosting)
    participant API as Cloud Run API
    participant AG as P2 agents<br/>(same container)
    participant GEM as Vertex AI Gemini
    participant SQL as Cloud SQL

    U->>FE: Open incident
    FE->>API: POST /incidents
    API->>SQL: INSERT incident (ANALYSING)
    API-->>FE: Incident

    FE->>API: WS /incidents/{id}/stream?token=JWT
    API->>AG: run(incident)
    loop each agent step
        AG->>GEM: chat() (redacted prompt + tool list)
        GEM-->>AG: thought / tool call
        AG->>API: ToolGateway.call(tool, args)
        API->>SQL: read history, parts, docs
        SQL-->>API: rows
        API-->>AG: tool result
        AG-->>API: AgentEvent
        API->>SQL: INSERT agent_event
        API-->>FE: event over wss
    end
    API->>SQL: UPDATE incident → AWAITING_APPROVAL
    API-->>FE: finished, close

    U->>FE: Approve option
    FE->>API: POST /incidents/{id}/approve
    API->>SQL: INSERT work order + stock transfer + audit (one transaction)
    API-->>FE: Approval (EXECUTED)
```

If Gemini is unavailable, P2's runtime falls back to the recorded replay (`agents/replay.py`), so the demo still works.

---

## B5. RAG with pgvector and Vertex AI embeddings

```mermaid
flowchart TB
    subgraph OFFLINE["Offline, once (P4 / P2 script)"]
        direction LR
        M["Manuals + work orders<br/>(p4-ml-data/corpus)"] --> CH["Split into chunks<br/>~2,000"]
        CH --> E1["Vertex AI embeddings<br/>chunk → vector"]
        E1 --> T[("Cloud SQL<br/>doc_chunks(text, section, page,<br/>embedding vector, tsv tsvector)")]
    end

    subgraph ONLINE["Online, per question"]
        direction LR
        Q["Agent asks:<br/>'spindle vibration rising bearing'"] --> E2["Vertex AI embeddings<br/>query → vector"]
        E2 --> S["SQL: vector distance (pgvector)<br/>+ keyword rank (full-text)<br/>→ merge → top_k"]
        S --> R["DocSearchResponse<br/>MAN-SPINDLE-200 §7.4 first"]
    end

    T -.-> S
```

The search is one SQL query (sketch):

```sql
SELECT doc_id, title, section, page, text,
       0.7 * (1 - (embedding <=> :query_vec))            -- meaning match
     + 0.3 * ts_rank(tsv, plainto_tsquery(:query_text)) AS score   -- keyword match
FROM doc_chunks
ORDER BY score DESC
LIMIT :top_k;
```

`HybridRetriever.search` keeps the same `DocSearchRequest` → `DocSearchResponse` contract, so callers do not change.

---

## B6. Data model in Cloud SQL

```mermaid
erDiagram
    INCIDENTS ||--o{ AGENT_EVENTS : "has"
    INCIDENTS ||--o{ APPROVALS : "has"
    INCIDENTS ||--o{ WORK_ORDERS : "creates"
    INCIDENTS ||--o{ STOCK_TRANSFERS : "creates"
    INCIDENTS {
        text id PK "INC-0042"
        text plant_id
        text status "OPEN..RESOLVED"
        jsonb diagnosis
        jsonb options
        timestamptz created_at
    }
    AGENT_EVENTS {
        text incident_id FK
        int seq
        text agent
        text type
        jsonb payload
    }
    APPROVALS {
        text incident_id FK
        text option_id
        text approved_by
        timestamptz at
    }
    WORK_ORDERS {
        text id PK
        text incident_id FK
        jsonb body
    }
    STOCK_TRANSFERS {
        text id PK
        text incident_id FK
        jsonb body
    }
    AUDIT_LOG {
        bigint seq PK
        text actor
        text action
        text target
        text prev_hash
        text hash
    }
    DOC_CHUNKS {
        text doc_id
        text section
        int page
        text text
        vector embedding "pgvector"
        tsvector tsv
    }
    TELEMETRY {
        text asset_id
        text signal
        timestamptz ts
        float value
    }
```

Pydantic models from `plantguard_contracts` are stored as `jsonb`, so the tables stay small and the contract stays the single source of truth. Cloud SQL has no TimescaleDB extension: `TELEMETRY` is a plain table with an index on `(asset_id, ts)`, which is enough for demo volumes.

---

## B7. What changes in the code

The ports-and-adapters design means each Google service is a **new adapter** chosen in `container.py`. Routers, services and the contract stay the same.

```mermaid
flowchart LR
    subgraph PORTS["Ports (unchanged)"]
        P1["IncidentRepository"]
        P2["AuditLogger store"]
        P3["Write connectors<br/>(work orders, transfers)"]
        P4["Retriever"]
        P5["llm.chat()"]
    end
    subgraph NOW["Today (Part A)"]
        A1["InMemory…"]
        A2["in-memory list"]
        A3["Fixture… (in memory)"]
        A4["FixtureRetriever"]
        A5["replay"]
    end
    subgraph NEW["Part B adapters"]
        B1["PostgresIncidentRepository"]
        B2["PostgresAuditStore"]
        B3["Postgres write connectors"]
        B4["HybridRetriever (pgvector)"]
        B5["provider = vertex (Gemini)"]
    end
    P1 --- A1 -.->|swap in container.py| B1
    P2 --- A2 -.-> B2
    P3 --- A3 -.-> B3
    P4 --- A4 -.-> B4
    P5 --- A5 -.-> B5
```

| Owner | Change | Files |
|---|---|---|
| **P1** | Postgres adapters for incidents, agent events, audit log, work orders and transfers; pick them when `DATABASE_URL` is set, else in-memory | new `repositories/postgres_*.py`, `container.py`, `schema.sql` |
| **P1** | Dockerfile: `COPY p2-agents`, `--proxy-headers`, `${PORT}` | `Dockerfile` |
| **P1** | Add `psycopg[binary]`, `pgvector`, `google-cloud-aiplatform` (or `google-genai`) | `requirements.txt` |
| **P2** | `LLM_PROVIDER=vertex` branch in `chat()` calling Gemini (the redactor stays in front) | `p2-agents/llm/client.py` |
| **P2** | `HybridRetriever` on Cloud SQL (SQL above) | `p2-agents/rag/retriever.py` |
| **P4** | Ingest script: chunk corpus → embed → `INSERT doc_chunks` | `p4-ml-data/…/ingest.py` |
| **P3** | `NEXT_PUBLIC_API_URL` = Cloud Run URL; `wss://`; static export for Firebase Hosting | `p3-frontend` |
| **Contract** | Base URL wording only (as in Part A). No schema or endpoint change | `CONTRACTS.md` |

Local development keeps working: without `DATABASE_URL` the in-memory adapters are used, and `LLM_PROVIDER` can stay `anthropic`/`ollama`/replay.

---

## B8. Deploy commands (Part B)

```bash
PROJECT=$(gcloud config get-value project); REGION=europe-west1

# 1. APIs
gcloud services enable run.googleapis.com sqladmin.googleapis.com aiplatform.googleapis.com \
  secretmanager.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com

# 2. Service account + roles
gcloud iam service-accounts create plantguard-api
SA=plantguard-api@$PROJECT.iam.gserviceaccount.com
for r in cloudsql.client aiplatform.user secretmanager.secretAccessor storage.objectViewer logging.logWriter; do
  gcloud projects add-iam-policy-binding $PROJECT --member=serviceAccount:$SA --role=roles/$r; done

# 3. Cloud SQL (smallest tier) + database + user
gcloud sql instances create plantguard-db --database-version=POSTGRES_16 \
  --tier=db-f1-micro --region=$REGION --edition=enterprise
gcloud sql databases create plantguard --instance=plantguard-db
gcloud sql users create plantguard --instance=plantguard-db --password="$(openssl rand -hex 16)"
# then: CREATE EXTENSION vector;  (via gcloud sql connect or Cloud SQL Studio)

# 4. Secrets
printf '%s' "$(openssl rand -hex 32)" | gcloud secrets create plantguard-jwt --data-file=-
printf '%s' "postgresql://plantguard:<pw>@/plantguard?host=/cloudsql/$PROJECT:$REGION:plantguard-db" \
  | gcloud secrets create plantguard-db-url --data-file=-

# 5. API
gcloud run deploy plantguard-api --source . --region $REGION \
  --service-account $SA --add-cloudsql-instances $PROJECT:$REGION:plantguard-db \
  --min-instances 1 --max-instances 1 --no-cpu-throttling --timeout 3600 \
  --concurrency 250 --memory 1Gi --allow-unauthenticated \
  --set-env-vars AUTH_MODE=local,LLM_PROVIDER=vertex,GOOGLE_CLOUD_PROJECT=$PROJECT,GOOGLE_CLOUD_LOCATION=$REGION,CORS_ORIGINS=https://$PROJECT.web.app \
  --set-secrets JWT_SECRET=plantguard-jwt:latest,DATABASE_URL=plantguard-db-url:latest

# 6. Frontend
cd p3-frontend && NEXT_PUBLIC_API_URL=https://<cloud-run-url> npm run build && firebase deploy --only hosting
```

---

## B9. Later upgrades (not needed for the hackathon)

| Need | Google service | Unlocks |
|---|---|---|
| More than one API instance | **Memorystore for Redis** as the event bus | `--max-instances > 1` (incidents are already in Cloud SQL) |
| Real sensor ingest | **Pub/Sub** (or IoT gateway → Pub/Sub) replacing MQTT | live plant data instead of synthetic |
| SSO for real users | **Identity Platform** (new `AUTH_MODE`, a contract addition) | Google / company login |
| Telemetry analytics | **BigQuery** (export from Cloud SQL or Pub/Sub) | long-term trends, model training data |
| Model hosting | **Vertex AI endpoint** for the P4 health model | retrain and deploy models without rebuilding the API |

---

## B10. Known limits (Part B)

- **Still one API instance:** the event bus is in memory, so `max-instances` stays 1 until Memorystore is added. Incidents and audit now survive restarts; open WebSocket streams do not (the frontend reconnects with `after_seq`, and events are read back from Cloud SQL).
- **Cost:** Cloud SQL `db-f1-micro` and one always-on Cloud Run instance are billed continuously. Hackathon credits usually cover this; stop the SQL instance and set `--min-instances 0` after the demo.
- **Gemini quotas:** new projects have low Vertex AI quotas. Keep the replay fallback on for the live demo.
