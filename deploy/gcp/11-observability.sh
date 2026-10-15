#!/usr/bin/env bash
# Step 11: uptime checks, alerting and a log-based error metric. Logs are JSON (severity, request id, trace) so
# Cloud Logging groups each request's lines; use Logs Explorer with resource.labels.service_name="<name>-api".
# Needs the gcloud beta component for notification channels:  gcloud components install beta
source "$(dirname "$0")/lib.sh"

say "Notification channel ${ALERT_EMAIL}"
CHANNEL="$(gcloud beta monitoring channels list --filter="labels.email_address=${ALERT_EMAIL}" --format='value(name)' | head -1)"
[ -n "$CHANNEL" ] || CHANNEL="$(gcloud beta monitoring channels create --type=email --display-name="PlantGuard on-call" \
  --channel-labels="email_address=${ALERT_EMAIL}" --format='value(name)')"

say "Uptime checks"
for target in "api:${API_URL#https://}:/auth/config" "web:${WEB_URL#https://}:/login"; do
  IFS=: read -r what host path <<< "$target"
  exists gcloud monitoring uptime describe "${NAME}-${what}" || \
    gcloud monitoring uptime create "${NAME}-${what}" --resource-type=uptime-url \
      --resource-labels="host=${host},project_id=${PROJECT_ID}" --path="$path" --protocol=https \
      --period=1 --timeout=10 --regions=asia-pacific,europe,usa-oregon 2>/dev/null || true
done

say "Log-based metric: backend errors"
exists gcloud logging metrics describe "${NAME}_backend_errors" || \
  gcloud logging metrics create "${NAME}_backend_errors" \
    --description="ERROR lines of the PlantGuard backend services" \
    --log-filter="resource.type=\"cloud_run_revision\" AND resource.labels.service_name=~\"^${NAME}-(api|engine|worker)$\" AND severity>=ERROR"

say "Alert policies"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
policy() {  # policy FILE-NAME DISPLAY FILTER COMPARISON THRESHOLD DURATION ALIGNER
  cat > "$TMP/$1.json" <<JSON
{"displayName": "$2", "combiner": "OR", "notificationChannels": ["$CHANNEL"],
 "conditions": [{"displayName": "$2", "conditionThreshold": {
   "filter": "$3", "comparison": "$4", "thresholdValue": $5, "duration": "$6",
   "aggregations": [{"alignmentPeriod": "60s", "perSeriesAligner": "$7"}]}}],
 "documentation": {"content": "See docs/GCP_DEPLOYMENT_GUIDE.md, section Operations.", "mimeType": "text/markdown"}}
JSON
  gcloud monitoring policies list --filter="displayName=\"$2\"" --format='value(name)' | grep -q . || \
    gcloud monitoring policies create --policy-from-file="$TMP/$1.json" >/dev/null
  echo "  $2"
}
policy api5xx  "PlantGuard API 5xx" \
  "resource.type=\\\"cloud_run_revision\\\" AND resource.labels.service_name=\\\"${NAME}-api\\\" AND metric.type=\\\"run.googleapis.com/request_count\\\" AND metric.labels.response_code_class=\\\"5xx\\\"" \
  COMPARISON_GT 5 300s ALIGN_RATE
policy errors  "PlantGuard backend errors" \
  "resource.type=\\\"cloud_run_revision\\\" AND metric.type=\\\"logging.googleapis.com/user/${NAME}_backend_errors\\\"" \
  COMPARISON_GT 0.2 300s ALIGN_RATE
policy sqlcpu  "PlantGuard Cloud SQL CPU > 80%" \
  "resource.type=\\\"cloudsql_database\\\" AND metric.type=\\\"cloudsql.googleapis.com/database/cpu/utilization\\\"" \
  COMPARISON_GT 0.8 600s ALIGN_MEAN
policy redismem "PlantGuard Redis memory > 80%" \
  "resource.type=\\\"redis_instance\\\" AND metric.type=\\\"redis.googleapis.com/stats/memory/usage_ratio\\\"" \
  COMPARISON_GT 0.8 600s ALIGN_MEAN
policy qdrantcpu "PlantGuard Qdrant VM CPU > 85%" \
  "resource.type=\\\"gce_instance\\\" AND metric.type=\\\"compute.googleapis.com/instance/cpu/utilization\\\" AND metadata.user_labels.role=\\\"qdrant\\\"" \
  COMPARISON_GT 0.85 600s ALIGN_MEAN
echo "done"
