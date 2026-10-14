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
from app.routers import (audit, auth, energy, engineering, erp, garden, incidents, maintenance, mes, ml, ot, plants,
                         quality, security, simulator, workforce)
from app.middleware.rate_limit import RateLimitMiddleware, TokenBucketLimiter

log = logging.getLogger("plantguard")
DEFAULT_SECRET = "demo-secret-change-me"


async def _warm_llm() -> None:
    import importlib.util
    if importlib.util.find_spec("litellm") is None:
        return
    try:
        await asyncio.to_thread(importlib.import_module, "litellm")
        log.info("LLM client library loaded")
    except Exception:  # never stop the app over a warm-up
        log.exception("LLM warm-up failed (it will load on first use)")


def create_app(container: Container | None = None, settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI app: container, middleware, error handlers, all routers and background workers. Tests pass their
    own container/settings."""
    c = container or build_container(settings)
    seed_demo_redaction(c)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        """On startup warn about the demo JWT secret, open the hero incident if the starting scene is not calm, and start
        the scoring and alert-opener tasks (if enabled); cancel them on shutdown."""
        if c.verifier.secret == DEFAULT_SECRET:
            log.warning("JWT_SECRET is the demo default; set it in .env before any real use")
        if c.engine is None and c.settings.open_incident_on_start and not c.scene.is_calm():
            # The scene listener only fires on a scene *change*; starting in warning/crisis must still open the hero
            # incident, otherwise the overview shows the risk while /incidents/INC-0042 is 404.
            await c.scene.set(c.scene.scenario)
        tasks = [asyncio.create_task(w.run()) for w in (c.scoring, c.alert_opener) if w is not None]
        tasks += [asyncio.create_task(start()) for start in c.background]  # simulated plant: engine, listeners
        # LiteLLM takes ~3 s to import. Imported lazily on the first agent call it would freeze the event loop (API,
        # WebSockets, plant tick) for that long, so import it now in a worker thread.
        tasks.append(asyncio.create_task(_warm_llm()))
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
    for r in (auth, plants, ot, erp, mes, maintenance, quality, engineering, workforce, energy, ml, incidents, garden, audit,
              security, simulator):
        app.include_router(r.router)
    return app


app = create_app()
