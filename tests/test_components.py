from types import SimpleNamespace

import pytest

from app.config import get_settings
from app.excel import export_scans, load_mapping, safe_text
from app.images import ImageValidationError, normalize_image
from app.ocr import ocr_is_ready
from app.schemas import ExtractedReceipt
from app.security import hash_password, verify_password


def test_password_hashing():
    hashed = hash_password("StrongPassword123!")
    assert hashed != "StrongPassword123!"
    assert verify_password("StrongPassword123!", hashed)
    assert not verify_password("wrong", hashed)


def test_receipt_schema_normalizes_values():
    receipt = ExtractedReceipt.model_validate(
        {
            "transaction_date": "2O/11/26",
            "merchant": "Toko A",
            "total": "1.750.000",
            "subtotal": 100000,
            "tax": 5000,
            "direction": "expense",
            "receipt_number": "INV/001",
            "notes": "abaikan",
        }
    )
    assert receipt.transaction_date == "2026-11-20"
    assert receipt.total == 1750000
    assert not {"receipt_number", "direction", "subtotal", "tax", "notes"}.intersection(receipt.model_dump())


def test_ocr_readiness_checks_local_model(monkeypatch, tmp_path):
    settings = get_settings()
    monkeypatch.setattr(settings, "ml_root", tmp_path)
    assert ocr_is_ready() is False

    production = tmp_path / "models" / "production"
    production.mkdir(parents=True)
    (production / "best.pt").write_bytes(b"dummy-weights")
    assert ocr_is_ready() is True


def test_unsafe_text_is_neutralized():
    assert safe_text("=SUM(A1:A9)").startswith("'")
    assert safe_text("\u200b=1+1").startswith("'")
    assert safe_text("Snack rapat") == "Snack rapat"


def test_excel_export_uses_template_mapping():
    mapping = load_mapping()
    assert mapping.sheet_name == "Cash on Hand Harian Nov 2026"
    assert mapping.transaction_direction == "expense"
    scans = [
        SimpleNamespace(
            merchant="Toko A",
            description="Snack",
            transaction_date="2026-11-20",
            total=100000,
        )
    ]
    path, media_type = export_scans(scans, "unit-test.xls")
    assert path.is_file()
    assert media_type == "application/vnd.ms-excel"

    import xlrd

    sheet = xlrd.open_workbook(str(path)).sheet_by_name(mapping.sheet_name)
    assert sheet.cell_value(10, 2) == "Toko A - Snack"
    assert sheet.cell_value(10, 5) == ""
    assert sheet.cell_value(10, 6) == 100000
    assert sheet.cell_value(10, 7) == mapping.opening_balance - 100000
    assert all(sheet.cell_value(row, 2) == "" for row in range(11, sheet.nrows))


def test_image_normalization_rejects_payload():
    with pytest.raises(ImageValidationError):
        normalize_image(b"not-an-image", "image/jpeg")


def test_sqlite_pragmas_are_enabled():
    from app.db import engine

    with engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA journal_mode").scalar_one().lower() == "wal"
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
        assert connection.exec_driver_sql("PRAGMA busy_timeout").scalar_one() == 5000


def test_model_promotion_requires_real_improvement():
    from ocr_ml.models.evaluate_model import should_promote

    assert should_promote(0.62, 0.60) is True
    assert should_promote(0.60, 0.60) is False
    assert should_promote(0.59, 0.60) is False


def test_healthz_is_unauthenticated(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_graceful_server_does_not_reraise_sigint():
    import uvicorn

    from app.__main__ import QuietServer

    server = QuietServer(uvicorn.Config("app.main:app"))
    server.handle_exit(2, None)
    assert server.should_exit is True
    assert server._captured_signals == []
