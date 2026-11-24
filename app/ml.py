"""Jembatan antara aplikasi FastAPI dan paket ML di folder ``ocr-ml``."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from app.config import BASE_DIR, get_settings
from app.schemas import core_receipt_fields

CODE_SRC = BASE_DIR / "ocr-ml" / "src"


def _ensure_package() -> None:
    if "ocr_ml" in sys.modules:
        return
    spec = importlib.util.spec_from_file_location(
        "ocr_ml",
        CODE_SRC / "__init__.py",
        submodule_search_locations=[str(CODE_SRC)],
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Tidak dapat memuat modul ML dari {CODE_SRC}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["ocr_ml"] = module
    spec.loader.exec_module(module)


_ensure_package()

from ocr_ml.models.predict_model import ModelNotReadyError  # noqa: E402, F401


def model_ready() -> bool:
    from ocr_ml.models.predict_model import is_model_ready

    return is_model_ready(Path(get_settings().ml_root))


def predict(image_bytes: bytes) -> dict:
    from ocr_ml.models.predict_model import predict_receipt

    settings = get_settings()
    result = predict_receipt(
        image_bytes,
        root=Path(settings.ml_root),
        conf=settings.ocr_confidence,
        iou=settings.ocr_iou,
        imgsz=settings.ocr_imgsz,
        ocr_engine=settings.ocr_engine,
        ocr_min_confidence=settings.ocr_min_confidence,
        ocr_variants=settings.ocr_variants,
    )
    return result.as_dict()


def model_version() -> str:
    from ocr_ml.models.predict_model import model_version as _model_version

    return _model_version(Path(get_settings().ml_root))


def store_feedback(
    image_bytes: bytes,
    *,
    boxes: list[dict] | None = None,
    fields: dict | None = None,
    employee: str = "",
    role: str = "",
    scan_id: int | None = None,
    verified: bool = True,
    model: str = "",
) -> dict:
    from ocr_ml.data.feedback_sync import save_feedback

    return save_feedback(
        image_bytes,
        boxes=boxes,
        fields=core_receipt_fields(fields),
        employee=employee,
        role=role,
        scan_id=scan_id,
        verified=verified,
        model_version=model or model_version(),
        root=Path(get_settings().ml_root),
    )


def verified_feedback_count() -> int:
    from ocr_ml.data.feedback_sync import verified_count

    return verified_count(Path(get_settings().ml_root) / "data" / "feedback_loop")


def pending_feedback() -> list[dict]:
    from ocr_ml.data.feedback_sync import pending_feedback as load_pending

    return load_pending(Path(get_settings().ml_root) / "data" / "feedback_loop")


def verify_feedback(feedback_id: str) -> dict:
    from ocr_ml.data.feedback_sync import verify_feedback as verify

    return verify(feedback_id, Path(get_settings().ml_root) / "data" / "feedback_loop")


def registry_production() -> dict | None:
    from ocr_ml.models.train_model import load_registry

    return load_registry(Path(get_settings().ml_root)).get("production")


def retrain(force: bool = False) -> dict:
    from ocr_ml.pipelines.retrain_pipeline import retrain as run_retrain

    settings = get_settings()
    return run_retrain(
        Path(settings.ml_root),
        force=force,
        min_feedback=settings.feedback_threshold,
        epochs=settings.retrain_epochs,
    )


def rollback_model() -> dict:
    from ocr_ml.pipelines.retrain_pipeline import rollback as run_rollback

    return run_rollback(Path(get_settings().ml_root))
