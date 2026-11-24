from fastapi import Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.config import get_settings
from app.deps import optional_context

settings = get_settings()
templates = Jinja2Templates(directory=str(settings.base_dir / "app" / "templates"))


def render(request: Request, name: str, db: Session, **context):
    auth = optional_context(request, db)
    payload = {
        "user": auth.user if auth else None,
        "csrf": auth.session.csrf_token if auth else None,
        "app_name": settings.app_name,
    }
    payload.update(context)
    return templates.TemplateResponse(request, name, payload)
