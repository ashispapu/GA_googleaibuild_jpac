"""Helpers for the Windows deploy scripts (deploy/gcp/*.bat). Standard library only, except `certs`
(needs `pip install cryptography`, already installed with the backend's requirements).

  python tools.py random N OUTFILE              random secret (letters + digits), no newline
  python tools.py strip FILE                    remove trailing whitespace/newlines in place (gcloud output -> secret)
  python tools.py certs OUTDIR DNS              private CA + server certificate for Qdrant (ca.pem, server.pem, server.key)
  python tools.py token ROLE PREFIX PLANTS DAYS KEYFILE OUTFILE
                                                Qdrant JWT naming exactly its collections (role app | plant | readonly)
  python tools.py backend-env OUTFILE ROLE [K=V ...]   Cloud Run env-vars YAML for a backend role (reads the step variables)
  python tools.py envfile OUTFILE K=V [K=V ...] Cloud Run env-vars YAML from arguments
  python tools.py lifecycle OUTFILE             Cloud Storage lifecycle rules for the backup bucket
  python tools.py alerts CHANNEL                create the error log metric and the alert policies that are missing
  python tools.py lf IN OUT                     copy a text file with Linux line endings (the VM boot script)
  python tools.py tag                           image tag: git commit, else a timestamp
  python tools.py verify                        smoke test of the deployed URLs (reads the step variables)

The .bat files call it as  %PY% "%~dp0tools.py" ...  (PY defaults to python; set PY=py in config.cmd if needed).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import string
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


def env(name: str, default: str | None = None) -> str:
    v = os.environ.get(name, default)
    if v is None:
        raise SystemExit(f"variable {name} is not set (run the step through its .bat file)")
    return v


def write(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


# ---------------------------------------------------------------- secrets
def cmd_random(n: str, out: str) -> None:
    alphabet = string.ascii_letters + string.digits  # nothing that cmd.exe, URLs or YAML treat specially
    write(out, "".join(secrets.choice(alphabet) for _ in range(int(n))))


def cmd_strip(path: str) -> None:
    write(path, Path(path).read_text(encoding="utf-8-sig").strip())


# ---------------------------------------------------------------- certificates
def cmd_certs(outdir: str, dns: str) -> None:
    """Same certificates as deploy/qdrant/make_certs.sh. Python 3.13+ rejects a CA without keyUsage, so the
    extensions are required, not cosmetic."""
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
    except ImportError:
        raise SystemExit("needs the cryptography package:  pip install cryptography")
    import datetime as dt
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    now = dt.datetime.now(dt.timezone.utc)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    ca_name = x509.Name([x509.NameAttribute(NameOID.ORGANIZATION_NAME, "PlantGuard"),
                         x509.NameAttribute(NameOID.COMMON_NAME, "PlantGuard internal CA")])
    ski = x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key())
    ca = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name).public_key(ca_key.public_key())
          .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(minutes=5))
          .not_valid_after(now + dt.timedelta(days=3650))
          .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
          .add_extension(x509.KeyUsage(digital_signature=False, content_commitment=False, key_encipherment=False,
                                       data_encipherment=False, key_agreement=False, key_cert_sign=True, crl_sign=True,
                                       encipher_only=False, decipher_only=False), critical=True)
          .add_extension(ski, critical=False)
          .sign(ca_key, hashes.SHA256()))
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    cert = (x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.ORGANIZATION_NAME, "PlantGuard"),
                                     x509.NameAttribute(NameOID.COMMON_NAME, dns)]))
            .issuer_name(ca_name).public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(minutes=5)).not_valid_after(now + dt.timedelta(days=825))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False, key_encipherment=True,
                                         data_encipherment=False, key_agreement=False, key_cert_sign=False,
                                         crl_sign=False, encipher_only=False, decipher_only=False), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(dns), x509.DNSName("localhost")]), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_subject_key_identifier(ski), critical=False)
            .sign(ca_key, hashes.SHA256()))
    pem = serialization.Encoding.PEM
    (out / "ca.pem").write_bytes(ca.public_bytes(pem))
    (out / "server.pem").write_bytes(cert.public_bytes(pem))
    (out / "server.key").write_bytes(key.private_bytes(pem, serialization.PrivateFormat.TraditionalOpenSSL,
                                                       serialization.NoEncryption()))
    print(f"certificates in {out} (CN={dns}); the CA private key was not saved")


# ---------------------------------------------------------------- Qdrant token
def qdrant_claims(role: str, prefix: str, plants: list[str], days: float) -> dict:
    if role not in ("app", "plant", "readonly"):
        raise SystemExit("role must be app, plant or readonly")
    if role == "plant" and len(plants) != 1:
        raise SystemExit("role plant takes exactly one plant")
    write_mode = "rw" if role in ("app", "plant") else "r"
    access = [{"collection": f"{prefix}_manuals", "access": "r"}]
    access += [{"collection": f"{prefix}_workorders_{p.lower().replace('-', '_')}", "access": write_mode} for p in plants]
    claims: dict = {"access": access, "sub": f"plantguard-{role}"}
    if days > 0:
        claims["exp"] = int(time.time() + days * 86400)
    return claims


def jwt_hs256(claims: dict, key: str) -> str:
    b64 = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=").decode()  # noqa: E731
    head = b64(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    body = b64(json.dumps(claims, separators=(",", ":")).encode())
    sig = b64(hmac.new(key.encode(), f"{head}.{body}".encode(), hashlib.sha256).digest())
    return f"{head}.{body}.{sig}"


def cmd_token(role: str, prefix: str, plants: str, days: str, keyfile: str, out: str) -> None:
    key = Path(keyfile).read_text(encoding="utf-8-sig").strip()
    write(out, jwt_hs256(qdrant_claims(role, prefix, [p.strip() for p in plants.split(",") if p.strip()],
                                       float(days)), key))


# ---------------------------------------------------------------- Cloud Run env files
def yaml_env(values: dict[str, str]) -> str:
    return "".join(f"{k}: {json.dumps(str(v))}\n" for k, v in values.items())


def backend_values(role: str) -> dict[str, str]:
    name, envname = env("NAME"), env("ENV")
    return {
        "PLANTGUARD_ROLE": role, "PLANTGUARD_ENV": envname, "DATA_MODE": "sim", "LOG_FORMAT": "json",
        "GOOGLE_CLOUD_PROJECT": env("PROJECT_ID"),
        "DB_HOST": env("SQL_IP"), "DB_USER": "plantguard", "DB_NAME_OT": "plantguard_ot", "DB_NAME_IT": "plantguard_it",
        "DB_SSLMODE": "require", "PLANTGUARD_DB_POOL_SIZE": "8",
        "REDIS_HOST": env("REDIS_HOST"), "REDIS_PORT": "6378", "REDIS_TLS": "true",
        "REDIS_CA_CERT": "/etc/plantguard/redis/ca.pem", "PLANTGUARD_REDIS_PREFIX": f"{name}-{envname}",
        "QDRANT_URL": f"https://{env('QDRANT_HOST')}:6333", "QDRANT_CA_CERT": "/etc/plantguard/qdrant/ca.pem",
        "PLANTGUARD_VECTOR_PREFIX": env("VECTOR_PREFIX"),
        "GOOGLE_GENAI_USE_ENTERPRISE": "True", "GOOGLE_CLOUD_LOCATION": env("VERTEX_LOCATION", "global"),
        "AUTH_MODE": "keycloak", "KEYCLOAK_URL": env("KC_URL"), "KEYCLOAK_REALM": "plantguard",
        "KEYCLOAK_AUDIENCE": "plantguard-api", "KEYCLOAK_CLIENT_ID": "plantguard-web", "CORS_ORIGINS": env("WEB_URL"),
        "WORKER_CONCURRENCY": env("WORKER_CONCURRENCY", "4"),
    }


def pairs(args: list[str]) -> dict[str, str]:
    out = {}
    for a in args:
        k, sep, v = a.partition("=")
        if not sep:
            raise SystemExit(f"expected KEY=VALUE, got {a!r}")
        out[k] = v
    return out


def cmd_backend_env(out: str, role: str, *extra: str) -> None:
    write(out, yaml_env({**backend_values(role), **pairs(list(extra))}))


def cmd_envfile(out: str, *kv: str) -> None:
    write(out, yaml_env(pairs(list(kv))))


def cmd_lifecycle(out: str) -> None:
    write(out, json.dumps({"rule": [
        {"action": {"type": "Delete"}, "condition": {"age": 30}},
        {"action": {"type": "Delete"}, "condition": {"isLive": False, "daysSinceNoncurrentTime": 7}}]}))


# ---------------------------------------------------------------- monitoring
def gcloud(*args: str, capture: bool = True) -> str:
    import shutil
    exe = shutil.which("gcloud") or "gcloud"  # gcloud.cmd on Windows
    r = subprocess.run([exe, *args], capture_output=capture, text=True)
    if r.returncode:
        raise SystemExit(f"gcloud {' '.join(args)} failed:\n{r.stderr}")
    return r.stdout


def cmd_alerts(channel: str) -> None:
    """Log-based error metric + alert policies (skips the ones that exist). Done here rather than in the .bat file
    because the filters are full of quotes and >= that cmd.exe would mangle."""
    name = env("NAME")
    metrics = set(gcloud("logging", "metrics", "list", "--format=value(name)").split())
    if f"{name}_backend_errors" not in metrics:
        metric = {"name": f"{name}_backend_errors", "description": "ERROR lines of the PlantGuard backend services",
                  "filter": f'resource.type="cloud_run_revision" AND '
                            f'resource.labels.service_name=~"^{name}-(api|engine|worker)$" AND severity>=ERROR'}
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(metric, f)
        try:
            gcloud("logging", "metrics", "create", f"{name}_backend_errors", f"--config-from-file={f.name}")
        finally:
            os.unlink(f.name)
        print(f"  log metric {name}_backend_errors: created")
    existing = set(gcloud("monitoring", "policies", "list", "--format=value(displayName)").splitlines())
    run = 'resource.type="cloud_run_revision"'
    policies = [
        ("PlantGuard API 5xx", f'{run} AND resource.labels.service_name="{name}-api" AND '
         'metric.type="run.googleapis.com/request_count" AND metric.labels.response_code_class="5xx"', 5, "ALIGN_RATE"),
        ("PlantGuard backend errors", f'{run} AND metric.type="logging.googleapis.com/user/{name}_backend_errors"',
         0.2, "ALIGN_RATE"),
        ("PlantGuard Cloud SQL CPU > 80%", 'resource.type="cloudsql_database" AND '
         'metric.type="cloudsql.googleapis.com/database/cpu/utilization"', 0.8, "ALIGN_MEAN"),
        ("PlantGuard Redis memory > 80%", 'resource.type="redis_instance" AND '
         'metric.type="redis.googleapis.com/stats/memory/usage_ratio"', 0.8, "ALIGN_MEAN"),
        ("PlantGuard Qdrant VM CPU > 85%", 'resource.type="gce_instance" AND '
         'metric.type="compute.googleapis.com/instance/cpu/utilization" AND metadata.user_labels.role="qdrant"',
         0.85, "ALIGN_MEAN"),
    ]
    for display, flt, threshold, aligner in policies:
        if display in existing:
            print(f"  {display}: exists")
            continue
        body = {"displayName": display, "combiner": "OR", "notificationChannels": [channel],
                "conditions": [{"displayName": display, "conditionThreshold": {
                    "filter": flt, "comparison": "COMPARISON_GT", "thresholdValue": threshold, "duration": "300s",
                    "aggregations": [{"alignmentPeriod": "60s", "perSeriesAligner": aligner}]}}],
                "documentation": {"content": "See docs/GCP_DEPLOYMENT_GUIDE.md, section Operations.",
                                  "mimeType": "text/markdown"}}
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(body, f)
        try:
            gcloud("monitoring", "policies", "create", f"--policy-from-file={f.name}")
        finally:
            os.unlink(f.name)
        print(f"  {display}: created")


# ---------------------------------------------------------------- misc
def cmd_lf(src: str, out: str) -> None:
    Path(out).write_bytes(Path(src).read_bytes().replace(b"\r\n", b"\n"))


def cmd_tag() -> None:
    try:
        tag = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                             cwd=Path(__file__).parent).stdout.strip()
    except OSError:
        tag = ""
    sys.stdout.write(tag or time.strftime("%Y%m%d%H%M"))


def cmd_verify() -> None:
    api, web, kc = env("API_URL"), env("WEB_URL"), env("KC_URL")
    run = lambda svc: f"https://{env('NAME')}-{svc}-{env('PROJECT_NUMBER')}.{env('REGION')}.run.app"  # noqa: E731
    failed = 0

    def get(url):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, method="GET"), timeout=20) as r:
                return r.status, r.read().decode(errors="replace"), dict(r.headers)
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode(errors="replace"), dict(e.headers)
        except Exception as e:  # noqa: BLE001
            return 0, str(e), {}

    def check(ok, what):
        nonlocal failed
        print(("  ok    " if ok else "  FAIL  ") + what)
        failed += 0 if ok else 1

    s, _, h = get(f"{api}/healthz")
    check(s == 200, "api /healthz")
    headers = {k.lower() for k in h}
    for hd in ("strict-transport-security", "x-content-type-options", "x-frame-options"):
        check(hd in headers, f"api header {hd}")
    s, body, _ = get(f"{api}/auth/config")
    check(s == 200 and "keycloak" in body, "api is in keycloak mode")
    check(get(f"{api}/plants")[0] == 401, "api refuses anonymous calls")
    check(get(f"{api}/docs")[0] == 404, "api docs hidden in production")
    check(get(f"{web}/login")[0] == 200, "web /login")
    check(get(f"{kc}/realms/plantguard/.well-known/openid-configuration")[0] == 200, "keycloak realm")
    for svc in ("engine", "worker"):
        s = get(f"{run(svc)}/healthz")[0]
        check(s in (403, 404), f"{svc} is not reachable from the internet ({s})")
    raise SystemExit(1 if failed else 0)


COMMANDS = {"random": cmd_random, "strip": cmd_strip, "certs": cmd_certs, "token": cmd_token,
            "backend-env": cmd_backend_env, "envfile": cmd_envfile, "lifecycle": cmd_lifecycle,
            "alerts": cmd_alerts, "lf": cmd_lf, "tag": cmd_tag, "verify": cmd_verify}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(__doc__)
        raise SystemExit(2)
    COMMANDS[sys.argv[1]](*sys.argv[2:])
