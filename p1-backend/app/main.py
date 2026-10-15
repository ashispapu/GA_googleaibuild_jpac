"""PlantGuard AI real backend. `app` is built by create_app(); tests and uvicorn both use it.
Run:  PYTHONPATH=.:p1-backend uvicorn app.main:app --port 8000
Contract tests: make test-p1"""
import asyncio
import contextlib
import logging
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from plantguard_contracts.schemas.common import ErrorResponse

from app.core.config import Settings
from app.container import Container, build_container, seed_demo_redaction
from app.core.errors import install_error_handlers
from app.routers import (audit, auth, energy, engineering, erp, garden, incidents, maintenance, mes, ml, ot, plants,
                         quality, security, simulator, workforce)
from app.core.observability import RequestContextMiddleware, environment, is_production, setup_logging
from app.middleware.rate_limit import RateLimitMiddleware, SharedRateLimiter, TokenBucketLimiter

setup_logging()
log = logging.getLogger("plantguard")
DEFAULT_SECRET = "demo-secret-change-me"


def check_production_config(c: Container) -> None:
    """PLANTGUARD_ENV=prod refuses to start with demo settings."""
    if not is_production():
        return
    problems = []
    mode = getattr(c.verifier.config().mode, "value", c.verifier.config().mode)
    if mode == "local" and (c.verifier.secret == DEFAULT_SECRET or len(c.verifier.secret) < 32):
        problems.append("JWT_SECRET must be a random value of at least 32 characters (Secret Manager)")
    if "*" in c.settings.cors_origins:
        problems.append("CORS_ORIGINS must list the web app's origin(s), not *")
    if os.getenv("SIM_RESET", "").lower() in ("1", "true", "yes") and c.role != "migrate":
        problems.append("SIM_RESET must not be set on a production service")
    if problems:
        raise RuntimeError("production configuration refused: " + "; ".join(problems))
    if mode == "local":
        log.warning("AUTH_MODE=local in production: users sign in with PlantGuard passwords. Keycloak (AUTH_MODE="
                    "keycloak) is the recommended production setup")


async def _warm_llm() -> None:
    import importlib.util
    if importlib.util.find_spec("litellm") is None:
        return
    try:
        try:
            from llm.providers.litellm_provider import import_litellm  # same lock as the agents' first call
        except ImportError:  # agents package not on the path (fixtures mode): nothing will import it concurrently
            import_litellm = lambda: importlib.import_module("litellm")  # noqa: E731
        await asyncio.to_thread(import_litellm)
        log.info("LLM client library loaded")
    except Exception:  # never stop the app over a warm-up
        log.exception("LLM warm-up failed (it will load on first use)")


def create_app(container: Container | None = None, settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI app: container, middleware, error handlers, all routers and background workers. Tests pass their
    own container/settings."""
    c = container or build_container(settings)
    seed_demo_redaction(c)
    check_production_config(c)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        """On startup warn about the demo JWT secret, open the hero incident if the starting scene is not calm, and start
        the scoring and alert-opener tasks (if enabled); cancel them on shutdown."""
        if c.verifier.secret == DEFAULT_SECRET:
            log.warning("JWT_SECRET is the demo default; set it in .env before any real use")
        log.info("PlantGuard API starting: role=%s env=%s shared=%s", c.role, environment(), c.shared is not None)
        if c.engine is None and c.settings.open_incident_on_start and not c.scene.is_calm():
            # The scene listener only fires on a scene *change*; starting in warning/crisis must still open the hero
            # incident, otherwise the overview shows the risk while /incidents/INC-0042 is 404.
            await c.scene.set(c.scene.scenario)
        tasks = [asyncio.create_task(w.run()) for w in (c.scoring, c.alert_opener) if w is not None]
        tasks += [asyncio.create_task(start()) for start in c.background]  # simulated plant: engine, listeners
        # LiteLLM takes ~3 s to import. Imported lazily on the first agent call it would freeze the event loop (API,
        # WebSockets, plant tick) for that long, so import it now in a worker thread.
        if c.role in ("all", "worker"):
            tasks.append(asyncio.create_task(_warm_llm()))
        app.state.started = True
        try:
            yield
        finally:
            app.state.started = False
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    errors = {s: {"model": ErrorResponse} for s in (401, 403, 404, 409, 422, 429)}
    docs = not is_production() or os.getenv("API_DOCS", "").lower() in ("1", "true", "yes")
    app = FastAPI(title="PlantGuard AI API", version="1.0.0", responses=errors, lifespan=lifespan,
                  description="Contract v1. Real backend (P1).", docs_url="/docs" if docs else None,
                  redoc_url="/redoc" if docs else None, openapi_url="/openapi.json" if docs else None)
    app.state.container = c
    app.state.started = False
    if c.shared is not None:  # several API instances: one limit for all of them
        limiter = SharedRateLimiter(c.shared, c.settings.rate_limit_per_min, c.settings.rate_limit_burst)
    else:
        limiter = TokenBucketLimiter(c.settings.rate_limit_per_min, c.settings.rate_limit_burst)
    app.add_middleware(RateLimitMiddleware, limiter=limiter)
    wildcard = "*" in c.settings.cors_origins
    app.add_middleware(CORSMiddleware, allow_origins=list(c.settings.cors_origins), allow_credentials=not wildcard,
                       allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
                       allow_headers=["Authorization", "Content-Type", "X-Request-ID", "Idempotency-Key"],
                       expose_headers=["X-Request-ID", "Retry-After"], max_age=600)
    app.add_middleware(RequestContextMiddleware, hsts=is_production())
    install_error_handlers(app)

    @app.get("/healthz", include_in_schema=False)
    def healthz():
        """Liveness: the process answers (Cloud Run liveness probe)."""
        return {"status": "ok", "role": c.role}

    @app.get("/readyz", include_in_schema=False)
    def readyz():
        """Readiness: every backing service answers (startup probe / load balancer)."""
        checks = {}
        for name, fn in c.readiness.items():
            try:
                checks[name] = "ok" if fn() is not False else "failing"
            except Exception as e:  # noqa: BLE001
                checks[name] = f"failing: {type(e).__name__}"
        ok = all(v == "ok" for v in checks.values()) and app.state.started
        return JSONResponse({"status": "ready" if ok else "not_ready", "role": c.role, "checks": checks},
                            status_code=200 if ok else 503)

    for r in (auth, plants, ot, erp, mes, maintenance, quality, engineering, workforce, energy, ml, incidents, garden, audit,
              security, simulator):
        app.include_router(r.router)
    return app


app = create_app()
