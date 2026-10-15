#!/usr/bin/env bash
# Qdrant JWT (HS256, signed with the Qdrant API key) naming exactly the collections a client may use.
# Same tokens as `python -m app.rag.qdrant_tokens`, with nothing but bash + openssl.
#   QDRANT_ADMIN_API_KEY=... deploy/qdrant/mint_token.sh <app|plant|readonly> <prefix> <PLANT-A,PLANT-B> [days]
set -euo pipefail
ROLE=${1:?app|plant|readonly}; PREFIX=${2:?collection prefix}; PLANTS=${3:?plants}; DAYS=${4:-90}
KEY=${QDRANT_ADMIN_API_KEY:?set QDRANT_ADMIN_API_KEY}
b64url() { openssl base64 -A | tr '+/' '-_' | tr -d '='; }
IFS=',' read -r -a P <<< "$PLANTS"
[ "$ROLE" = plant ] && [ "${#P[@]}" -ne 1 ] && { echo "role plant takes exactly one plant" >&2; exit 2; }
W=r; [ "$ROLE" = app ] || [ "$ROLE" = plant ] && W=rw
ACCESS="{\"collection\":\"${PREFIX}_manuals\",\"access\":\"r\"}"
for p in "${P[@]}"; do
  c="${PREFIX}_workorders_$(echo "$p" | tr 'A-Z-' 'a-z_')"
  ACCESS="$ACCESS,{\"collection\":\"$c\",\"access\":\"$W\"}"
done
EXP=""; [ "$DAYS" != 0 ] && EXP=",\"exp\":$(( $(date +%s) + DAYS * 86400 ))"
H=$(printf '{"alg":"HS256","typ":"JWT"}' | b64url)
B=$(printf '{"access":[%s],"sub":"plantguard-%s"%s}' "$ACCESS" "$ROLE" "$EXP" | b64url)
S=$(printf '%s.%s' "$H" "$B" | openssl dgst -sha256 -hmac "$KEY" -binary | b64url)
printf '%s.%s.%s\n' "$H" "$B" "$S"
