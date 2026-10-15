#!/usr/bin/env bash
# Step 6: the vector service - Qdrant on a private VM with a persistent SSD, TLS, API key + JWT RBAC.
#   collections: <name>_<env>_manuals (shared, read-only for the app) and <name>_<env>_workorders_<plant> (one per plant)
#   persistence: data disk (daily disk snapshots, 14 days) + collection snapshots copied to Cloud Storage hourly
source "$(dirname "$0")/lib.sh"

say "Backup bucket gs://${BACKUP_BUCKET}"
if ! exists gcloud storage buckets describe "gs://${BACKUP_BUCKET}"; then
  gcloud storage buckets create "gs://${BACKUP_BUCKET}" --location="$REGION" --uniform-bucket-level-access \
    --public-access-prevention --default-storage-class=STANDARD
  gcloud storage buckets update "gs://${BACKUP_BUCKET}" --versioning
  printf '%s' '{"rule":[{"action":{"type":"Delete"},"condition":{"age":30}},{"action":{"type":"Delete"},"condition":{"isLive":false,"daysSinceNoncurrentTime":7}}]}' > /tmp/lifecycle.json
  gcloud storage buckets update "gs://${BACKUP_BUCKET}" --lifecycle-file=/tmp/lifecycle.json
fi
gcloud storage buckets add-iam-policy-binding "gs://${BACKUP_BUCKET}" --member="serviceAccount:${SA_QDRANT}" \
  --role=roles/storage.objectAdmin >/dev/null

say "TLS certificate for ${QDRANT_HOST} (private CA)"
if ! has_secret_value qdrant-ca; then
  TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
  "$REPO/deploy/qdrant/make_certs.sh" "$TMP" "$QDRANT_HOST"
  secret qdrant-ca - < "$TMP/ca.pem"
  secret qdrant-tls-cert - < "$TMP/server.pem"
  secret qdrant-tls-key - < "$TMP/server.key"
  echo "  the CA private key was not kept (issue a new CA + cert to rotate)"
fi

say "Data disk ${QDRANT_DISK} + daily snapshot schedule"
exists gcloud compute resource-policies describe "${NAME}-daily" --region="$REGION" || \
  gcloud compute resource-policies create snapshot-schedule "${NAME}-daily" --region="$REGION" \
    --daily-schedule --start-time=19:00 --max-retention-days=14 --on-source-disk-delete=keep-auto-snapshots \
    --storage-location="$REGION"
exists gcloud compute disks describe "$QDRANT_DISK" --zone="$ZONE" || \
  gcloud compute disks create "$QDRANT_DISK" --zone="$ZONE" --type=pd-ssd --size="${QDRANT_DISK_GB}GB" \
    --resource-policies="${NAME}-daily" --labels="app=${NAME},env=${ENV}"

say "VM ${QDRANT_VM} (no public IP)"
if ! exists gcloud compute instances describe "$QDRANT_VM" --zone="$ZONE"; then
  gcloud compute instances create "$QDRANT_VM" --zone="$ZONE" --machine-type="$QDRANT_MACHINE" \
    --subnet="$SVC_SUBNET" --no-address --tags=qdrant \
    --image-family=debian-12 --image-project=debian-cloud --boot-disk-size=20GB --boot-disk-type=pd-balanced \
    --disk="name=${QDRANT_DISK},device-name=qdrant-data,mode=rw,boot=no,auto-delete=no" \
    --service-account="$SA_QDRANT" --scopes=cloud-platform \
    --shielded-secure-boot --shielded-vtpm --shielded-integrity-monitoring \
    --metadata="secret-prefix=${NAME},backup-bucket=${BACKUP_BUCKET},qdrant-image=${QDRANT_IMAGE},enable-oslogin=TRUE" \
    --metadata-from-file=startup-script="$REPO/deploy/qdrant/startup.sh" \
    --labels="app=${NAME},env=${ENV},role=qdrant"
else
  gcloud compute instances add-metadata "$QDRANT_VM" --zone="$ZONE" \
    --metadata="qdrant-image=${QDRANT_IMAGE}" --metadata-from-file=startup-script="$REPO/deploy/qdrant/startup.sh"
fi
QDRANT_IP="$(gcloud compute instances describe "$QDRANT_VM" --zone="$ZONE" --format='value(networkInterfaces[0].networkIP)')"

say "DNS ${QDRANT_HOST} -> ${QDRANT_IP}"
if exists gcloud dns record-sets describe "${QDRANT_HOST}." --type=A --zone="${NAME}-internal"; then
  gcloud dns record-sets update "${QDRANT_HOST}." --type=A --ttl=60 --rrdatas="$QDRANT_IP" --zone="${NAME}-internal"
else
  gcloud dns record-sets create "${QDRANT_HOST}." --type=A --ttl=60 --rrdatas="$QDRANT_IP" --zone="${NAME}-internal"
fi
echo "Qdrant: https://${QDRANT_HOST}:6333 (inside the VPC). Boot log: gcloud compute instances get-serial-port-output ${QDRANT_VM} --zone=${ZONE}"
