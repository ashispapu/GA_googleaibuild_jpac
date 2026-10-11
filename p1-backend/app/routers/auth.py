from fastapi import APIRouter, Depends

from plantguard_contracts.schemas.auth import TokenRequest, TokenResponse, User
from plantguard_contracts.schemas.security import AccessClaims, AuthConfig

from app.container import Container
from app.routers.deps import container, current_user
from app.services.auth import user_from_claims

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/config", response_model=AuthConfig)
def auth_config(c: Container = Depends(container)):
    """No auth needed. Tells the UI whether to show the local login form or redirect to Keycloak."""
    return c.verifier.config()


@router.post("/token", response_model=TokenResponse)
def login(body: TokenRequest, c: Container = Depends(container)):
    """Local mode only. In keycloak mode the UI logs in at Keycloak (see /auth/config)."""
    return c.login.login(body)


@router.get("/me", response_model=User)
def me(claims: AccessClaims = Depends(current_user)):
    """The logged-in user's id, name, role, plants and permissions."""
    return user_from_claims(claims)
