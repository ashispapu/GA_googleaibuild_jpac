"""PlantGuard AI real backend. `app` is built by create_app(); tests and uvicorn both use it.
Run:  PYTHONPATH=.:p1-backend uvicorn app.main:app --port 8000
Contract tests: make test-p1"""
import asyncio
import contextlib
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from plantguard_contracts.schemas.common import ErrorResponse

from app.core.config import Settings
from app.container import Container, build_container, seed_demo_redaction
from app.core.errors import install_error_handlers
from app.routers import (audit, auth, energy, engineering, erp, incidents, maintenance, mes, ml, ot, plants, quality,
                         security, simulator, workforce)
from app.middleware.rate_limit import RateLimitMiddleware, TokenBucketLimiter

log = logging.getLogger("plantguard")
DEFAULT_SECRET = "demo-secret-change-me"


def create_app(container: Container | None = None, settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI app: container, middleware, error handlers, all routers and background workers. Tests pass their
    own container/settings."""
    c = container or build_container(settings)
    seed_demo_redaction(c)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        """On startup warn about the demo JWT secret and start the scoring and alert-opener tasks (if enabled); cancel
        them on shutdown."""
        if c.verifier.secret == DEFAULT_SECRET:
            log.warning("JWT_SECRET is the demo default; set it in .env before any real use")
        tasks = [asyncio.create_task(w.run()) for w in (c.scoring, c.alert_opener) if w is not None]
        try:
            yield
        finally:
            for t in tasks:
                t.cancel()

    errors = {s: {"model": ErrorResponse} for s in (401, 403, 404, 409, 422, 429)}
    app = FastAPI(title="PlantGuard AI API", version="1.0.0", responses=errors, lifespan=lifespan,
                  description="Contract v1. Real backend (P1).")
    app.state.container = c
    app.add_middleware(RateLimitMiddleware,
                       limiter=TokenBucketLimiter(c.settings.rate_limit_per_min, c.settings.rate_limit_burst))
    app.add_middleware(CORSMiddleware, allow_origins=list(c.settings.cors_origins), allow_methods=["*"],
                       allow_headers=["*"])
    install_error_handlers(app)
    for r in (auth, plants, ot, erp, mes, maintenance, quality, engineering, workforce, energy, ml, incidents, audit,
              security, simulator):
        app.include_router(r.router)
    return app


app = create_app()
