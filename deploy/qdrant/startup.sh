#!/usr/bin/env bash
# Qdrant VM boot script (Debian 12, runs as root on every boot; idempotent).
#  - formats/mounts the persistent data disk at /mnt/disks/qdrant (storage + snapshots survive VM replacement)
#  - reads the master API key and TLS certificate from Secret Manager (nothing secret in metadata or images)
#  - runs Qdrant in Docker: TLS on 6333, API key + JWT RBAC (each service only sees its own collections)
#  - copies collection snapshots to Cloud Storage every hour (systemd timer)
set -euo pipefail
MD=http://metadata.google.internal/computeMetadata/v1/instance/attributes
meta() { curl -sf -H "Metadata-Flavor: Google" "$MD/$1"; }
PREFIX="$(meta secret-prefix)"; BUCKET="$(meta backup-bucket)"; IMAGE="$(meta qdrant-image)"
DISK=/dev/disk/by-id/google-qdrant-data; MNT=/mnt/disks/qdrant

# ---- data disk
if ! blkid "$DISK" >/dev/null 2>&1; then mkfs.ext4 -m 0 -E lazy_itable_init=0,lazy_journal_init=0,discard "$DISK"; fi
mkdir -p "$MNT"
grep -q "$MNT" /etc/fstab || echo "UUID=$(blkid -s UUID -o value "$DISK") $MNT ext4 discard,defaults,nofail 0 2" >> /etc/fstab
mountpoint -q "$MNT" || mount "$MNT"
mkdir -p "$MNT/storage" "$MNT/snapshots"

# ---- docker + ops agent (logs and metrics in Cloud Monitoring)
if ! command -v docker >/dev/null; then apt-get update -q && apt-get install -yq docker.io; fi
systemctl enable --now docker
if ! systemctl is-active -q google-cloud-ops-agent 2>/dev/null; then
  curl -sSO https://dl.google.com/cloudagents/add-google-cloud-ops-agent-repo.sh && bash add-google-cloud-ops-agent-repo.sh --also-install || true
fi

# ---- secrets (root-only files)
install -d -m 700 /etc/qdrant
gcloud secrets versions access latest --secret="${PREFIX}-qdrant-tls-cert" > /etc/qdrant/cert.pem
gcloud secrets versions access latest --secret="${PREFIX}-qdrant-tls-key" > /etc/qdrant/key.pem
gcloud secrets versions access latest --secret="${PREFIX}-qdrant-ca" > /etc/qdrant/ca.pem
umask 077
{
  echo "QDRANT__SERVICE__API_KEY=$(gcloud secrets versions access latest --secret="${PREFIX}-qdrant-admin-key")"
  echo "QDRANT__SERVICE__JWT_RBAC=true"
  echo "QDRANT__SERVICE__ENABLE_TLS=true"
  echo "QDRANT__TLS__CERT=/qdrant/tls/cert.pem"
  echo "QDRANT__TLS__KEY=/qdrant/tls/key.pem"
  echo "QDRANT__TLS__CA_CERT=/qdrant/tls/ca.pem"   # Qdrant's own HTTPS client (snapshot recovery) needs it
  echo "QDRANT__STORAGE__SNAPSHOTS_PATH=/qdrant/snapshots"
  echo "QDRANT__TELEMETRY_DISABLED=true"
  echo "QDRANT__LOG_LEVEL=INFO"
} > /etc/qdrant/env
chmod 600 /etc/qdrant/* ; chmod 644 /etc/qdrant/cert.pem /etc/qdrant/ca.pem

# ---- qdrant
docker pull "$IMAGE"
docker rm -f qdrant >/dev/null 2>&1 || true
docker run -d --name qdrant --restart=always --env-file /etc/qdrant/env \
  -p 6333:6333 -p 6334:6334 \
  -v "$MNT/storage:/qdrant/storage" -v "$MNT/snapshots:/qdrant/snapshots" -v /etc/qdrant:/qdrant/tls:ro \
  --log-driver=journald --ulimit nofile=65536:65536 "$IMAGE"

# ---- hourly snapshot copy to Cloud Storage (the backup job creates them through the API)
cat > /etc/systemd/system/qdrant-backup.service <<UNIT
[Unit]
Description=Copy Qdrant snapshots to gs://$BUCKET/qdrant
[Service]
Type=oneshot
ExecStart=/usr/bin/gcloud storage rsync --recursive $MNT/snapshots gs://$BUCKET/qdrant
UNIT
cat > /etc/systemd/system/qdrant-backup.timer <<'UNIT'
[Unit]
Description=Hourly Qdrant snapshot copy
[Timer]
OnCalendar=hourly
Persistent=true
[Install]
WantedBy=timers.target
UNIT
systemctl daemon-reload
systemctl enable --now qdrant-backup.timer
echo "qdrant started"
