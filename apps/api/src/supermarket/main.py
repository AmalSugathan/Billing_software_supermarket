"""Phase 1 identity, catalog and immutable inventory foundation."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError

from supermarket.catalog import catalog_router
from supermarket.commerce import commerce_router
from supermarket.config import Settings
from supermarket.corrections import corrections_router
from supermarket.database import build_engine, database_ready
from supermarket.finance import finance_router
from supermarket.identity import identity_router
from supermarket.offline import offline_router
from supermarket.purchases import purchases_router


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
        description=(
            "Phase 1 online store operations. "
            "Offline billing and release gates remain under development."
        ),
        lifespan=lifespan,
    )

    @application.exception_handler(SQLAlchemyError)
    async def database_error(_: Request, __: SQLAlchemyError) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": "Database operation unavailable"})

    application.include_router(identity_router(database, configuration))
    application.include_router(catalog_router(database, configuration))
    application.include_router(purchases_router(database, configuration))
    application.include_router(finance_router(database, configuration))
    application.include_router(commerce_router(database, configuration))
    application.include_router(corrections_router(database, configuration))
    application.include_router(offline_router(database, configuration))

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
