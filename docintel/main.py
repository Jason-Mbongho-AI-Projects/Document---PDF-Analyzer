"""
FastAPI application.

    uvicorn docintel.main:app --reload

Errors are normalised so that internal exception text never reaches a client;
the detail goes to the log with a correlation id the user can quote.
"""
import logging
import time
import uuid

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from docintel.api.v1 import (
    ai, annotations, auth, content, convert, documents, edit, jobs,
    signing, workspaces,
)
from docintel.config import settings
from docintel.db.session import engine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("docintel.api")

app = FastAPI(
    title="DocIntel API",
    version="1.0.0",
    description="Document intelligence platform",
    docs_url="/docs" if settings.environment != "production" else None,
    redoc_url=None,
)

if settings.environment != "production":
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000", "http://localhost:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


@app.middleware("http")
async def request_context(request: Request, call_next):
    request_id = uuid.uuid4().hex[:12]
    request.state.request_id = request_id
    started = time.perf_counter()

    response = await call_next(request)

    duration_ms = (time.perf_counter() - started) * 1000
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    logger.info("%s %s -> %s in %.1fms [%s]",
                request.method, request.url.path, response.status_code,
                duration_ms, request_id)
    return response


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    # Numeric literal rather than the constant: Starlette renamed
    # HTTP_422_UNPROCESSABLE_ENTITY to ..._CONTENT, so the name is a moving target.
    return JSONResponse(
        status_code=422,
        content={"detail": "Request validation failed", "errors": exc.errors()},
    )


@app.exception_handler(Exception)
async def unhandled_error(request: Request, exc: Exception):
    request_id = getattr(request.state, "request_id", "unknown")
    # Full detail to the log, an opaque reference to the client.
    logger.exception("unhandled error [%s]", request_id)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "detail": "An internal error occurred.",
            "request_id": request_id,
        },
    )


@app.get("/health", tags=["ops"])
def health():
    return {"status": "ok", "environment": settings.environment}


@app.get("/health/ready", tags=["ops"])
def readiness():
    checks = {}
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:
        logger.error("database readiness failed: %s", exc)
        checks["database"] = "unavailable"

    healthy = all(v == "ok" for v in checks.values())
    return JSONResponse(
        status_code=status.HTTP_200_OK if healthy else status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"status": "ready" if healthy else "degraded", "checks": checks},
    )


if settings.auth_open:
    logger.warning(
        "=" * 78 + "\n"
        "  AUTHENTICATION IS DISABLED (DOCINTEL_AUTH_MODE=open)\n"
        "  Every request is treated as the built-in development user.\n"
        "  Anyone who can reach this service has full access to every document.\n"
        "  Set DOCINTEL_AUTH_MODE=required before exposing this anywhere.\n"
        + "=" * 78
    )


API_V1 = "/api/v1"
app.include_router(auth.router, prefix=API_V1)
app.include_router(workspaces.router, prefix=API_V1)
app.include_router(documents.router, prefix=API_V1)
app.include_router(jobs.router, prefix=API_V1)
app.include_router(edit.router, prefix=API_V1)
app.include_router(annotations.router, prefix=API_V1)
app.include_router(content.router, prefix=API_V1)
app.include_router(ai.router, prefix=API_V1)
app.include_router(convert.router, prefix=API_V1)
app.include_router(signing.router, prefix=API_V1)
