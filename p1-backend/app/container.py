"""Composition root: the only place that knows which concrete class implements which port.
To plug in a real SAP connector, P2's runtime or P4's model, change the line here and nothing else."""
import sys
from dataclasses import dataclass

from plantguard_contracts.auth import TokenVerifier
from plantguard_contracts.schemas.security import RedactionReport
from plantguard_contracts.schemas.simulator import Scenario

from app.infrastructure.event_bus import InMemoryEventBus
from app.core.common import IdSequence, utcnow
from app.core.config import Settings
from app.connectors.fixtures import (FixtureEnergy, FixtureEngineering, FixtureERP, FixtureMaintenance, FixtureMES,
                                  FixtureOT, FixturePlants, FixtureQuality, FixtureRetriever, FixtureWorkforce)
from app.connectors.store import FixtureStore
from app.services.plant_directory import PlantDirectory
from app.services.tool_handlers import ToolBackends, build_handlers
from app.services.tool_gateway import ToolGateway
from app.services.action_executor import ActionExecutor, SpeedChangeHandler, ToolWriteHandler
from app.services.confidence_updater import ConfidenceUpdater
from app.services.incident_lifecycle import SceneIncidentLifecycle
from app.repositories.incident_repository import InMemoryIncidentRepository
from app.services.run_coordinator import RunCoordinator
from app.services.agent_runtime import ReplayAgentRuntime
from app.services.incident_service import IncidentService
from app.connectors.health import FixtureHealthProvider, LatestScores, LiveHealthProvider
from app.services.ot_gateway import OTGateway, QualityPolicy
from app.services.line_speeds import LineSpeeds
from app.connectors.telemetry.synthetic import SyntheticTelemetrySource
from app.connectors.telemetry.tag_map import TagMap
from app.services.scene_controller import SceneController
from app.services.approval_tokens import ApprovalTokenService
from app.services.audit_log import HashChainAuditLog
from app.services.auth import AccessPolicy, Authenticator, LoginService
from app.services.redaction_log import RedactionLog
from app.repositories.user_directory import FixtureUserDirectory
from app.services.write_authorizer import WriteAuthorizer


@dataclass
class Container:
    """Holds every wired object. Routers get it through the `container` dependency."""

    settings: Settings
    bus: InMemoryEventBus
    scene: SceneController
    verifier: TokenVerifier
    authenticator: Authenticator
    login: LoginService
    policy: AccessPolicy
    audit: HashChainAuditLog
    tokens: ApprovalTokenService
    write_authorizer: WriteAuthorizer
    redactions: RedactionLog
    plants: FixturePlants
    directory: PlantDirectory
    ot: FixtureOT
    ot_gateway: OTGateway
    erp: FixtureERP
    mes: FixtureMES
    maintenance: FixtureMaintenance
    quality: FixtureQuality
    engineering: FixtureEngineering
    retriever: FixtureRetriever
    workforce: FixtureWorkforce
    energy: FixtureEnergy
    health: object
    scores: LatestScores
    tool_gateway: ToolGateway
    incidents: IncidentService
    runs: RunCoordinator
    telemetry_source: SyntheticTelemetrySource
    scoring: object | None = None  # ot.scoring.ScoringService when SCORING_ENABLED
    alert_opener: object | None = None


def build_container(settings: Settings | None = None, verifier: TokenVerifier | None = None) -> Container:
    """Composition root: create each object once, wire them together and return the Container (see STRUCTURE.md for the
    step-by-step)."""
    s = settings or Settings.from_env()
    clock = utcnow
    store = FixtureStore(s.fixtures_dir)
    verifier = verifier or TokenVerifier.from_env()

    bus = InMemoryEventBus()
    scene = SceneController(bus, s.initial_scenario, clock)
    audit = HashChainAuditLog(clock, seed=store.load("audit_seed"))
    policy = AccessPolicy(audit)
    tokens = ApprovalTokenService(verifier.secret)
    speeds = LineSpeeds()

    plants = FixturePlants(store, scene)
    directory = PlantDirectory(plants)
    telemetry_source = SyntheticTelemetrySource(directory, scene, speeds, s.hero_asset_id, s.telemetry_interval_s, clock)
    ot_gateway = OTGateway(telemetry_source, QualityPolicy(TagMap.load(s.tag_map_path)), clock)
    ot = FixtureOT(store, scene, directory, ot_gateway)
    erp, mes = FixtureERP(store, clock), FixtureMES(store, speeds)
    maintenance, quality = FixtureMaintenance(store, clock), FixtureQuality(store)
    engineering, retriever = FixtureEngineering(store), FixtureRetriever(store)
    workforce, energy = FixtureWorkforce(store), FixtureEnergy(store)
    scores = LatestScores()
    health = FixtureHealthProvider(store, scene)

    inc_ids = IdSequence("INC-", s.incident_id_start, width=4)
    repo = InMemoryIncidentRepository(inc_ids.next, inc_ids.reset)
    authorizer = WriteAuthorizer(tokens, repo)
    handlers = build_handlers(ToolBackends(ot, erp, mes, maintenance, quality, engineering, retriever, workforce,
                                           energy, health))
    tool_gateway = ToolGateway(handlers, audit, authorizer)

    replay_factory = lambda speed: ReplayAgentRuntime(s.agent_events_file, speed, clock)  # noqa: E731
    # P2: replace the primary with their runtime, e.g. lambda speed: FallbackAgentRuntime(their_runtime, replay_factory(speed))
    runtime_factory = replay_factory
    executor = ActionExecutor([
        SpeedChangeHandler(mes, audit),
        ToolWriteHandler("work_order", "create_work_order", tool_gateway, audit),
        ToolWriteHandler("stock_transfer", "create_stock_transfer", tool_gateway, audit)])
    incidents = IncidentService(repo, directory, health, audit, bus, tokens, executor, runtime_factory(1.0), policy,
                                ConfidenceUpdater(), IdSequence("APR-", 7, 4), IdSequence("FB-", 31, 4), clock)
    runs = RunCoordinator(repo, bus, runtime_factory, audit, clock)

    lifecycle = SceneIncidentLifecycle(incidents, s.hero_asset_id)

    async def reset_world(scenario: Scenario) -> None:
        """Scene listener: on calm/reset clear speeds, stock transfers and created work orders."""
        if scenario in (Scenario.RESET, Scenario.CALM):
            speeds.reset(), erp.reset(), maintenance.reset()
    scene.add_listener(reset_world)
    scene.add_listener(lifecycle.on_scene)

    redactions = RedactionLog()
    c = Container(s, bus, scene, verifier, Authenticator(verifier),
                  LoginService(verifier, FixtureUserDirectory(store), audit), policy, audit, tokens, authorizer,
                  redactions, plants, directory, ot, ot_gateway, erp, mes, maintenance, quality, engineering,
                  retriever, workforce, energy, health, scores, tool_gateway, incidents, runs, telemetry_source)
    if s.scoring_enabled:
        _enable_scoring(c, clock)
    return c


def _enable_scoring(c: Container, clock) -> None:
    """BE-4/BE-5: needs P4's model + extractor (p4-ml-data/ml). Falls back to fixture health if the model is missing."""
    from app.services.incident_lifecycle import AlertIncidentOpener
    from app.services.scoring import HealthScorer, ScoringService
    p4 = str(c.settings.model_path.parents[1])
    sys.path.insert(0, p4)
    from ml.features import WindowFeatureExtractor  # type: ignore
    from ml.serving import load_model  # type: ignore
    model = load_model(str(c.settings.model_path))
    c.health = LiveHealthProvider(model, c.scores, c.health)
    scorer = HealthScorer(WindowFeatureExtractor(), model, c.bus, c.scores, c.directory,
                          lambda asset_id: _hours_since_service(c, asset_id), clock)
    lines = [(p.id, l.id) for p in c.plants.list_plants() for l in p.lines]
    c.scoring = ScoringService(c.ot_gateway, scorer, c.bus, c.directory, lines, c.settings.scoring_interval_s, clock)
    c.alert_opener = AlertIncidentOpener(c.incidents, c.bus)


def _hours_since_service(c: Container, asset_id: str) -> float:
    """Hours since the asset's last completed work order ended (0 if none)."""
    from plantguard_contracts.schemas.maintenance import WorkOrderStatus
    done = [w for w in c.maintenance.get_history(asset_id) if w.status == WorkOrderStatus.DONE and w.planned_start]
    if not done:
        return 0.0
    last = max(done, key=lambda w: w.planned_start)
    return max(0.0, (utcnow() - last.planned_start).total_seconds() / 3600 - (last.duration_hours or 0))


def seed_demo_redaction(c: Container) -> None:
    """One sample so the Security panel is not empty before the first LLM call."""
    import hashlib
    h = lambda x: hashlib.sha256(x.encode()).hexdigest()  # noqa: E731
    c.redactions.add(RedactionReport(call_id="LLM-0001", agent="diagnosis", ts=utcnow(), model="claude-sonnet-4-5",
                                     chars_in=4120, chars_out=4064, findings=[
        {"kind": "serial_number", "original_hash": h("SN-88231-X"), "placeholder": "<SERIAL_1>"},
        {"kind": "person_name", "original_hash": h("Suresh"), "placeholder": "<PERSON_1>"},
        {"kind": "plc_tag", "original_hash": h("L3.ST2.SPDL2.VIB_RMS"), "placeholder": "<TAG_1>"}]))
