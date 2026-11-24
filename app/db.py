from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import get_settings

settings = get_settings()


class Base(DeclarativeBase):
    pass


def _engine_kwargs(url: str) -> dict:
    if url.startswith("sqlite"):
        return {"connect_args": {"check_same_thread": False, "timeout": 30}}
    return {"pool_pre_ping": True}


engine = create_engine(settings.database_url, **_engine_kwargs(settings.database_url))
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def _ensure_sqlite_scan_sequence() -> None:
    if not settings.database_url.startswith("sqlite"):
        return
    with engine.begin() as connection:
        has_sequence = connection.execute(
            text("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'sqlite_sequence'")
        ).scalar_one_or_none()
        if has_sequence is None:
            return
        current = connection.execute(text("SELECT COALESCE(MAX(id), 0) FROM scans")).scalar_one()
        try:
            historical = connection.execute(
                text(
                    "SELECT COALESCE(MAX(CAST(target AS INTEGER)), 0) FROM audit_logs "
                    "WHERE action LIKE 'scan_%' AND target GLOB '[0-9]*'"
                )
            ).scalar_one()
        except Exception:
            historical = 0
        seq = max(int(current or 0), int(historical or 0))
        connection.execute(
            text(
                "INSERT INTO sqlite_sequence (name, seq) "
                "SELECT 'scans', :seq WHERE NOT EXISTS "
                "(SELECT 1 FROM sqlite_sequence WHERE name = 'scans')"
            ),
            {"seq": seq},
        )
        connection.execute(
            text("UPDATE sqlite_sequence SET seq = MAX(seq, :seq) WHERE name = 'scans'"),
            {"seq": seq},
        )


def init_db() -> None:
    Path(settings.database_url.replace("sqlite:///", "")).parent.mkdir(parents=True, exist_ok=True)
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    settings.export_dir.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(bind=engine)
    _ensure_sqlite_scan_sequence()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
