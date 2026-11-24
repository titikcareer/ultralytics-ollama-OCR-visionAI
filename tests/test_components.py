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
            "transaction_date": "20/11/2026",
            "merchant": "Toko A",
            "total": 0,
            "subtotal": 100000,
            "tax": 5000,
            "direction": "expense",
        }
    )
    assert receipt.transaction_date == "2026-11-20"
    assert receipt.total == 105000


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
    assert safe_text("Snack rapat") == "Snack rapat"


def test_excel_export_uses_template_mapping():
    mapping = load_mapping()
    assert mapping.sheet_name == "Cash on Hand Harian Nov 2026"
    scans = [
        SimpleNamespace(
            merchant="Toko A",
            description="Snack",
            transaction_date="2026-11-20",
            direction="expense",
            total=100000,
        )
    ]
    path, media_type = export_scans(scans, "unit-test.xls")
    assert path.is_file()
    assert media_type == "application/vnd.ms-excel"

    import xlrd

    sheet = xlrd.open_workbook(str(path)).sheet_by_name(mapping.sheet_name)
    assert sheet.cell_value(10, 2) == "Toko A - Snack"
    assert sheet.cell_value(10, 6) == 100000
    assert sheet.cell_value(10, 7) == mapping.opening_balance - 100000


def test_image_normalization_rejects_payload():
    with pytest.raises(ImageValidationError):
        normalize_image(b"not-an-image", "image/jpeg")
