#!/usr/bin/env bash
# Step 9: the migrate job (Cloud Run job) - creates/upgrades both schemas, seeds a new plant, creates the Qdrant
# collections and fills them. Idempotent: run it on every release before rolling out new code.
# Then mints the Qdrant token for the app (read manuals, read/write each plant's repairs; nothing else).
source "$(dirname "$0")/lib.sh"
TAG="${TAG:-$(cat "$HERE/.last_tag")}"

say "Job ${NAME}-migrate"
gcloud run jobs deploy "${NAME}-migrate" --image="${AR}/api:${TAG}" --region="$REGION" \
  --service-account="$SA_OPS" --command=python --args=-m,app.ops,migrate \
  --tasks=1 --max-retries=1 --task-timeout=1800 --cpu=2 --memory=2Gi "${NETWORK_FLAGS[@]}" \
  --set-env-vars="$(backend_env)|PLANTGUARD_ROLE=migrate" --set-secrets="$(backend_secrets qdrant-admin-key)" \
  --labels="app=${NAME},env=${ENV},role=migrate"
gcloud run jobs execute "${NAME}-migrate" --region="$REGION" --wait

say "Qdrant token for the app"
if ! has_secret_value qdrant-app-token || [ "${ROTATE_QDRANT_TOKEN:-0}" = 1 ]; then
  QDRANT_ADMIN_API_KEY="$(secret_value qdrant-admin-key)" \
    "$REPO/deploy/qdrant/mint_token.sh" app "$VECTOR_PREFIX" "$PLANT_IDS" 365 | tr -d '\n' | secret qdrant-app-token -
  echo "  minted (valid 365 days; ROTATE_QDRANT_TOKEN=1 ./09-migrate.sh to rotate, then redeploy the services)"
fi
