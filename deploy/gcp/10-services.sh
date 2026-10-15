#!/usr/bin/env bash
# Step 10: the PlantGuard services (one image, one role each) and the web app.
#   engine  exactly one active (Redis lease), internal only, CPU always on: simulation, live scoring, alerts
#   worker  1..N, internal only, CPU always on: takes analyses from the Redis run queue and runs the agents
#   api     1..N, public HTTPS + WebSockets, session affinity: everything the web app calls
#   web     1..N, public: the Next.js app (signs in through Keycloak)
source "$(dirname "$0")/lib.sh"
TAG="${TAG:-$(cat "$HERE/.last_tag")}"
ENVV="$(backend_env)"; SECRETS="$(backend_secrets qdrant-app-token)"
PROBES=(--startup-probe="httpGet.path=/readyz,httpGet.port=8000,initialDelaySeconds=5,periodSeconds=5,failureThreshold=30,timeoutSeconds=4"
        --liveness-probe="httpGet.path=/healthz,httpGet.port=8000,periodSeconds=15,failureThreshold=4,timeoutSeconds=4")
common=(--image="${AR}/api:${TAG}" --region="$REGION" --port=8000 "${NETWORK_FLAGS[@]}" "${PROBES[@]}"
        --set-secrets="$SECRETS" --execution-environment=gen2 --cpu-boost)

say "engine"
gcloud run deploy "${NAME}-engine" "${common[@]}" --service-account="$SA_ENGINE" \
  --set-env-vars="${ENVV}|PLANTGUARD_ROLE=engine" \
  --ingress=internal --no-allow-unauthenticated --min-instances=1 --max-instances=1 --no-cpu-throttling \
  --cpu=1 --memory=1Gi --concurrency=20 --labels="app=${NAME},env=${ENV},role=engine"

say "worker"
gcloud run deploy "${NAME}-worker" "${common[@]}" --service-account="$SA_WORKER" \
  --set-env-vars="${ENVV}|PLANTGUARD_ROLE=worker|RUN_RECLAIM_IDLE_S=300" \
  --ingress=internal --no-allow-unauthenticated --min-instances="$WORKER_MIN" --max-instances="$WORKER_MAX" \
  --no-cpu-throttling --cpu=2 --memory=2Gi --concurrency=10 --labels="app=${NAME},env=${ENV},role=worker"

say "api -> ${API_URL}"
gcloud run deploy "${NAME}-api" "${common[@]}" --service-account="$SA_API" \
  --set-env-vars="${ENVV}|PLANTGUARD_ROLE=api" \
  --ingress=all --allow-unauthenticated --min-instances="$API_MIN" --max-instances="$API_MAX" \
  --no-cpu-throttling --cpu=2 --memory=2Gi --concurrency=80 --timeout=3600 --session-affinity \
  --labels="app=${NAME},env=${ENV},role=api"

say "web -> ${WEB_URL}"
gcloud run deploy "${NAME}-web" --image="${AR}/web:${TAG}" --region="$REGION" --port=3000 \
  --service-account="$SA_WEB" --ingress=all --allow-unauthenticated --min-instances=1 --max-instances=5 \
  --cpu=1 --memory=1Gi --concurrency=80 --cpu-boost \
  --set-env-vars="^|^AUTH_URL=${WEB_URL}|AUTH_TRUST_HOST=true|AUTH_KEYCLOAK_ID=plantguard-web|AUTH_KEYCLOAK_ISSUER=${KC_URL}/realms/plantguard" \
  --set-secrets="AUTH_SECRET=${NAME}-auth-secret:latest,AUTH_KEYCLOAK_SECRET=${NAME}-kc-client-secret:latest" \
  --labels="app=${NAME},env=${ENV},role=web"

say "Backup job (Qdrant collection snapshots; the VM copies them to gs://${BACKUP_BUCKET})"
gcloud run jobs deploy "${NAME}-backup" --image="${AR}/api:${TAG}" --region="$REGION" \
  --service-account="$SA_OPS" --command=python --args=-m,app.ops,snapshot \
  --tasks=1 --max-retries=2 --task-timeout=900 --cpu=1 --memory=1Gi "${NETWORK_FLAGS[@]}" \
  --set-env-vars="$(backend_env)|PLANTGUARD_ROLE=migrate" --set-secrets="$(backend_secrets qdrant-admin-key)" \
  --labels="app=${NAME},env=${ENV},role=backup"
exists gcloud scheduler jobs describe "${NAME}-backup" --location="$REGION" || \
  gcloud scheduler jobs create http "${NAME}-backup" --location="$REGION" --schedule="30 */6 * * *" \
    --time-zone="Etc/UTC" --http-method=POST \
    --uri="https://run.googleapis.com/v2/projects/${PROJECT_ID}/locations/${REGION}/jobs/${NAME}-backup:run" \
    --oauth-service-account-email="$SA_SCHED"

echo
echo "web app : ${WEB_URL}"
echo "api     : ${API_URL}   (health: ${API_URL}/healthz)"
echo "sign-in : ${KC_URL}"
