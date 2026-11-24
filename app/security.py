import hashlib
import hmac
import secrets
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import AuditLog, User, utcnow
from app.models import Session as UserSession

settings = get_settings()
_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4, hash_len=32, salt_len=16)

SESSION_COOKIE = "receipt_session"
CSRF_COOKIE = "receipt_csrf"
CSRF_HEADER = "X-CSRF-Token"


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        return _hasher.verify(stored_hash, password)
    except (VerifyMismatchError, InvalidHashError, ValueError):
        return False


def needs_rehash(stored_hash: str) -> bool:
    try:
        return _hasher.check_needs_rehash(stored_hash)
    except (InvalidHashError, ValueError):
        return True


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_token() -> str:
    return secrets.token_urlsafe(32)


def sign_csrf(nonce: str) -> str:
    signature = hmac.new(settings.secret_key.encode(), nonce.encode(), hashlib.sha256).hexdigest()
    return f"{nonce}.{signature}"


def verify_pre_csrf(token: str | None) -> bool:
    if not token or "." not in token:
        return False
    nonce, signature = token.rsplit(".", 1)
    expected = hmac.new(settings.secret_key.encode(), nonce.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, expected)


def new_csrf_token() -> str:
    return sign_csrf(secrets.token_urlsafe(24))


def create_session(db: Session, user: User, ip_address: str | None) -> tuple[str, str]:
    token = new_token()
    csrf = new_csrf_token()
    expires = utcnow() + timedelta(minutes=settings.session_ttl_minutes)
    db.add(
        UserSession(
            token_hash=token_hash(token),
            user_id=user.id,
            csrf_token=csrf,
            expires_at=expires,
            ip_address=ip_address,
        )
    )
    db.commit()
    return token, csrf


def session_by_token(db: Session, token: str | None) -> UserSession | None:
    if not token:
        return None
    row = (
        db.query(UserSession)
        .filter(UserSession.token_hash == token_hash(token), UserSession.revoked_at.is_(None))
        .one_or_none()
    )
    if row is None:
        return None
    expires = row.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    if expires <= datetime.now(UTC):
        return None
    return row


def revoke_session(db: Session, token: str | None) -> None:
    row = session_by_token(db, token)
    if row:
        row.revoked_at = utcnow()
        db.commit()


def csrf_matches(session: UserSession, supplied: str | None) -> bool:
    return bool(supplied) and hmac.compare_digest(session.csrf_token, supplied or "")


def client_ip(request) -> str | None:
    return request.client.host if request.client else None


def add_audit(
    db: Session,
    action: str,
    user: User | None = None,
    target: str | None = None,
    ip_address: str | None = None,
    detail: str = "",
) -> None:
    db.add(
        AuditLog(
            user_id=user.id if user else None,
            username=user.username if user else None,
            action=action,
            target=target,
            ip_address=ip_address,
            detail=detail[:2000],
        )
    )
    db.commit()


class LoginRateLimiter:
    def __init__(self) -> None:
        self._attempts: dict[str, list[float]] = {}
        self._locked: dict[str, float] = {}

    def _key(self, username: str, ip: str | None) -> str:
        return f"{(ip or 'unknown')}|{username.lower()}"

    def is_locked(self, username: str, ip: str | None) -> bool:
        key = self._key(username, ip)
        until = self._locked.get(key, 0)
        if until > time.time():
            return True
        self._locked.pop(key, None)
        self._attempts.pop(key, None)
        return False

    def record_failure(self, username: str, ip: str | None) -> None:
        key = self._key(username, ip)
        now = time.time()
        attempts = [t for t in self._attempts.get(key, []) if now - t < settings.login_lockout_minutes * 60]
        attempts.append(now)
        self._attempts[key] = attempts
        if len(attempts) >= settings.login_max_attempts:
            self._locked[key] = now + settings.login_lockout_minutes * 60

    def clear(self, username: str, ip: str | None) -> None:
        key = self._key(username, ip)
        self._attempts.pop(key, None)
        self._locked.pop(key, None)


login_limiter = LoginRateLimiter()


def safe_relative_path(root: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False
