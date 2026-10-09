"""Token verification shared by the mock server and P1's real backend.

    verifier = TokenVerifier.from_env()
    claims = verifier.verify(bearer_token)   # -> AccessClaims, or raises InvalidToken

AUTH_MODE=local     HS256 tokens signed by the backend (JWT_SECRET). Dev, tests, demo fallback.
AUTH_MODE=keycloak  RS256 tokens from Keycloak, checked against its public keys (JWKS):
                    signature, issuer, audience, expiry. Roles -> permissions stays in the backend.
Approval tokens are ALWAYS signed by the backend (HS256, JWT_SECRET), in both modes.
"""
import os
import time

import jwt

from .schemas.security import AccessClaims, AuthConfig, AuthMode, KeycloakClaims, to_access_claims

ACCESS_TTL = 15 * 60
LEEWAY_S = 30


class InvalidToken(Exception):
    pass


class TokenVerifier:
    def __init__(self, mode: AuthMode, secret: str, issuer: str | None = None, audience: str | None = None,
                 client_id: str | None = None, jwks_client: "jwt.PyJWKClient | None" = None,
                 public_key=None):
        self.mode, self.secret = mode, secret
        self.issuer, self.audience, self.client_id = issuer, audience, client_id
        self.jwks_client, self.public_key = jwks_client, public_key  # public_key: tests only

    @classmethod
    def from_env(cls) -> "TokenVerifier":
        mode = AuthMode(os.getenv("AUTH_MODE", "local"))
        secret = os.getenv("JWT_SECRET", "demo-secret-change-me")
        if mode == AuthMode.LOCAL:
            return cls(mode, secret)
        realm = os.getenv("KEYCLOAK_REALM", "plantguard")
        public = os.getenv("KEYCLOAK_URL", "http://localhost:8080").rstrip("/")         # what browsers use = issuer
        internal = os.getenv("KEYCLOAK_INTERNAL_URL", public).rstrip("/")               # e.g. http://keycloak:8080 in Docker
        return cls(mode, secret, issuer=f"{public}/realms/{realm}",
                   audience=os.getenv("KEYCLOAK_AUDIENCE", "plantguard-api"),
                   client_id=os.getenv("KEYCLOAK_CLIENT_ID", "plantguard-web"),
                   jwks_client=jwt.PyJWKClient(f"{internal}/realms/{realm}/protocol/openid-connect/certs",
                                               cache_keys=True))

    # ---- local mode only ----
    def issue_local(self, user_id: str, name: str, role: str, plant_ids: list[str]) -> str:
        if self.mode != AuthMode.LOCAL:
            raise RuntimeError("tokens are issued by Keycloak in keycloak mode")
        t = int(time.time())
        claims = AccessClaims(sub=user_id, name=name, role=role, plant_ids=plant_ids, iat=t, exp=t + ACCESS_TTL)
        return jwt.encode(claims.model_dump(mode="json"), self.secret, algorithm="HS256")

    # ---- both modes ----
    def verify(self, token: str) -> AccessClaims:
        if not token:
            raise InvalidToken("missing token")
        try:
            if self.mode == AuthMode.LOCAL:
                return AccessClaims(**jwt.decode(token, self.secret, algorithms=["HS256"], leeway=LEEWAY_S))
            key = self.public_key or self.jwks_client.get_signing_key_from_jwt(token).key
            raw = jwt.decode(token, key, algorithms=["RS256"], issuer=self.issuer, audience=self.audience,
                             leeway=LEEWAY_S, options={"require": ["exp", "iat", "iss", "aud"]})
            return to_access_claims(KeycloakClaims(**raw))
        except InvalidToken:
            raise
        except Exception as e:  # bad signature, expired, wrong issuer/audience, missing claims, no role
            raise InvalidToken(str(e)) from e

    def config(self) -> AuthConfig:
        if self.mode == AuthMode.LOCAL:
            return AuthConfig(mode=self.mode)
        oidc = f"{self.issuer}/protocol/openid-connect"
        return AuthConfig(mode=self.mode, issuer=self.issuer, client_id=self.client_id,
                          authorize_url=f"{oidc}/auth", token_url=f"{oidc}/token", logout_url=f"{oidc}/logout")
