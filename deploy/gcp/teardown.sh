#!/usr/bin/env bash
# Deletes everything 01-12 created (NOT reversible). Cloud SQL deletion protection must be lifted first:
#   gcloud sql instances patch <name>-pg --no-deletion-protection
source "$(dirname "$0")/lib.sh"
read -r -p "Delete ALL PlantGuard resources in ${PROJECT_ID}? Type the project id: " a; [ "$a" = "$PROJECT_ID" ] || exit 1
for s in web api worker engine auth; do gcloud run services delete "${NAME}-$s" --region="$REGION" -q || true; done
for j in migrate backup; do gcloud run jobs delete "${NAME}-$j" --region="$REGION" -q || true; done
gcloud scheduler jobs delete "${NAME}-backup" --location="$REGION" -q || true
gcloud compute instances delete "$QDRANT_VM" --zone="$ZONE" -q || true
echo "kept on purpose (delete by hand when sure): disk ${QDRANT_DISK} and its snapshots, bucket gs://${BACKUP_BUCKET}"
gcloud redis instances delete "$REDIS_INSTANCE" --region="$REGION" -q || true
gcloud sql instances delete "$SQL_INSTANCE" -q || true
