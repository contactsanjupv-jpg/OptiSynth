"""
Backend entry point. Run with:  uvicorn backend.app.main:app --reload --port 5050

This file's only job is ASSEMBLY -- it imports and wires together the
pieces built elsewhere (config, security, middleware, routes). It contains
no business logic itself.

Startup requires the database to already be at the latest Alembic
migration -- see verify_migrations_at_head() in config/database.py. Run
`alembic upgrade head` (from backend/) before starting the app; the app
refuses to start against a database that's behind, rather than silently
running against a schema the code doesn't actually match.
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app.config.settings import settings
from backend.app.config.database import verify_migrations_at_head
from backend.app.middleware.error_handling import register_error_handlers
from backend.app.middleware.security_headers import SecurityHeadersMiddleware
from backend.app.api.routes import (
    auth_routes, project_routes, dataset_routes, optimization_routes,
    report_routes, dashboard_routes, team_routes, change_case_routes,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    verify_migrations_at_head()
    yield


def create_app() -> FastAPI:
    logging.basicConfig(level=logging.INFO if settings.is_production else logging.DEBUG)

    app = FastAPI(
        title="OptiSynth API",
        description="Fewer experiments, same target -- R&D optimization backend.",
        # Hide interactive docs in production -- they reveal the full API
        # surface (including request/response schemas) to anyone.
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None if settings.is_production else "/redoc",
        lifespan=lifespan,
    )

    # allow_credentials=True is required so the session cookie is sent;
    # combined with a wildcard origin, browsers reject this outright -- so
    # this MUST be a specific origin, never "*". See CORS_ALLOWED_ORIGIN.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.CORS_ALLOWED_ORIGIN],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type"],
    )
    app.add_middleware(SecurityHeadersMiddleware)

    register_error_handlers(app)

    app.include_router(auth_routes.router)
    app.include_router(project_routes.router)
    app.include_router(dataset_routes.router)
    app.include_router(optimization_routes.router)
    app.include_router(optimization_routes.candidates_router)
    app.include_router(report_routes.router)
    app.include_router(dashboard_routes.router)
    app.include_router(team_routes.router)
    app.include_router(team_routes.public_router)
    app.include_router(change_case_routes.router)

    @app.get("/health")
    def health():
        return {"status": "ok"}


    return app


app = create_app()
