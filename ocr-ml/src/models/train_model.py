"""Training awal dan fine-tuning incremental YOLOv8 untuk struk."""

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
from datetime import UTC, datetime  # noqa: E402

from ocr_ml.models.evaluate_model import evaluate, resolved_data_yaml  # noqa: E402
from ocr_ml.models.predict_model import model_candidates, reload_detector  # noqa: E402

ML_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WEIGHTS = "yolov8n.pt"
DEFAULT_DATA = ML_ROOT / "configs" / "data.yaml"


def registry_path(root: Path = ML_ROOT) -> Path:
    return Path(root) / "models" / "registry.json"


def load_registry(root: Path = ML_ROOT) -> dict:
    path = registry_path(root)
    if not path.is_file():
        return {"schema_version": 1, "production": None, "history": []}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"schema_version": 1, "production": None, "history": []}
    value.setdefault("schema_version", 1)
    value.setdefault("production", None)
    value.setdefault("history", [])
    return value


def save_registry(registry: dict, root: Path = ML_ROOT) -> None:
    path = registry_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(registry, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def record_model(
    *,
    root: Path = ML_ROOT,
    version: str,
    weights: Path,
    metrics: dict,
    source: str,
    base_weights: str,
    epochs: int,
    data: Path,
    promote: bool = True,
    extra: dict | None = None,
) -> dict:
    """Catat versi model ke registry dan optionally promosikan ke produksi."""
    registry = load_registry(root)
    entry = {
        "version": version,
        "trained_at": datetime.now(UTC).isoformat(),
        "path": str(weights.resolve().relative_to(Path(root).resolve())),
        "source": source,
        "base_weights": base_weights,
        "epochs": epochs,
        "data": str(data),
        "map50": metrics.get("map50", 0.0),
        "map50_95": metrics.get("map50_95", 0.0),
        "precision": metrics.get("precision", 0.0),
        "recall": metrics.get("recall", 0.0),
        "promoted": bool(promote),
    }
    if extra:
        entry.update(extra)
    registry["history"].append(entry)
    if promote:
        registry["production"] = entry
        reload_detector()
    save_registry(registry, root)
    return entry


def train(
    *,
    root: Path = ML_ROOT,
    data: Path | None = None,
    weights: str = DEFAULT_WEIGHTS,
    init_from: Path | str | None = None,
    output: str = "baseline",
    epochs: int = 50,
    imgsz: int = 640,
    batch: int = 16,
    device: str | None = None,
    fresh: bool = False,
    record: bool = True,
) -> dict:
    """Latih/fine-tune YOLOv8, evaluasi, dan simpan bobot ke ``models/<output>``."""
    from ultralytics import YOLO

    ml_root = Path(root).resolve()
    data_file = Path(data) if data else ml_root / "configs" / "data.yaml"
    if not data_file.is_file():
        raise FileNotFoundError(f"Konfigurasi dataset tidak ditemukan: {data_file}")

    production = model_candidates(ml_root)
    base = str(init_from) if init_from else (str(production) if production and not fresh else weights)
    version = f"v{datetime.now(UTC):%Y%m%d%H%M%S}"
    project = ml_root / "models" / "runs"
    project.mkdir(parents=True, exist_ok=True)

    with resolved_data_yaml(data_file, ml_root) as runtime_data:
        run = YOLO(base).train(
            data=str(runtime_data),
            epochs=epochs,
            imgsz=imgsz,
            batch=batch,
            device=device,
            patience=min(10, epochs),
            project=str(project),
            name=f"{output}_{version}",
            exist_ok=True,
            verbose=False,
        )
    best = Path(run.save_dir) / "weights" / "best.pt"
    if not best.is_file():
        raise FileNotFoundError(f"Bobot terbaik tidak ditemukan di {best}")

    target_dir = ml_root / "models" / output
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "best.pt"
    shutil.copy2(best, target)

    metrics = evaluate(target, data_file, imgsz=imgsz, device=device)
    entry = None
    if record:
        entry = record_model(
            root=ml_root,
            version=version,
            weights=target,
            metrics=metrics,
            source=output,
            base_weights=base,
            epochs=epochs,
            data=data_file,
            promote=(output == "production"),
            extra={"feedback_samples": 0},
        )
    reload_detector()
    return {
        "version": version,
        "weights": str(target),
        "metrics": metrics,
        "registry": entry,
        "data": str(data_file),
        "base_weights": base,
        "epochs": epochs,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Training YOLOv8 untuk deteksi area struk")
    parser.add_argument("--root", type=Path, default=ML_ROOT)
    parser.add_argument("--data", type=Path, default=None)
    parser.add_argument("--weights", default=DEFAULT_WEIGHTS, help="Bobot pretrained untuk training awal")
    parser.add_argument("--init-from", default=None, help="Bobot untuk fine-tuning (mis. models/production/best.pt)")
    parser.add_argument("--output", default="baseline", choices=("baseline", "production", "candidate"))
    parser.add_argument("--epochs", type=int, default=50)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--batch", type=int, default=16)
    parser.add_argument("--device", default=None, help="cpu, 0, atau mps")
    parser.add_argument("--fresh", action="store_true", help="Mulai dari --weights meskipun model produksi ada")
    args = parser.parse_args()

    result = train(
        root=args.root,
        data=args.data,
        weights=args.weights,
        init_from=args.init_from,
        output=args.output,
        epochs=args.epochs,
        imgsz=args.imgsz,
        batch=args.batch,
        device=args.device,
        fresh=args.fresh,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
