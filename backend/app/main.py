from __future__ import annotations

from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path
from time import monotonic

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware

from .api import public_router, router
from .config import Settings, settings
from .db import SessionLocal, init_db
from .draft.errors import DraftDomainError
from .draft.router import router as draft_router
from .draft.simulation import recover_interrupted_computations
from .models import Owner
from .pool_errors import PoolDomainError
from .routes.analysis_library import router as analysis_library_router
from .routes.injuries import router as injuries_router
from .routes.league_analysis import router as league_analysis_router
from .routes.pools import router as pools_router
from .scheduler import recover_source_jobs, start_scheduler, stop_scheduler
from .security import hash_password
from .services.injury_report import recover_injury_checks
from .services.league_analysis import recover_league_analyses
from .services.news import ensure_default_sources


def bootstrap_owner(db, configuration: Settings = settings) -> None:
    """An existing owner remains valid when its bootstrap password is removed."""
    if db.query(Owner).first():
        return
    if configuration.auth_required and not configuration.owner_password:
        raise RuntimeError(
            "OWNER_PASSWORD is required to initialize an authenticated installation. "
            "Set it for the first start; an existing owner's password is never overwritten."
        )
    if configuration.owner_password:
        db.add(
            Owner(
                username=configuration.owner_username,
                password_hash=hash_password(configuration.owner_password),
            )
        )
        db.commit()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings.validate()
    settings.ensure_directories()
    init_db()
    db = SessionLocal()
    try:
        bootstrap_owner(db, settings)
        ensure_default_sources(db)
        recover_interrupted_computations(db)
        recover_league_analyses(db)
        recover_injury_checks(db)
        recover_source_jobs(db)
    finally:
        db.close()
    start_scheduler()
    try:
        yield
    finally:
        stop_scheduler()


app = FastAPI(
    title="Open Gridiron API",
    version="0.1.0",
    description="Evidence-grounded fantasy football and NFL pool decision support.",
    lifespan=lifespan,
)


class WorkspaceCompression:
    """Compress read-only workspace payloads, not authentication or event streams."""

    def __init__(self, app):
        self.app = app
        self.compressed = GZipMiddleware(app, minimum_size=1000, compresslevel=5)

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        target = (
            self.compressed
            if (
                scope["type"] == "http"
                and scope.get("method") == "GET"
                and (path.startswith("/assets/") or path.startswith("/api/v1/leagues/"))
            )
            else self.app
        )
        await target(scope, receive, send)


app.add_middleware(WorkspaceCompression)


request_windows: dict[str, deque[float]] = defaultdict(deque)


@app.middleware("http")
async def rate_limit(request: Request, call_next):  # type: ignore[no-untyped-def]
    if not settings.auth_required or not request.url.path.startswith("/api/"):
        return await call_next(request)
    forwarded = request.headers.get("x-forwarded-for", "")
    client_ip = forwarded.split(",", 1)[0].strip() or (
        request.client.host if request.client else "unknown"
    )
    bucket_name = "login" if request.url.path.endswith("/auth/login") else "api"
    limit = 10 if bucket_name == "login" else 300
    key = f"{client_ip}:{bucket_name}"
    now = monotonic()
    window = request_windows[key]
    while window and window[0] < now - 60:
        window.popleft()
    if len(window) >= limit:
        return JSONResponse(
            status_code=429,
            content={"detail": "Rate limit exceeded; retry in one minute"},
            headers={"Retry-After": "60"},
        )
    window.append(now)
    response = await call_next(request)
    response.headers["X-RateLimit-Limit"] = str(limit)
    response.headers["X-RateLimit-Remaining"] = str(max(0, limit - len(window)))
    return response


@app.middleware("http")
async def security_headers(request: Request, call_next):  # type: ignore[no-untyped-def]
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if settings.auth_required:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


@app.exception_handler(ValueError)
async def value_error_handler(_request: Request, exc: ValueError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(PoolDomainError)
async def pool_domain_error_handler(_request: Request, exc: PoolDomainError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=jsonable_encoder({"error": exc.code, "message": exc.message, **exc.context}),
    )


@app.exception_handler(DraftDomainError)
async def draft_domain_error_handler(_request: Request, exc: DraftDomainError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=jsonable_encoder({"error": exc.code, "message": exc.message, **exc.context}),
    )


@app.get("/healthz", include_in_schema=False)
def healthz() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(public_router)
app.include_router(pools_router)
app.include_router(league_analysis_router)
app.include_router(analysis_library_router)
app.include_router(injuries_router)
if settings.draft_suite_enabled:
    app.include_router(draft_router)
app.include_router(router)

if settings.app_env == "test":
    from .testing.draft_fixtures import router as draft_fixtures_router

    app.include_router(draft_fixtures_router)


frontend_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if frontend_dist.exists():
    assets = frontend_dist / "assets"
    if assets.exists():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        requested = frontend_dist / path
        if (
            path
            and requested.is_file()
            and requested.resolve().is_relative_to(frontend_dist.resolve())
        ):
            return FileResponse(requested)
        return FileResponse(frontend_dist / "index.html")
else:

    @app.get("/", include_in_schema=False)
    def root() -> dict[str, str]:
        return {"name": "Open Gridiron", "docs": "/docs", "status": "frontend_not_built"}
