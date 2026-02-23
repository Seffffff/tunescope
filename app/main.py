"""
FastAPI application factory.
"""
import asyncio
from app.ingestion.manual_analyzer import _warmup_librosa

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import auth, playlists, ingestion
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.db.session import engine
from app.db.models import models  # noqa: F401

settings = get_settings()
logger = get_logger(__name__)

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    logger.info("app_starting", env="debug" if settings.debug else "production")

    from app.db.session import Base
    # from app.db.models.models import (  # noqa
    #     User, Playlist, PlaylistTrack, Track, AudioFeatures, RawSpotifyPayload
    # )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    logger.info("database_tables_ready")

    loop = asyncio.get_event_loop()         
    await loop.run_in_executor(None, _warmup_librosa)

    yield
    logger.info("app_shutting_down")
    await engine.dispose()


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version="1.0.0",
        description="Music metadata enrichment platform powered by Spotify",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # API routes
    app.include_router(auth.router)
    app.include_router(playlists.router)
    app.include_router(ingestion.router)

    # Serve the frontend SPA at /app
    @app.get("/app")
    async def frontend():
        return FileResponse(STATIC_DIR / "index.html")

    # Root → frontend
    @app.get("/")
    async def root():
        return RedirectResponse(url="/app")

    @app.get("/health")
    async def health():
        return {"status": "ok", "service": settings.app_name}

    # Bearer token support for /docs
    def custom_openapi():
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(
            title=app.title,
            version=app.version,
            description=app.description,
            routes=app.routes,
        )
        schema["components"]["securitySchemes"] = {
            "BearerAuth": {"type": "http", "scheme": "bearer"}
        }
        for path in schema["paths"].values():
            for method in path.values():
                method["security"] = [{"BearerAuth": []}]
        app.openapi_schema = schema
        return schema

    app.openapi = custom_openapi

    return app


app = create_app()