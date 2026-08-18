"""
FastAPI main application
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.routes import (
    admin,
    auth,
    evaluation_prompt,
    evaluation_requirement,
    hackathon,
    internal_jobs,
    metric_scoring,
    settings,
    submissions,
    theme,
)
from app.utils.async_io import run_sync
from app.utils.cors_config import (
    api_docs_enabled,
    get_allowed_origins,
    get_cors_allow_headers,
    get_cors_allow_methods,
    get_cors_expose_headers,
)
from app.utils.seeder import DatabaseSeeder, seed_on_startup_enabled
from app.exceptions import (
    AppError,
    InfrastructureError,
    status_code_for_value_error_message,
)


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

_DOCS_ENABLED = api_docs_enabled()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan context manager for FastAPI
    """
    # Startup
    logger.info("🚀 FastAPI application starting...")

    # Phase 9: shared Firebase / GCS / service graph (request-immutable singletons).
    from app.dependencies import init_app_container

    container = init_app_container(app)

    # Phase 8: gate seeder behind SEED_ON_STARTUP (default true = today's behaviour).
    if seed_on_startup_enabled():
        try:
            logger.info("🌱 Initializing database...")
            seeder = DatabaseSeeder(
                firebase=container.firebase,
                user_service=container.user_service,
            )
            await run_sync(seeder.seed_all)
            logger.info("Database initialization completed")
        except Exception as e:
            logger.warning(f"Database seeding encountered an issue: {str(e)}")
            # Don't fail startup if seeding fails
    else:
        logger.info("Skipping database seeding (SEED_ON_STARTUP=false)")

    yield
    # Shutdown
    logger.info("FastAPI application shutting down...")


# Create FastAPI app (Phase 12: no /docs|/redoc|/openapi.json in production).
app = FastAPI(
    title="AI Hackathon Evaluator Backend",
    description="FastAPI backend that evaluates hackathon submission videos with AI",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs" if _DOCS_ENABLED else None,
    redoc_url="/redoc" if _DOCS_ENABLED else None,
    openapi_url="/openapi.json" if _DOCS_ENABLED else None,
)

# CORS: credentials + explicit origins; methods/headers match SPA preflights.
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_allowed_origins(),
    allow_credentials=True,
    allow_methods=get_cors_allow_methods(),
    allow_headers=get_cors_allow_headers(),
    expose_headers=get_cors_expose_headers(),
)


# ==================== Routes ====================
@app.get("/health", tags=["health"])
async def health_check():
    """Health check endpoint"""
    from app.utils.time import now_ist_iso

    return {
        "status": "healthy",
        "message": "AI Hackathon Evaluator Backend is running",
        "timezone": "Asia/Kolkata",
        "server_time": now_ist_iso(),
    }


# Include routers
app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(settings.router)
app.include_router(submissions.router)
app.include_router(hackathon.router)
app.include_router(theme.router)
app.include_router(evaluation_requirement.router)
app.include_router(evaluation_prompt.router)
app.include_router(metric_scoring.router)
app.include_router(internal_jobs.router)


# ==================== Error Handlers (Phase 6) ====================
@app.exception_handler(AppError)
async def app_error_handler(request, exc: AppError):
    """Typed application errors — same status codes clients already expect."""
    if exc.status_code >= 500:
        logger.exception("AppError %s: %s", exc.status_code, exc.detail)
        # Keep 5xx client body generic for infrastructure; detail may be ops-facing.
        client_detail = (
            exc.detail
            if not isinstance(exc, InfrastructureError)
            else "Service temporarily unavailable"
        )
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": client_detail},
        )
    logger.warning("AppError %s: %s", exc.status_code, exc.detail)
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
    )


@app.exception_handler(ValueError)
async def value_error_handler(request, exc: ValueError):
    """
    Map uncaught ValueError to the same 400/404/409/413 rules routes already use.
    """
    code = status_code_for_value_error_message(str(exc))
    if code >= 500:
        logger.exception("ValueError mapped to %s: %s", code, str(exc))
    else:
        logger.warning("ValueError mapped to %s: %s", code, str(exc))
    return JSONResponse(
        status_code=code,
        content={"detail": str(exc)},
    )


@app.exception_handler(Exception)
async def general_exception_handler(request, exc: Exception):
    """Unhandled errors: log stack trace; generic client body."""
    logger.exception("Unhandled exception")
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )


# ==================== Root Endpoint ====================
@app.get("/", tags=["root"])
async def root():
    """Root endpoint"""
    payload = {
        "status": "success",
        "message": "Welcome to AI Hackathon Evaluator backend",
    }
    if _DOCS_ENABLED:
        payload["docs"] = "/docs"
        payload["openapi_schema"] = "/openapi.json"
    return payload


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )
