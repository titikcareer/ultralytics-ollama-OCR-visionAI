"""Evaluasi model YOLO pada dataset validasi dan guardrail promosi model."""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile

ML_ROOT = Path(__file__).resolve().parents[2]


@contextmanager
def resolved_data_yaml(data: Path | str, root: Path) -> Iterator[Path]:
    """Buat salinan data.yaml dengan path absolut agar Ultralytics tidak memakai folder datasets."""
    data_file = Path(data).resolve()
    processed = Path(root).resolve() / "data" / "processed"
    lines = [
        f"path: {processed}" if line.startswith("path:") else line
        for line in data_file.read_text(encoding="utf-8").splitlines()
    ]
    with NamedTemporaryFile("w", suffix=".yaml", encoding="utf-8", delete=False) as handle:
        handle.write("\n".join(lines) + "\n")
        runtime_file = Path(handle.name)
    try:
        yield runtime_file
    finally:
        runtime_file.unlink(missing_ok=True)


def evaluate(
    weights: Path | str,
    data: Path | str,
    imgsz: int = 640,
    device: str | None = None,
    root: Path | None = None,
) -> dict:
    """Jalankan validasi Ultralytics dan kembalikan metrik deteksi."""
    from ultralytics import YOLO

    data_file = Path(data).resolve()
    ml_root = Path(root).resolve() if root is not None else data_file.parents[1]
    with resolved_data_yaml(data_file, ml_root) as runtime_data:
        metrics = YOLO(str(weights)).val(
            data=str(runtime_data),
            imgsz=imgsz,
            device=device,
            verbose=False,
            plots=False,
        )
    box = getattr(metrics, "box", None)
    result = {
        "map50": round(float(getattr(box, "map50", 0.0)), 6),
        "map50_95": round(float(getattr(box, "map", 0.0)), 6),
        "precision": round(float(getattr(box, "mp", 0.0)), 6),
        "recall": round(float(getattr(box, "mr", 0.0)), 6),
    }
    return result


def should_promote(candidate_map50: float, production_map50: float) -> bool:
    """Model baru hanya naik produksi jika mAP50 naik secara signifikan."""
    return float(candidate_map50) > float(production_map50) + 0.001


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluasi bobot YOLO pada data valid")
    parser.add_argument("--weights", type=Path, required=True)
    parser.add_argument("--data", type=Path, default=ML_ROOT / "configs" / "data.yaml")
    parser.add_argument("--imgsz", type=int, default=640)
    args = parser.parse_args()
    print(json.dumps(evaluate(args.weights, args.data, args.imgsz), indent=2))


if __name__ == "__main__":
    main()
