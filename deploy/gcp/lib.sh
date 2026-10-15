# Shared helpers for deploy/gcp/*.sh (sourced, not run).
# shellcheck shell=bash
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
[ -f "$HERE/config.env" ] || { echo "copy deploy/gcp/config.env.example to deploy/gcp/config.env and edit it" >&2; exit 1; }
# shellcheck disable=SC1091
source "$HERE/config.env"
: "${PROJECT_ID:?}" "${REGION:?}" "${ZONE:?}" "${NAME:?}" "${ENV:?}"
gcloud config set project "$PROJECT_ID" >/dev/null 2>&1
gcloud config set run/region "$REGION" >/dev/null 2>&1

PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
AR="${REGION}-docker.pkg.dev/${PROJECT_ID}/${NAME}"
VPC="${NAME}-vpc"; RUN_SUBNET="${NAME}-run"; SVC_SUBNET="${NAME}-svc"
SQL_INSTANCE="${NAME}-pg"; REDIS_INSTANCE="${NAME}-redis"
QDRANT_VM="${NAME}-qdrant"; QDRANT_DISK="${NAME}-qdrant-data"; QDRANT_HOST="qdrant.${NAME}.internal"
BACKUP_BUCKET="${PROJECT_ID}-${NAME}-backups"
VECTOR_PREFIX="${NAME}_${ENV}"
# Cloud Run URLs are known before the first deploy: https://<service>-<project number>.<region>.run.app
run_url() { echo "https://$1-${PROJECT_NUMBER}.${REGION}.run.app"; }
API_URL="$(run_url "${NAME}-api")"; WEB_URL="$(run_url "${NAME}-web")"; KC_URL="$(run_url "${NAME}-auth")"

# service accounts (least privilege: each gets only the secrets and roles it needs)
sa() { echo "${NAME}-$1@${PROJECT_ID}.iam.gserviceaccount.com"; }
SA_API="$(sa api)"; SA_ENGINE="$(sa engine)"; SA_WORKER="$(sa worker)"; SA_OPS="$(sa ops)"
SA_WEB="$(sa web)"; SA_AUTH="$(sa auth)"; SA_QDRANT="$(sa qdrant)"; SA_SCHED="$(sa scheduler)"

say() { printf '\n\033[1;34m== %s\033[0m\n' "$*"; }
exists() { "$@" >/dev/null 2>&1; }

# secret NAME [VALUE|-]   create the secret if needed; add a version when a value is given (- = stdin)
secret() {
  local name="${NAME}-$1"
  exists gcloud secrets describe "$name" || gcloud secrets create "$name" --replication-policy=automatic \
    --labels="app=${NAME},env=${ENV}" >/dev/null
  if [ "$#" -ge 2 ]; then
    if [ "$2" = "-" ]; then gcloud secrets versions add "$name" --data-file=- >/dev/null
    else printf '%s' "$2" | gcloud secrets versions add "$name" --data-file=- >/dev/null; fi
  fi
}
has_secret_value() { exists gcloud secrets versions access latest --secret="${NAME}-$1"; }
secret_value() { gcloud secrets versions access latest --secret="${NAME}-$1"; }
grant_secret() {  # grant_secret SECRET SA...
  local s="${NAME}-$1"; shift
  for who in "$@"; do
    gcloud secrets add-iam-policy-binding "$s" --member="serviceAccount:$who" \
      --role=roles/secretmanager.secretAccessor --condition=None >/dev/null
  done
}

# ---- what every backend role (api / engine / worker / jobs) is given ----
backend_env() {
  local sql_ip redis_host
  sql_ip="$(gcloud sql instances describe "$SQL_INSTANCE" --format='value(ipAddresses[0].ipAddress)')"
  redis_host="$(gcloud redis instances describe "$REDIS_INSTANCE" --region="$REGION" --format='value(host)')"
  local env=(
    "PLANTGUARD_ENV=${ENV}" "DATA_MODE=sim" "LOG_FORMAT=json" "GOOGLE_CLOUD_PROJECT=${PROJECT_ID}"
    "DB_HOST=${sql_ip}" "DB_USER=plantguard" "DB_NAME_OT=plantguard_ot" "DB_NAME_IT=plantguard_it" "DB_SSLMODE=require"
    "PLANTGUARD_DB_POOL_SIZE=8"
    "REDIS_HOST=${redis_host}" "REDIS_PORT=6378" "REDIS_TLS=true" "REDIS_CA_CERT=/etc/plantguard/redis/ca.pem"
    "PLANTGUARD_REDIS_PREFIX=${NAME}-${ENV}"
    "QDRANT_URL=https://${QDRANT_HOST}:6333" "QDRANT_CA_CERT=/etc/plantguard/qdrant/ca.pem"
    "PLANTGUARD_VECTOR_PREFIX=${VECTOR_PREFIX}"
    "GOOGLE_GENAI_USE_ENTERPRISE=True" "GOOGLE_CLOUD_LOCATION=${VERTEX_LOCATION}"
    "AUTH_MODE=keycloak" "KEYCLOAK_URL=${KC_URL}" "KEYCLOAK_REALM=plantguard" "KEYCLOAK_AUDIENCE=plantguard-api"
    "KEYCLOAK_CLIENT_ID=plantguard-web" "CORS_ORIGINS=${WEB_URL}" "WORKER_CONCURRENCY=${WORKER_CONCURRENCY}"
  )
  local IFS='|'; echo "^|^${env[*]}"   # gcloud list syntax with | as the separator (values contain commas)
}
backend_secrets() {  # $1 = token secret for Qdrant (qdrant-app-token | qdrant-admin-key)
  echo "DB_PASSWORD=${NAME}-db-password:latest,REDIS_AUTH=${NAME}-redis-auth:latest,JWT_SECRET=${NAME}-jwt-secret:latest,QDRANT_API_KEY=${NAME}-${1}:latest,/etc/plantguard/redis/ca.pem=${NAME}-redis-ca:latest,/etc/plantguard/qdrant/ca.pem=${NAME}-qdrant-ca:latest"
}
NETWORK_FLAGS=(--network="$VPC" --subnet="$RUN_SUBNET" --vpc-egress=private-ranges-only)
