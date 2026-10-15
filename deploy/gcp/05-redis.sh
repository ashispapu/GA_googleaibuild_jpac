#!/usr/bin/env bash
# Step 5: Memorystore for Redis 7.2 - replica with automatic fail-over, AUTH, in-transit TLS, RDB persistence.
# Used for: the event bus (pub/sub), the analysis run queue (stream + consumer group), live scores and telemetry,
# single-use approval tokens, shared rate limits, the plant-engine lease. Postgres stays the system of record.
source "$(dirname "$0")/lib.sh"

say "Memorystore ${REDIS_INSTANCE} (takes ~5 min the first time)"
if ! exists gcloud redis instances describe "$REDIS_INSTANCE" --region="$REGION"; then
  args=(--region="$REGION" --tier="$REDIS_TIER" --size="$REDIS_SIZE_GB" --redis-version=redis_7_2
        --network="projects/${PROJECT_ID}/global/networks/${VPC}" --connect-mode=private-service-access
        --enable-auth --transit-encryption-mode=server-authentication
        --redis-config=maxmemory-policy=volatile-lru
        --maintenance-window-day=sunday --maintenance-window-hour=21
        --labels="app=${NAME},env=${ENV}" --display-name="PlantGuard bus and queue")
  [ "$REDIS_TIER" = standard ] && args+=(--persistence-mode=rdb --rdb-snapshot-period=1h)
  gcloud redis instances create "$REDIS_INSTANCE" "${args[@]}"
fi

say "AUTH string and server CA -> Secret Manager"
gcloud redis instances get-auth-string "$REDIS_INSTANCE" --region="$REGION" --format='value(authString)' \
  | tr -d '\n' | secret redis-auth -
gcloud redis instances describe "$REDIS_INSTANCE" --region="$REGION" --format='value(serverCaCerts[0].cert)' \
  | secret redis-ca -
echo "host: $(gcloud redis instances describe "$REDIS_INSTANCE" --region="$REGION" --format='value(host)'):6378 (TLS)"
