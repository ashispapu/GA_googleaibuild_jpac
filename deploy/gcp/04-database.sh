#!/usr/bin/env bash
# Step 4: Cloud SQL for PostgreSQL 16 - private IP only, TLS required, HA, daily backups + point-in-time recovery.
# Databases: plantguard_ot (machines, readings, alarms), plantguard_it (ERP/MES/CMMS, incidents, audit, users),
#            keycloak (identity). Two users: plantguard (OT + IT), keycloak (keycloak db only).
source "$(dirname "$0")/lib.sh"

say "Cloud SQL instance ${SQL_INSTANCE} (takes ~10 min the first time)"
if ! exists gcloud sql instances describe "$SQL_INSTANCE"; then
  gcloud sql instances create "$SQL_INSTANCE" \
    --database-version=POSTGRES_16 --edition=ENTERPRISE --tier="$SQL_TIER" --region="$REGION" \
    --availability-type="$SQL_HA" --storage-type=SSD --storage-size="$SQL_STORAGE_GB" --storage-auto-increase \
    --network="projects/${PROJECT_ID}/global/networks/${VPC}" --no-assign-ip --enable-google-private-path \
    --ssl-mode=ENCRYPTED_ONLY \
    --backup-start-time=20:00 --enable-point-in-time-recovery --retained-backups-count=14 \
    --retained-transaction-log-days=7 \
    --maintenance-window-day=SUN --maintenance-window-hour=21 --maintenance-release-channel=production \
    --insights-config-query-insights-enabled --insights-config-record-application-tags \
    --database-flags=max_connections=500,log_min_duration_statement=500,log_lock_waits=on,cloudsql.iam_authentication=on \
    --password-policy-min-length=16 --enable-password-policy \
    --deletion-protection
fi

say "Databases and users"
for db in plantguard_ot plantguard_it keycloak; do
  exists gcloud sql databases describe "$db" --instance="$SQL_INSTANCE" || \
    gcloud sql databases create "$db" --instance="$SQL_INSTANCE"
done
user() {  # user NAME SECRET
  if exists gcloud sql users describe "$1" --instance="$SQL_INSTANCE"; then
    gcloud sql users set-password "$1" --instance="$SQL_INSTANCE" --password="$(secret_value "$2")" >/dev/null
  else
    gcloud sql users create "$1" --instance="$SQL_INSTANCE" --password="$(secret_value "$2")" >/dev/null
  fi
  echo "  user $1 ok"
}
user plantguard db-password
user keycloak kc-db-password

SQL_IP="$(gcloud sql instances describe "$SQL_INSTANCE" --format='value(ipAddresses[0].ipAddress)')"
echo "private IP: $SQL_IP"
cat <<MSG

Least privilege inside PostgreSQL (run once; the default "postgres" admin is only needed for this):
  gcloud sql users set-password postgres --instance=$SQL_INSTANCE --prompt-for-password
  then from Cloud Shell / a VM in the VPC:  psql "host=$SQL_IP user=postgres sslmode=require" -f deploy/gcp/sql/grants.sql
MSG
