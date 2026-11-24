import os
import tempfile
from pathlib import Path

_tmp = Path(tempfile.mkdtemp(prefix="receipt-recap-test-"))
_ml_root = _tmp / "ocr-ml"
(_ml_root / "configs").mkdir(parents=True, exist_ok=True)
(_ml_root / "configs" / "classes.txt").write_text(
    "struk_belanja\ninfo_toko\nitem_belanja\ntotal\n", encoding="utf-8"
)

os.environ.update(
    {
        "ML_ROOT": str(_ml_root),
        "SECRET_KEY": "test-secret-key-with-minimum-32-characters",
        "ADMIN_PASSWORD": "TestingAdmin123!",
        "SECURE_COOKIES": "false",
        "ENVIRONMENT": "test",
        "DATABASE_URL": f"sqlite:///{_tmp / 'test.db'}",
        "UPLOAD_DIR": str(_tmp / "uploads"),
        "EXPORT_DIR": str(_tmp / "exports"),
    }
)

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db import engine, init_db
from app.main import app
from app.models import AuditLog, Export, Scan, Session, User

init_db()


@pytest.fixture(autouse=True)
def reset_database():
    with engine.begin() as connection:
        for table in (AuditLog, Export, Scan, Session, User):
            connection.execute(text(f"DELETE FROM {table.__tablename__}"))
    yield


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def logged_in(client):
    client.get("/login")
    token = client.cookies.get("receipt_csrf")
    response = client.post(
        "/login",
        data={"username": "admin", "password": "TestingAdmin123!", "csrf": token},
        follow_redirects=False,
    )
    assert response.status_code == 303
    return client


@pytest.fixture
def sample_image():
    from io import BytesIO

    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (640, 480), (210, 210, 210)).save(buffer, format="JPEG")
    return buffer.getvalue()
