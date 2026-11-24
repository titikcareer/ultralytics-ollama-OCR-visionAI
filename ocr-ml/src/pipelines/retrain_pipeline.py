"""Orkestrasi retraining otomatis dari feedback karyawan (Human-in-the-Loop)."""

from __future__ import annotations

import sys
from pathlib import Path

_SRC_DIR = Path(__file__).resolve().parents[1]
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from bootstrap import ensure_ocr_ml_package  # noqa: E402

ensure_ocr_ml_package()

import argparse  # noqa: E402
import json  # noqa: E402
import shutil  # noqa: E402
from contextlib import contextmanager  # noqa: E402
from datetime import UTC, datetime  # noqa: E402

from ocr_ml.data.feedback_sync import sync_feedback_to_dataset, verified_count  # noqa: E402
from ocr_ml.models.evaluate_model import should_promote  # noqa: E402
from ocr_ml.models.predict_model import model_candidates, reload_detector  # noqa: E402
from ocr_ml.models.train_model import (  # noqa: E402
    DEFAULT_WEIGHTS,
    load_registry,
    record_model,
    train,
)

ML_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_THRESHOLD = 25
DEFAULT_RETRAIN_EPOCHS = 20

try:
    import fcntl
except ImportError:  # pragma: no cover - platform non-Unix
    fcntl = None


@contextmanager
def _retrain_lock(root: Path):
    lock_path = root / "models" / ".retrain.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as handle:
        if fcntl is not None:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                yield False
                return
        try:
            yield True
        finally:
            if fcntl is not None:
                fcntl.flock(handle, fcntl.LOCK_UN)


def _append_log(root: Path, result: dict) -> None:
    log_path = root / "models" / "retrain.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(
        {"logged_at": datetime.now(UTC).isoformat(), **result}, ensure_ascii=False, sort_keys=True
    )
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def retrain(
    root: Path = ML_ROOT,
    *,
    force: bool = False,
    min_feedback: int = DEFAULT_THRESHOLD,
    epochs: int = DEFAULT_RETRAIN_EPOCHS,
    imgsz: int = 640,
    device: str | None = None,
) -> dict:
    """Sinkronkan feedback, fine-tune, evaluasi, dan promosikan bila lebih baik."""
    ml_root = Path(root).resolve()
    pending = verified_count(ml_root / "data" / "feedback_loop")
    registry = load_registry(ml_root)
    production = registry.get("production") or {}
    current_map = float(production.get("map50", 0.0) or 0.0)

    if not force and pending < min_feedback:
        result = {
            "status": "skipped",
            "reason": f"Feedback terverifikasi baru {pending}, belum mencapai ambang {min_feedback}.",
            "verified_feedback": pending,
            "threshold": min_feedback,
        }
        _append_log(ml_root, result)
        return result

    with _retrain_lock(ml_root) as acquired:
        if not acquired:
            return {"status": "already_running", "verified_feedback": pending}

        sync = sync_feedback_to_dataset(ml_root)
        production_weights = model_candidates(ml_root)
        base_weights = str(production_weights) if production_weights else DEFAULT_WEIGHTS

        candidate = train(
            root=ml_root,
            init_from=production_weights,
            output="candidate",
            epochs=epochs,
            imgsz=imgsz,
            device=device,
            fresh=production_weights is None,
            record=False,
        )
        candidate_map = float(candidate["metrics"]["map50"])
        promoted = should_promote(candidate_map, current_map)

        if promoted:
            target_dir = ml_root / "models" / "production"
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / "best.pt"
            temporary = target_dir / "best.pt.tmp"
            shutil.copy2(candidate["weights"], temporary)
            temporary.replace(target)

        extra = {
            "feedback_samples": pending,
            "synced_samples": sync["synced"],
            "previous_map50": current_map,
            "candidate_map50": candidate_map,
        }
        entry = record_model(
            root=ml_root,
            version=candidate["version"],
            weights=(ml_root / "models" / "production" / "best.pt") if promoted else Path(candidate["weights"]),
            metrics=candidate["metrics"],
            source="retrain",
            base_weights=base_weights,
            epochs=candidate["epochs"],
            data=Path(candidate["data"]),
            promote=promoted,
            extra=extra,
        )
        if promoted:
            reload_detector()

        result = {
            "status": "promoted" if promoted else "rejected",
            "version": candidate["version"],
            "metrics": candidate["metrics"],
            "previous_map50": current_map,
            "promoted": promoted,
            "verified_feedback": pending,
            "synced_samples": sync["synced"],
            "registry": entry,
        }
        _append_log(ml_root, result)
        return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Retrain model dari feedback karyawan")
    parser.add_argument("--root", type=Path, default=ML_ROOT)
    parser.add_argument("--force", action="store_true", help="Latih ulang tanpa menunggu 25 feedback")
    parser.add_argument("--min-feedback", type=int, default=DEFAULT_THRESHOLD)
    parser.add_argument("--epochs", type=int, default=DEFAULT_RETRAIN_EPOCHS)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--device", default=None)
    args = parser.parse_args()

    result = retrain(
        args.root,
        force=args.force,
        min_feedback=args.min_feedback,
        epochs=args.epochs,
        imgsz=args.imgsz,
        device=args.device,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
