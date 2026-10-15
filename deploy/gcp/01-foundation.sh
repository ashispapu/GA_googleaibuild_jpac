#!/usr/bin/env bash
# Step 1: APIs, Artifact Registry, service accounts and their project roles.
source "$(dirname "$0")/lib.sh"

say "Enabling APIs"
gcloud services enable \
  run.googleapis.com compute.googleapis.com sqladmin.googleapis.com redis.googleapis.com \
  servicenetworking.googleapis.com secretmanager.googleapis.com artifactregistry.googleapis.com \
  cloudbuild.googleapis.com aiplatform.googleapis.com dns.googleapis.com cloudscheduler.googleapis.com \
  monitoring.googleapis.com logging.googleapis.com cloudtrace.googleapis.com iap.googleapis.com

say "Artifact Registry ${AR}"
exists gcloud artifacts repositories describe "$NAME" --location="$REGION" || \
  gcloud artifacts repositories create "$NAME" --repository-format=docker --location="$REGION" \
    --description="PlantGuard images" --labels="app=${NAME}"

say "Service accounts"
for who in api:"PlantGuard API" engine:"PlantGuard plant engine" worker:"PlantGuard analysis worker" \
           ops:"PlantGuard migrate + backup jobs" web:"PlantGuard web app" auth:"PlantGuard Keycloak" \
           qdrant:"PlantGuard Qdrant VM" scheduler:"PlantGuard Cloud Scheduler"; do
  id="${NAME}-${who%%:*}"
  exists gcloud iam service-accounts describe "${id}@${PROJECT_ID}.iam.gserviceaccount.com" || \
    gcloud iam service-accounts create "$id" --display-name="${who#*:}"
done

say "Project roles"
bind() { gcloud projects add-iam-policy-binding "$PROJECT_ID" --member="serviceAccount:$1" --role="$2" \
           --condition=None >/dev/null; echo "  $2 -> $1"; }
for s in "$SA_API" "$SA_ENGINE" "$SA_WORKER" "$SA_OPS" "$SA_WEB" "$SA_AUTH" "$SA_QDRANT"; do
  bind "$s" roles/logging.logWriter
  bind "$s" roles/monitoring.metricWriter
done
for s in "$SA_API" "$SA_ENGINE" "$SA_WORKER"; do
  bind "$s" roles/cloudtrace.agent
  bind "$s" roles/aiplatform.user      # Gemini on Vertex AI (agents, options chat, assistant)
done
bind "$SA_OPS" roles/aiplatform.user
bind "$SA_SCHED" roles/run.invoker       # starts the backup job

# Cloud Build deploys with its own service account
CB_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
bind "$CB_SA" roles/run.admin
bind "$CB_SA" roles/artifactregistry.writer
for s in "$SA_API" "$SA_ENGINE" "$SA_WORKER" "$SA_OPS" "$SA_WEB" "$SA_AUTH"; do
  gcloud iam service-accounts add-iam-policy-binding "$s" --member="serviceAccount:$CB_SA" \
    --role=roles/iam.serviceAccountUser >/dev/null
done
echo "done"
