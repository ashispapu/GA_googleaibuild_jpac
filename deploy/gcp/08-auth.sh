#!/usr/bin/env bash
# Step 8: Keycloak (single sign-on) on Cloud Run, backed by the "keycloak" database.
#   - the production realm is generated from config/keycloak/plantguard-realm.json (web URLs, new client secret,
#     strict password policy, no password-grant client) and mounted from Secret Manager
#   - demo users are kept with one-time passwords (Keycloak forces a change at first sign-in) unless DROP_USERS=1
#   - one instance with a local cache (Cloud Run has no cluster discovery); its state lives in Postgres
source "$(dirname "$0")/lib.sh"
TAG="${TAG:-$(cat "$HERE/.last_tag")}"
SQL_IP="$(gcloud sql instances describe "$SQL_INSTANCE" --format='value(ipAddresses[0].ipAddress)')"

if ! has_secret_value keycloak-realm || [ "${REGENERATE_REALM:-0}" = 1 ]; then
  say "Production realm"
  TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
  users=(--temp-passwords "$HERE/initial-passwords.csv"); [ "${DROP_USERS:-0}" = 1 ] && users=(--drop-users)
  python3 "$REPO/deploy/keycloak/prepare_realm.py" --web-url "$WEB_URL" \
    --client-secret "$(secret_value kc-client-secret)" --out "$TMP/realm.json" "${users[@]}"
  secret keycloak-realm - < "$TMP/realm.json"
  [ "${DROP_USERS:-0}" = 1 ] || echo "  one-time passwords: deploy/gcp/initial-passwords.csv (hand them out, then delete the file)"
fi

say "Keycloak service ${NAME}-auth -> ${KC_URL}"
gcloud run deploy "${NAME}-auth" --image="${AR}/auth:${TAG}" --region="$REGION" \
  --service-account="$SA_AUTH" --allow-unauthenticated --ingress=all \
  --cpu=2 --memory=2Gi --min-instances=1 --max-instances=1 --no-cpu-throttling --cpu-boost \
  --concurrency=80 --timeout=300 --port=8080 "${NETWORK_FLAGS[@]}" \
  --startup-probe="tcpSocket.port=8080,initialDelaySeconds=10,periodSeconds=5,failureThreshold=36,timeoutSeconds=3" \
  --set-env-vars="^|^KC_DB_URL=jdbc:postgresql://${SQL_IP}:5432/keycloak?sslmode=require|KC_DB_USERNAME=keycloak|KC_HOSTNAME=${KC_URL}|KC_LOG_CONSOLE_OUTPUT=json|KC_BOOTSTRAP_ADMIN_USERNAME=kcadmin" \
  --set-secrets="KC_DB_PASSWORD=${NAME}-kc-db-password:latest,KC_BOOTSTRAP_ADMIN_PASSWORD=${NAME}-kc-admin-password:latest,/opt/keycloak/data/import/plantguard-realm.json=${NAME}-keycloak-realm:latest" \
  --labels="app=${NAME},env=${ENV},role=auth"
echo "admin console: ${KC_URL}/admin  (user kcadmin, password: gcloud secrets versions access latest --secret=${NAME}-kc-admin-password)"
