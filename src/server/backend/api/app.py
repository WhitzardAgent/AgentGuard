"""FastAPI application factory for the AgentGuard server."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.client_router import router as client_router
from backend.api.console_router import router as console_router
from backend.api.frontend_router import router as frontend_router
from backend.api.health_router import router as health_router
from backend.app_state import get_manager


def create_app() -> FastAPI:
    app = FastAPI(title="AgentGuard Server", version="0.3.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health_router)
    app.include_router(client_router)
    app.include_router(frontend_router)
    app.include_router(console_router)

    @app.on_event("shutdown")
    def _stop_session_health_monitor() -> None:
        get_manager().stop_session_health_monitor()

    return app


app = create_app()
