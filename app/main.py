from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request, status
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.db import SessionLocal, init_db
from app.deps import AuthContext, LoginRequired, optional_context, required_context
from app.models import User
from app.ocr import ocr_is_ready
from app.routers import admin, auth, scans
from app.security import hash_password

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    with SessionLocal() as db:
        if db.query(User).filter(User.username == settings.admin_username.lower()).first() is None:
            db.add(
                User(
                    username=settings.admin_username.lower(),
                    full_name="Administrator",
                    password_hash=hash_password(settings.admin_password),
                    role="admin",
                    must_change_password=False,
                )
            )
            db.commit()
    yield


app = FastAPI(
    title=settings.app_name,
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    telemetry={"auto_configure": False},
)
app.mount("/static", StaticFiles(directory=settings.base_dir / "app" / "static"), name="static")


@app.get("/brand/logo.png", include_in_schema=False)
async def brand_logo() -> FileResponse:
    return FileResponse(settings.base_dir / "templates" / "gs-battery-logo-2.png", media_type="image/png")


@app.get("/brand/watermark.png", include_in_schema=False)
async def brand_watermark() -> FileResponse:
    return FileResponse(settings.base_dir / "templates" / "gs-battery-logo-2 1.png", media_type="image/png")


@app.get("/healthz", include_in_schema=False)
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/status")
def system_status(_: AuthContext = Depends(required_context)) -> dict[str, bool]:
    return {"ocr_ready": ocr_is_ready()}


app.include_router(auth.router)
app.include_router(scans.router)
app.include_router(admin.router)
app.include_router(admin.ml_router)


@app.exception_handler(LoginRequired)
async def login_required(_: Request, __: LoginRequired) -> RedirectResponse:
    return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data: blob:; media-src 'self' blob:; "
        "connect-src 'self'; form-action 'self'; frame-ancestors 'none'; "
        "object-src 'none'; base-uri 'self'"
    )
    forwarded = request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
    if settings.is_production and (forwarded == "https" or request.url.scheme == "https"):
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.get("/")
async def index(ctx: AuthContext | None = Depends(optional_context)):
    destination = "/scan" if ctx is not None else "/login"
    return RedirectResponse(destination, status_code=307)
