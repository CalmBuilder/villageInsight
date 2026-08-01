from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from village_insight.api.routes import (
    admin,
    auth,
    batches,
    catalog,
    files,
    health,
    questions,
    records,
    reviews,
)
from village_insight.api.routes import settings as settings_routes
from village_insight.config import get_settings
from village_insight.logging import configure_logging


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    settings.resolved_upload_root().mkdir(parents=True, exist_ok=True)
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="VillageInsight API",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health.router, prefix="/api/health")
    app.include_router(auth.router, prefix="/api")
    app.include_router(admin.router, prefix="/api")
    app.include_router(batches.router, prefix="/api")
    app.include_router(files.router, prefix="/api")
    app.include_router(catalog.router, prefix="/api")
    app.include_router(questions.router, prefix="/api")
    app.include_router(records.router, prefix="/api")
    app.include_router(reviews.router, prefix="/api")
    app.include_router(settings_routes.router, prefix="/api")
    return app


app = create_app()
