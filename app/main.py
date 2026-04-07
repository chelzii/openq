from __future__ import annotations

from dataclasses import dataclass

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.api.routes import build_router
from app.apps.services import BankApp, GalleryApp, MailApp, ProtectedStateStore, WeatherApp
from app.audit.service import AuditService
from app.chain.crypto import RequestSigner
from app.chain.fisco import FiscoBcosService
from app.core.experiments import DemoOrchestrator
from app.core.gateway import GatewayService, SandboxDispatcher
from app.core.openclaw import OpenClawFacade
from app.core.settings import Settings
from app.guards.embedding import BGEEmbeddingEncoder, BGEReranker
from app.guards.intent import IntentGuard
from app.state.store import StateIntegrityService


@dataclass
class AppContainer:
    settings: Settings
    signer: RequestSigner
    chain: FiscoBcosService
    audit: AuditService
    state_store: ProtectedStateStore
    integrity: StateIntegrityService
    gateway: GatewayService
    orchestrator: DemoOrchestrator


def build_container() -> AppContainer:
    settings = Settings.load()
    signer = RequestSigner()
    chain = FiscoBcosService(
        settings.chain_registry,
        settings.signer_identity,
        settings.chain_contract_source,
        settings.chain_console_contract_dir,
        console_script=settings.chain_console_script,
        probe_ports=settings.chain_probe_ports,
    )
    audit = AuditService(settings.audit_log)
    state_store = ProtectedStateStore(settings.state_dir)
    integrity = StateIntegrityService(state_store, settings.baseline_file)
    dispatcher = SandboxDispatcher(
        mail=MailApp(settings.messages_fixture),
        bank=BankApp(),
        gallery=GalleryApp(settings.assets_fixture),
        weather=WeatherApp(),
        state_store=state_store,
        integrity=integrity,
    )
    gateway = GatewayService(
        signer=signer,
        guard=IntentGuard(
            encoder=BGEEmbeddingEncoder(),
            reranker=BGEReranker(),
            calibration_fixture=settings.intent_calibration_fixture,
            calibration_report=settings.guard_calibration_report,
        ),
        chain=chain,
        dispatcher=dispatcher,
        audit=audit,
    )
    orchestrator = DemoOrchestrator(
        signer=signer,
        chain=chain,
        gateway=gateway,
        openclaw=OpenClawFacade(settings.real_openclaw_url),
        scenario_fixture=settings.scenario_fixture,
    )
    return AppContainer(
        settings=settings,
        signer=signer,
        chain=chain,
        audit=audit,
        state_store=state_store,
        integrity=integrity,
        gateway=gateway,
        orchestrator=orchestrator,
    )


def create_app() -> FastAPI:
    container = build_container()
    app = FastAPI(title="OpenQ", version="0.1.0")
    templates = Jinja2Templates(directory=str(container.settings.templates_dir))
    app.state.container = container
    app.mount("/static", StaticFiles(directory=str(container.settings.static_dir)), name="static")
    app.include_router(build_router(templates))
    return app


app = create_app()
