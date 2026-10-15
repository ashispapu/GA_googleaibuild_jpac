#!/usr/bin/env bash
# Step 3: generated secrets (never written to disk or to the repo). Re-running keeps existing values.
#   jwt-secret          signs approval tokens (and sessions in AUTH_MODE=local)
#   db-password         PostgreSQL user "plantguard"            kc-db-password  PostgreSQL user "keycloak"
#   qdrant-admin-key    Qdrant master key: only the migrate/backup jobs and the VM can read it
#   qdrant-app-token    Qdrant JWT for api/engine/worker (named collections only) - minted in step 7
#   auth-secret         Auth.js session encryption (web)        kc-client-secret  OIDC client secret of the web app
#   kc-admin-password   Keycloak bootstrap admin
#   redis-auth, redis-ca, qdrant-ca, qdrant-tls-cert, qdrant-tls-key, keycloak-realm: written by later steps
source "$(dirname "$0")/lib.sh"
gen() { openssl rand -base64 48 | tr -d '\n=+/' | cut -c1-"${1:-48}"; }
for s in jwt-secret db-password kc-db-password qdrant-admin-key auth-secret kc-client-secret kc-admin-password; do
  if has_secret_value "$s"; then echo "  $s: kept"; else secret "$s" "$(gen 48)"; echo "  $s: generated"; fi
done
for s in qdrant-app-token redis-auth redis-ca qdrant-ca qdrant-tls-cert qdrant-tls-key keycloak-realm; do secret "$s"; done

say "Who may read what"
grant_secret jwt-secret        "$SA_API" "$SA_ENGINE" "$SA_WORKER" "$SA_OPS"
grant_secret db-password       "$SA_API" "$SA_ENGINE" "$SA_WORKER" "$SA_OPS"
grant_secret redis-auth        "$SA_API" "$SA_ENGINE" "$SA_WORKER" "$SA_OPS"
grant_secret redis-ca          "$SA_API" "$SA_ENGINE" "$SA_WORKER" "$SA_OPS"
grant_secret qdrant-ca         "$SA_API" "$SA_ENGINE" "$SA_WORKER" "$SA_OPS" "$SA_QDRANT"
grant_secret qdrant-app-token  "$SA_API" "$SA_ENGINE" "$SA_WORKER"
grant_secret qdrant-admin-key  "$SA_OPS" "$SA_QDRANT"
grant_secret qdrant-tls-cert   "$SA_QDRANT"
grant_secret qdrant-tls-key    "$SA_QDRANT"
grant_secret auth-secret       "$SA_WEB"
grant_secret kc-client-secret  "$SA_WEB"
grant_secret kc-db-password    "$SA_AUTH"
grant_secret kc-admin-password "$SA_AUTH"
grant_secret keycloak-realm    "$SA_AUTH"
echo "done"
