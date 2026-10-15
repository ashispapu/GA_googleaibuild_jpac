#!/usr/bin/env bash
# Step 12: smoke test of the deployed system.
source "$(dirname "$0")/lib.sh"
ok() { printf '  \033[32mok\033[0m  %s\n' "$*"; }; bad() { printf '  \033[31mFAIL\033[0m %s\n' "$*"; FAILED=1; }
FAILED=0
say "Public endpoints"
code() { curl -s -o /dev/null -w '%{http_code}' "$1"; }
[ "$(code "${API_URL}/auth/config")" = 200 ] && ok "api answers" || bad "api /auth/config"
[ "$(code "${API_URL}/auth/config")" = 200 ] && ok "api /auth/config" || bad "api /auth/config"
curl -s "${API_URL}/auth/config" | grep -q keycloak && ok "api is in keycloak mode" || bad "api auth mode"
[ "$(code "${API_URL}/plants")" = 401 ] && ok "api rejects anonymous calls" || bad "api /plants without token"
[ "$(code "${API_URL}/docs")" = 404 ] && ok "api docs hidden in production" || bad "api /docs reachable"
[ "$(code "${WEB_URL}/login")" = 200 ] && ok "web /login" || bad "web /login"
[ "$(code "${KC_URL}/realms/plantguard/.well-known/openid-configuration")" = 200 ] && ok "keycloak realm" || bad "keycloak realm"
for h in strict-transport-security x-content-type-options x-frame-options; do
  curl -sI "${API_URL}/auth/config" | grep -qi "^$h" && ok "header $h" || bad "header $h"
done
say "Internal services are not public"
for s in engine worker; do
  c="$(code "$(run_url "${NAME}-$s")/auth/config")"; [ "$c" = 403 ] || [ "$c" = 404 ] && ok "$s not reachable ($c)" || bad "$s answered $c"
done
say "Readiness inside (job logs)"
gcloud run jobs executions list --job="${NAME}-migrate" --region="$REGION" --limit=1 \
  --format='value(name,status.conditions[0].type,status.conditions[0].status)'
exit "$FAILED"
