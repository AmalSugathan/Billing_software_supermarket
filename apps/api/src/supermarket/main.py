"""Health-only API baseline; operational APIs wait for identity and tenant isolation."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError

from supermarket.config import Settings
from supermarket.database import build_engine, database_ready
from supermarket.identity import identity_router


class HealthResponse(BaseModel):
    status: Literal["alive", "ready", "unavailable"]


def create_app(settings: Settings | None = None, engine: Engine | None = None) -> FastAPI:
    configuration = settings if settings is not None else Settings.from_environment()
    owned_engine = engine is None and configuration.database_url is not None
    database = engine
    if owned_engine and configuration.database_url is not None:
        database = build_engine(configuration.database_url)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
        try:
            yield
        finally:
            if owned_engine and database is not None:
                database.dispose()

    application = FastAPI(
        title="Supermarket platform",
        version="0.1.0",
        description="Phase 1 identity and business setup. Billing modules are under development.",
        lifespan=lifespan,
    )

    @application.exception_handler(SQLAlchemyError)
    async def database_error(_: Request, __: SQLAlchemyError) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": "Database operation unavailable"})

    application.include_router(identity_router(database, configuration))

    @application.get("/api/v1/health/live", response_model=HealthResponse, tags=["health"])
    def live(response: Response) -> HealthResponse:
        response.headers["Cache-Control"] = "no-store"
        return HealthResponse(status="alive")

    @application.get(
        "/api/v1/health/ready",
        response_model=HealthResponse,
        tags=["health"],
        responses={503: {"model": HealthResponse, "description": "Database/schema unavailable"}},
    )
    def ready(response: Response) -> HealthResponse:
        response.headers["Cache-Control"] = "no-store"
        available = database_ready(database)
        response.status_code = 200 if available else 503
        return HealthResponse(status="ready" if available else "unavailable")

    return application
