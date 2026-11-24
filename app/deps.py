from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import User
from app.security import (
    CSRF_COOKIE,
    CSRF_HEADER,
    SESSION_COOKIE,
    csrf_matches,
    session_by_token,
    verify_pre_csrf,
)


class LoginRequired(Exception):
    pass


@dataclass
class AuthContext:
    user: User
    session: object
    token: str


def optional_context(request: Request, db: Session = Depends(get_db)) -> AuthContext | None:
    token = request.cookies.get(SESSION_COOKIE)
    session = session_by_token(db, token)
    if session is None:
        return None
    user = db.get(User, session.user_id)
    if user is None or not user.is_active:
        return None
    return AuthContext(user=user, session=session, token=token or "")


def _wants_html(request: Request) -> bool:
    return "text/html" in request.headers.get("accept", "").lower() and not request.url.path.startswith("/api/")


def required_context(
    request: Request,
    ctx: AuthContext | None = Depends(optional_context),
) -> AuthContext:
    if ctx is None:
        if _wants_html(request):
            raise LoginRequired()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sesi tidak valid")
    if ctx.user.must_change_password and request.url.path not in {"/change-password", "/logout"}:
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            detail="Password harus diganti",
            headers={"Location": "/change-password"},
        )
    return ctx


def admin_context(ctx: AuthContext = Depends(required_context)) -> AuthContext:
    if ctx.user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Akses ditolak")
    return ctx


async def _supplied_csrf(request: Request) -> str | None:
    supplied = request.headers.get(CSRF_HEADER)
    if supplied:
        return supplied
    try:
        form = await request.form()
        value = form.get("csrf")
        return str(value) if value else None
    except Exception:
        return None


async def validate_csrf(request: Request, ctx: AuthContext = Depends(required_context)) -> None:
    supplied = await _supplied_csrf(request)
    if not csrf_matches(ctx.session, supplied):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Token CSRF tidak valid")


async def validate_pre_csrf(request: Request) -> None:
    cookie_token = request.cookies.get(CSRF_COOKIE)
    supplied = await _supplied_csrf(request)
    if not cookie_token or not supplied or cookie_token != supplied:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Token CSRF tidak valid")
    if not verify_pre_csrf(cookie_token):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Token CSRF tidak valid")
