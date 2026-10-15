#!/usr/bin/env bash
# Private CA + server certificate for the Qdrant service (TLS inside the VPC).
#   deploy/qdrant/make_certs.sh <out-dir> <dns-name> [ip]
# Writes ca.pem (give to the clients as QDRANT_CA_CERT), ca.key (keep offline / delete), server.pem, server.key.
# Python 3.13+ rejects CAs without keyUsage, so the extensions below are required, not cosmetic.
set -euo pipefail
OUT=${1:?out dir}; DNS=${2:?dns name}; IP=${3:-}
mkdir -p "$OUT"; cd "$OUT"; umask 077
openssl req -x509 -newkey rsa:3072 -nodes -keyout ca.key -out ca.pem -days 3650 -sha256 \
  -subj "/O=PlantGuard/CN=PlantGuard internal CA" \
  -addext "basicConstraints=critical,CA:TRUE,pathlen:0" \
  -addext "keyUsage=critical,keyCertSign,cRLSign" \
  -addext "subjectKeyIdentifier=hash" 2>/dev/null
openssl req -newkey rsa:2048 -nodes -keyout server.key -out server.csr -subj "/O=PlantGuard/CN=$DNS" 2>/dev/null
SAN="DNS:$DNS,DNS:localhost"; [ -n "$IP" ] && SAN="$SAN,IP:$IP"
cat > server.ext <<EXT
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
subjectAltName=$SAN
authorityKeyIdentifier=keyid
EXT
openssl x509 -req -in server.csr -CA ca.pem -CAkey ca.key -CAcreateserial -out server.pem -days 825 -sha256 \
  -extfile server.ext 2>/dev/null
rm -f server.csr server.ext ca.srl
chmod 644 ca.pem server.pem
echo "certificates in $OUT (CN=$DNS${IP:+, IP $IP})"
