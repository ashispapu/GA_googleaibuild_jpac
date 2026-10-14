"""Composition root for the simulated plant (DATA_MODE=sim). Same Container as fixtures mode; every port gets the
database-backed implementation and the plant engine, scorer, workflow, garden and sessions are wired in."""
from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone

from plantguard_contracts.auth import TokenVerifier
from plantguard_contracts.schemas.simulator import Scenario

from app.connectors.health import LatestScores
from app.connectors.sql import (SqlEnergy, SqlEngineering, SqlERP, SqlMaintenance, SqlMES, SqlOT, SqlPlants, SqlQuality,
                                SqlWorkforce)
from app.connectors.telemetry.tag_map import Tag, TagMap
from app.core.config import Settings
from app.infrastructure.event_bus import InMemoryEventBus
from app.plants_static import StaticPlants
from app.rag.corpus import load_manuals
from app.rag.retriever import FaissRetriever
from app.rag.vector_store import FaissStore
from app.repositories.incident_repository import SqlIncidentRepository
from app.services.approval_chain import ApprovalPolicy, ApprovalSettings
from app.services.option_chat import OptionChat
from app.services.action_executor import ActionExecutor, PurchaseOrderHandler, SpeedChangeHandler, ToolWriteHandler
from app.services.approval_tokens import ApprovalTokenService
from app.services.auth import AccessPolicy, Authenticator, LoginService
from app.services.confidence_updater import ConfidenceUpdater
from app.services.garden import GardenService
from app.services.incident_lifecycle import AlertIncidentOpener
from app.services.incident_service import IncidentService
from app.services.ot_gateway import OTGateway, QualityPolicy
from app.services.plant_directory import PlantDirectory
from app.services.redaction_log import RedactionLog
from app.services.repair_workflow import RepairWorkflow
from app.services.run_coordinator import RunCoordinator, is_final
from app.services.scene_controller import SceneController
from app.services.tool_gateway import ToolGateway
from app.services.tool_handlers import ToolBackends, build_handlers
from app.services.write_authorizer import WriteAuthorizer
from app.sim.clock import SimClock, iso, parse_ts
from app.sim.db import open_databases
from app.sim.engine import PlantEngine
from app.sim.health_model import SignatureFeatureExtractor, SignatureHealthModel, backtest
from app.sim.identity import DbSequence, SessionStore, SqlAuditLog, SqlUserDirectory
from app.sim.scoring import RunningOnlyScorer, SimHealthProvider, SimScoringService, historian_past, hours_since_service
from app.sim.seed import seed, wipe
from app.sim.settings import load_sim_settings

log = logging.getLogger("plantguard.sim")


def build_sim_container(s: Settings, verifier: TokenVerifier | None = None):
    from app.container import Container
    root = s.fixtures_dir.parents[1]
    cfg = load_sim_settings(root, s.sim_ini)
    if cfg.reset_on_start or os.getenv("SIM_RESET", "").lower() in ("1", "true", "yes"):
        wipe(cfg.data_dir)
    ot, it = open_databases(cfg.data_dir)
    fresh = it.meta("seeded_at") is None
    start = parse_ts(it.meta("plant_time")) if not fresh else datetime.now(timezone.utc).replace(microsecond=0)
    clock = SimClock(start, cfg.scale)
    if fresh:
        log.warning("seeding a new simulated plant in %s", cfg.data_dir)
        seed(ot, it, cfg, clock())
    verifier = verifier or TokenVerifier.from_env()

    bus = InMemoryEventBus()
    scene = SceneController(bus, Scenario.CALM, clock)  # scenes belong to the scripted story; kept for API parity
    audit = SqlAuditLog(it, clock)
    policy = AccessPolicy(audit)
    tokens = ApprovalTokenService(verifier.secret)

    engine = PlantEngine(cfg, ot, it, clock, bus)
    scores = LatestScores()
    plants = SqlPlants(ot, it, engine, scores)
    directory = PlantDirectory(StaticPlants(it))
    tags = TagMap([Tag(r["tag"], r["asset_id"], r["signal"], r["unit"], r["protocol"], r["range_min"], r["range_max"])
                   for r in ot.q("SELECT * FROM tags")])
    stale_after = cfg.telemetry_interval_s * max(cfg.scale, 1.0) * 5  # five missed readings, in plant seconds
    ot_gateway = OTGateway(engine, QualityPolicy(tags, stale_after), clock)
    ot_conn = SqlOT(ot, engine, clock)
    ot_conn.subscribe = ot_gateway.stream  # live readings go through the OT gateway's quality rules
    erp, mes = SqlERP(it, clock), SqlMES(it, clock, engine)
    maintenance, quality = SqlMaintenance(it, clock), SqlQuality(it, clock)
    engineering, workforce = SqlEngineering(it), SqlWorkforce(it, clock)
    energy = SqlEnergy(ot, it, clock, cfg.models, cfg.plant["tariffs"], cfg.plant["co2_kg_per_kwh"])
    store = FaissStore.open(cfg.data_dir / "vdb")
    if store is None or store.size == 0:
        it.x("DELETE FROM doc_chunks")
        retriever = FaissRetriever.build(it, cfg.data_dir / "vdb", load_manuals(root / "p4-ml-data" / "corpus" / "manuals"),
                                         iso(clock()))
    else:
        retriever = FaissRetriever(it, store)

    # ---- health model (C5) on live readings ----
    eq = {r["asset_id"]: r["type"] for r in ot.q("SELECT asset_id, type FROM equipment")}
    baselines = {a: cfg.models[t]["baseline"] for a, t in eq.items()}
    rule = {a: cfg.models[t]["alarm"]["vibration_crit"] for a, t in eq.items()}
    extractor = SignatureFeatureExtractor(baselines, historian_past(ot))
    model = SignatureHealthModel(baselines, cfg.plant["signatures"], rule, trained_at=clock())
    model.metrics = backtest(model, baselines, cfg.plant["signatures"], rule)
    hours = hours_since_service(it, clock)
    health = SimHealthProvider(model, extractor, scores, ot, hours, clock, cfg.window_min)
    engine.health = health

    # ---- incidents, tools, agents ----
    repo = SqlIncidentRepository(it)
    authorizer = WriteAuthorizer(tokens, repo)
    workflow_ref: dict = {}
    handlers = build_handlers(ToolBackends(ot_conn, erp, mes, maintenance, quality, engineering, retriever, workforce,
                                           energy, health, incident_context=lambda i: workflow_ref["w"].context(i)))
    tool_gateway = ToolGateway(handlers, audit, authorizer)
    tool_gateway.plant_of_incident = lambda i: repo.get(i).incident.plant_id
    redactions = RedactionLog()
    runtime = _plant_runtime(s, tool_gateway, redactions, clock, cfg.agents_on_llm_failure)
    runtime_factory = lambda speed: runtime  # noqa: E731
    executor = ActionExecutor([
        SpeedChangeHandler(mes, audit),
        ToolWriteHandler("work_order", "create_work_order", tool_gateway, audit),
        ToolWriteHandler("stock_transfer", "create_stock_transfer", tool_gateway, audit),
        PurchaseOrderHandler(erp, authorizer, audit)])
    incidents = IncidentService(repo, directory, health, audit, bus, tokens, executor, runtime, policy,
                                ConfidenceUpdater(), DbSequence(it, "APR", "APR-", 7), DbSequence(it, "FB", "FB-", 31), clock)
    runs = RunCoordinator(repo, bus, runtime_factory, audit, clock)
    workflow = RepairWorkflow(incidents, it, engine, erp, mes, maintenance, workforce, retriever, audit, clock, cfg)
    workflow_ref["w"] = workflow
    incidents.workflow = workflow
    incidents.approval_policy = ApprovalPolicy(ApprovalSettings(
        enabled=cfg.approvals_enabled, manager_cosign_over_eur=cfg.manager_cosign_over_eur,
        manager_cosign_stop_h=cfg.manager_cosign_stop_h, source_plant_release=cfg.source_plant_release))
    workflow.start_run = lambda incident_id: runs.ensure_started(incident_id)
    incidents.on_opened.append(lambda rec: runs.ensure_started(rec.incident.id))  # agents start by themselves
    garden = GardenService(bus, clock, workflow.stage, repo)
    runs.listeners.append(garden.on_event)
    incidents.on_status.append(garden.on_status)
    incidents.on_opened.append(garden.on_status)
    garden.rebuild()

    sessions = SessionStore(it)
    users = SqlUserDirectory(it)
    scorer = RunningOnlyScorer(extractor, model, bus, scores, directory, hours, clock, min_samples=6)
    scoring = SimScoringService(engine, scorer, cfg.score_interval_s)
    alert_opener = AlertIncidentOpener(incidents, bus)

    async def follow(channel: str, handler):
        async for payload in bus.subscribe(channel):
            try:
                await handler(payload)
            except Exception:
                log.exception("%s handler failed", channel)

    async def recover():
        """Analyses interrupted by a restart start again (their partial events are dropped)."""
        for rec in repo.all():
            inc = rec.incident
            if inc.status.value != "analysing":
                continue
            if any(is_final(e) and e.seq >= rec.round_first_seq for e in rec.events):
                continue
            partial = [e for e in rec.events if e.seq >= rec.round_first_seq]
            if partial:
                it.x("DELETE FROM incident_events WHERE incident_id=? AND seq>=?", (inc.id, rec.round_first_seq))
                rec.events = [e for e in rec.events if e.seq < rec.round_first_seq]
            rec.run_round = inc.round - 1
            runs.ensure_started(inc.id)

    c = Container(s, bus, scene, verifier, Authenticator(verifier, sessions), LoginService(verifier, users, audit, sessions),
                  policy, audit, tokens, authorizer, redactions, plants, directory, ot_conn, ot_gateway, erp, mes,
                  maintenance, quality, engineering, retriever, workforce, energy, health, scores, tool_gateway, incidents,
                  runs, engine, scoring=scoring, alert_opener=alert_opener, clock=clock, engine=engine, workflow=workflow,
                  garden=garden, sessions=sessions,
                  option_chat=OptionChat(incidents, it, audit, clock, workflow.user))
    c.background = [engine.run, lambda: follow("sim.verification", workflow.on_verification),
                    lambda: follow("ot.alarm", workflow.on_alarm), recover]
    return c


def _plant_runtime(s: Settings, gateway, redactions, clock, on_failure: str):
    p2 = str(s.agents_dir)
    if p2 not in sys.path:
        sys.path.append(p2)
    from harness.factory import plant_runtime_from_config  # type: ignore
    return plant_runtime_from_config(gateway, config_path=s.agents_ini, redaction_sink=redactions.add, clock=clock,
                                     on_llm_failure=on_failure)
