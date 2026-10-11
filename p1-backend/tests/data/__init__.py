"""Sample data used by tests. Story fixtures themselves live in the repo-level fixtures/story/ folder."""
from datetime import datetime, timezone

NOW = datetime(2026, 10, 13, 21, 40, tzinfo=timezone.utc)

WORK_ORDER_DRAFT = {
    "asset_id": "EQ-3012", "type": "predictive", "title": "t", "root_cause": "r",
    "parts": [{"sku": "SKF-6205", "quantity": 1}], "technician_id": "TECH-104",
    "planned_start": "2026-10-14T22:00:00Z", "duration_hours": 2.5, "incident_id": "INC-0042",
}
