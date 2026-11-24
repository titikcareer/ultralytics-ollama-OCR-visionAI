import re

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.deps import AuthContext, required_context, validate_csrf, validate_pre_csrf
from app.models import User, utcnow
from app.render import render
from app.security import (
    CSRF_COOKIE,
    SESSION_COOKIE,
    add_audit,
    client_ip,
    create_session,
    hash_password,
    login_limiter,
    needs_rehash,
    new_csrf_token,
    revoke_session,
    verify_password,
)

settings = get_settings()
router = APIRouter(tags=["auth"])

USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]{3,64}$")
PASSWORD_MIN = 12


def _secure_cookie(request: Request) -> bool:
    return settings.secure_cookies and request.url.scheme == "https"


def _cookie_args(request: Request) -> dict:
    return {
        "httponly": False,
        "secure": _secure_cookie(request),
        "samesite": "lax",
        "path": "/",
    }


@router.get("/login")
async def login_page(request: Request, db: Session = Depends(get_db)):
    token = new_csrf_token()
    response = render(request, "login.html", db, csrf=token, error=None)
    response.set_cookie(CSRF_COOKIE, token, max_age=3600, **_cookie_args(request))
    return response


@router.post("/login")
async def login(request: Request, db: Session = Depends(get_db)):
    await validate_pre_csrf(request)
    form = await request.form()
    username = str(form.get("username") or "").strip().lower()
    password = str(form.get("password") or "")
    ip = client_ip(request)

    if not USERNAME_RE.fullmatch(username) or not password:
        raise HTTPException(status_code=400, detail="Nama pengguna atau kata sandi salah.")

    if login_limiter.is_locked(username, ip):
        raise HTTPException(status_code=429, detail="Terlalu banyak percobaan. Silakan coba lagi beberapa saat lagi.")

    user = db.query(User).filter(User.username == username).one_or_none()
    valid = user is not None and user.is_active and verify_password(password, user.password_hash)

    if not valid:
        login_limiter.record_failure(username, ip)
        add_audit(db, "login_failed", user=user, target=username, ip_address=ip)
        token = new_csrf_token()
        response = render(
            request,
            "login.html",
            db,
            csrf=token,
            error="Nama pengguna atau kata sandi salah.",
        )
        response.set_cookie(CSRF_COOKIE, token, max_age=3600, **_cookie_args(request))
        response.status_code = 401
        return response

    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
        db.commit()

    login_limiter.clear(username, ip)
    user.last_login_at = utcnow()
    session_token, csrf = create_session(db, user, ip)
    add_audit(db, "login", user=user, ip_address=ip)

    landing = "/change-password" if user.must_change_password else "/scan"
    response = RedirectResponse(landing, status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        SESSION_COOKIE,
        session_token,
        max_age=settings.session_ttl_minutes * 60,
        httponly=True,
        secure=_secure_cookie(request),
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        CSRF_COOKIE,
        csrf,
        max_age=settings.session_ttl_minutes * 60,
        **_cookie_args(request),
    )
    return response


@router.post("/logout")
async def logout(
    request: Request,
    ctx: AuthContext = Depends(required_context),
    db: Session = Depends(get_db),
    _: None = Depends(validate_csrf),
):
    add_audit(db, "logout", user=ctx.user, ip_address=client_ip(request))
    revoke_session(db, request.cookies.get(SESSION_COOKIE))
    response = RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")
    return response


@router.get("/change-password")
async def change_password_page(
    request: Request,
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(required_context),
):
    return render(request, "change_password.html", db, error=None)


@router.post("/change-password")
async def change_password(
    request: Request,
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(required_context),
    _: None = Depends(validate_csrf),
):
    form = await request.form()
    current = str(form.get("current_password") or "")
    new_password = str(form.get("new_password") or "")

    if not verify_password(current, ctx.user.password_hash):
        return render(request, "change_password.html", db, error="Kata sandi lama salah.")
    if len(new_password) < PASSWORD_MIN:
        return render(request, "change_password.html", db, error=f"Kata sandi baru minimal {PASSWORD_MIN} karakter.")

    ctx.user.password_hash = hash_password(new_password)
    ctx.user.must_change_password = False
    db.commit()
    add_audit(db, "password_changed", user=ctx.user, ip_address=client_ip(request))
    revoke_session(db, request.cookies.get(SESSION_COOKIE))

    response = RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")
    return response
