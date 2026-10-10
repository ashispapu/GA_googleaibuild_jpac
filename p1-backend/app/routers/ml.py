from fastapi import APIRouter, Depends

from plantguard_contracts.schemas.ml import AssetHealth, ModelInfo, PredictRequest
from plantguard_contracts.schemas.security import AccessClaims

from app.container import Container
from app.routers.deps import VIEW, container

router = APIRouter(prefix="/ml", tags=["ml"])


@router.get("/assets/{asset_id}/health", response_model=AssetHealth)
def health(asset_id: str, claims: AccessClaims = VIEW, c: Container = Depends(container)):
    """GET /ml/assets/{asset_id}/health"""
    return c.health.get_health(asset_id)


@router.post("/predict", response_model=AssetHealth)
def predict(body: PredictRequest, claims: AccessClaims = VIEW, c: Container = Depends(container)):
    """POST /ml/predict: score a supplied feature window."""
    return c.health.predict(body.window)


@router.get("/models", response_model=list[ModelInfo])
def models(claims: AccessClaims = VIEW, c: Container = Depends(container)):
    """GET /ml/models"""
    return c.health.models()
