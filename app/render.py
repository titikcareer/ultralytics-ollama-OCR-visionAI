import hashlib

from fastapi import Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.config import get_settings
from app.deps import optional_context

settings = get_settings()
templates = Jinja2Templates(directory=str(settings.base_dir / "app" / "templates"))
_static_dir = settings.base_dir / "app" / "static"
_asset_version = hashlib.sha256(
    (_static_dir / "css" / "app.css").read_bytes() + (_static_dir / "js" / "app.js").read_bytes()
).hexdigest()[:8]


def render(request: Request, name: str, db: Session, **context):
    auth = optional_context(request, db)
    payload = {
        "user": auth.user if auth else None,
        "csrf": auth.session.csrf_token if auth else None,
        "app_name": settings.app_name,
        "asset_version": _asset_version,
    }
    payload.update(context)
    return templates.TemplateResponse(request, name, payload)
