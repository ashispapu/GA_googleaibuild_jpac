"""Real backend. Start by copying routers from ../../mock_server/app.py, then replace fixture reads
with connectors. Must keep passing ../../tests (run with PLANTGUARD_URL pointing here)."""
from fastapi import FastAPI

app = FastAPI(title="PlantGuard AI API", version="1.0.0")

# TODO BE-2: auth + RBAC        -> app/security/
# TODO BE-3: domain routers     -> app/routers/  (ot, erp, mes, maintenance, quality, engineering, workforce, energy)
# TODO BE-4: OT gateway         -> app/ot/
# TODO BE-5: scoring loop       -> app/ot/scoring.py (uses P4's load_model)
# TODO BE-6: tool gateway       -> app/gateway/
# TODO BE-7: incidents/approvals/executor -> app/incidents/
# TODO BE-8: audit chain        -> app/security/audit.py
