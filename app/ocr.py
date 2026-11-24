"""Antarmuka OCR lokal: YOLOv8 + OCR engine, tanpa layanan LLM eksternal."""

from pydantic import ValidationError

from app import ml
from app.schemas import ExtractedReceipt

OCR_UNAVAILABLE = "Model OCR lokal belum siap. Silakan hubungi tim IT."


class OcrError(Exception):
    pass


def ocr_is_ready(timeout_seconds: float = 2.0) -> bool:
    """Status OCR bergantung pada keberadaan ``models/production/best.pt``."""
    try:
        return ml.model_ready()
    except Exception:
        return False


def run_ocr(image_bytes: bytes) -> dict:
    """Jalankan pipeline deteksi + OCR dan kembalikan JSON terstruktur."""
    try:
        return ml.predict(image_bytes)
    except ml.ModelNotReadyError as exc:
        raise OcrError(OCR_UNAVAILABLE) from exc
    except Exception as exc:
        raise OcrError(OCR_UNAVAILABLE) from exc


def receipt_from_prediction(prediction: dict) -> ExtractedReceipt:
    try:
        return ExtractedReceipt.model_validate(prediction.get("fields") or {})
    except (ValidationError, TypeError, AttributeError) as exc:
        raise OcrError(OCR_UNAVAILABLE) from exc


def extract_receipt(image_bytes: bytes) -> tuple[ExtractedReceipt, str]:
    prediction = run_ocr(image_bytes)
    return receipt_from_prediction(prediction), str(prediction.get("raw_text", ""))
