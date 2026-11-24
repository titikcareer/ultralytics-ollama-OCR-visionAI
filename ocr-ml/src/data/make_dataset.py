"""Konversi anotasi Roboflow (``_annotations.csv``) menjadi format YOLOv8.

Script ini hanya memakai pustaka standar sehingga dapat dijalankan sebelum
dependensi deep learning dipasang::

    python ocr-ml/src/data/make_dataset.py

Dataset mentah dibaca dari ``data/raw/<split>`` bila tersedia, jika tidak maka
dibaca dari lokasi bawaan ``data/<split>`` (train/valid/test).
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
from collections import defaultdict
from pathlib import Path

SPLITS = ("train", "valid", "test")
ANNOTATION_FILE = "_annotations.csv"
CLASS_FILE = "classes.txt"
ML_ROOT = Path(__file__).resolve().parents[2]

YOLO_HEADER = [
    "filename",
    "class_id",
    "class",
    "x_center",
    "y_center",
    "width",
    "height",
    "xmin",
    "ymin",
    "xmax",
    "ymax",
    "image_width",
    "image_height",
]


def _row_value(row: dict, *keys: str) -> str:
    lowered = {str(key).strip().lower(): (value or "") for key, value in row.items()}
    for key in keys:
        value = str(lowered.get(key, "")).strip()
        if value:
            return value
    return ""


def _as_float(value: str) -> float | None:
    try:
        result = float(str(value).strip().replace(",", "."))
    except (TypeError, ValueError):
        return None
    return result if result == result and abs(result) != float("inf") else None


def _image_size(path: Path) -> tuple[float, float] | None:
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        with Image.open(path) as image:
            width, height = image.size
        return float(width), float(height)
    except (OSError, ValueError):
        return None


def source_dir(root: Path, split: str) -> Path:
    """Lokasi dataset mentah untuk satu split."""
    raw = root / "data" / "raw" / split
    if (raw / ANNOTATION_FILE).is_file():
        return raw
    return root / "data" / split


def read_split(root: Path, split: str) -> list[dict]:
    """Baca satu CSV Roboflow menjadi record anotasi yang tervalidasi."""
    source = source_dir(root, split)
    csv_path = source / ANNOTATION_FILE
    if not csv_path.is_file():
        return []

    records: list[dict] = []
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            filename = _row_value(row, "filename", "file", "image", "name")
            class_name = _row_value(row, "class", "label", "class_name", "category")
            if not filename or not class_name:
                continue

            xmin = _as_float(_row_value(row, "xmin", "x_min", "left"))
            ymin = _as_float(_row_value(row, "ymin", "y_min", "top"))
            xmax = _as_float(_row_value(row, "xmax", "x_max", "right"))
            ymax = _as_float(_row_value(row, "ymax", "y_max", "bottom"))
            if None in (xmin, ymin, xmax, ymax):
                continue

            image_path = source / filename
            resolved = image_path.resolve()
            if source.resolve() not in resolved.parents:
                continue

            img_w = _as_float(_row_value(row, "image_width", "img_width", "width")) or 0.0
            img_h = _as_float(_row_value(row, "image_height", "img_height", "height")) or 0.0
            if img_w <= 0 or img_h <= 0:
                detected = _image_size(resolved)
                if detected is None:
                    continue
                img_w, img_h = detected

            box_w = _as_float(_row_value(row, "bbox_width", "box_width", "w"))
            box_h = _as_float(_row_value(row, "bbox_height", "box_height", "h"))
            if box_w and box_w > 0 and box_h and box_h > 0:
                xmax = xmin + box_w
                ymax = ymin + box_h

            records.append(
                {
                    "split": split,
                    "source": source,
                    "filename": filename,
                    "image_path": resolved,
                    "class_name": class_name,
                    "img_w": img_w,
                    "img_h": img_h,
                    "box": (xmin, ymin, xmax, ymax),
                }
            )
    return records


def load_classes(records: list[dict], classes_file: Path) -> list[str]:
    """Ambil daftar kelas secara dinamis dan pertahankan ID yang sudah ada."""
    classes: list[str] = []
    if classes_file.is_file():
        classes = [
            line.strip()
            for line in classes_file.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        ]

    for record in records:
        name = record["class_name"]
        if name not in classes:
            classes.append(name)

    if not classes:
        raise ValueError("Tidak ada kelas yang ditemukan pada anotasi Roboflow.")

    classes_file.parent.mkdir(parents=True, exist_ok=True)
    classes_file.write_text("\n".join(classes) + "\n", encoding="utf-8")
    return classes


def normalized_box(record: dict) -> tuple[float, float, float, float] | None:
    """Konversi ``xmin,ymin,xmax,ymax`` ke ``xc,yc,w,h`` ternormalisasi."""
    xmin, ymin, xmax, ymax = record["box"]
    if xmax < xmin:
        xmin, xmax = xmax, xmin
    if ymax < ymin:
        ymin, ymax = ymax, ymin

    img_w, img_h = record["img_w"], record["img_h"]
    x_center = ((xmin + xmax) / 2) / img_w
    y_center = ((ymin + ymax) / 2) / img_h
    width = (xmax - xmin) / img_w
    height = (ymax - ymin) / img_h

    if width <= 0 or height <= 0:
        return None
    return tuple(min(1.0, max(0.0, value)) for value in (x_center, y_center, width, height))


def write_data_yaml(classes: list[str], root: Path) -> dict[str, Path]:
    """Tulis ``data.yaml`` untuk training dan salinan interim."""
    names = "\n".join(f"  - {name}" for name in classes)
    content = (
        "# Dibuat otomatis oleh ocr-ml/src/data/make_dataset.py\n"
        "path: ../data/processed\n"
        "train: train/images\n"
        "val: valid/images\n"
        "test: test/images\n"
        f"nc: {len(classes)}\n"
        "names:\n"
        f"{names}\n"
    )
    configs_file = root / "configs" / "data.yaml"
    configs_file.parent.mkdir(parents=True, exist_ok=True)
    configs_file.write_text(content, encoding="utf-8")

    interim_file = root / "data" / "interim" / "data.yaml"
    interim_file.parent.mkdir(parents=True, exist_ok=True)
    interim_file.write_text(content.replace("path: ../data/processed", "path: ../processed"), encoding="utf-8")
    return {"config": configs_file, "interim": interim_file}


def build_dataset(root: Path = ML_ROOT, splits: tuple[str, ...] = SPLITS) -> dict:
    """Buat label YOLO di ``interim`` dan ``processed`` untuk semua split."""
    root = Path(root).resolve()
    records_by_split = {split: read_split(root, split) for split in splits}
    all_records = [record for records in records_by_split.values() for record in records]
    classes = load_classes(all_records, root / "configs" / "classes.txt")
    class_ids = {name: index for index, name in enumerate(classes)}

    interim = root / "data" / "interim"
    processed = root / "data" / "processed"
    summary: dict = {"classes": classes, "splits": {}, "root": str(root)}

    for split, records in records_by_split.items():
        labels_interim = interim / split / "labels"
        labels_processed = processed / split / "labels"
        images_processed = processed / split / "images"
        for folder in (labels_interim, labels_processed, images_processed):
            folder.mkdir(parents=True, exist_ok=True)

        grouped: dict[str, list[dict]] = defaultdict(list)
        for record in records:
            grouped[record["filename"]].append(record)

        written = 0
        annotations = 0
        missing_images = 0
        converted_rows: list[list] = []

        for filename, image_records in grouped.items():
            lines: list[str] = []
            for record in image_records:
                box = normalized_box(record)
                if box is None:
                    continue
                x_center, y_center, width, height = box
                class_id = class_ids[record["class_name"]]
                lines.append(f"{class_id} {x_center:.6f} {y_center:.6f} {width:.6f} {height:.6f}")
                xmin, ymin, xmax, ymax = record["box"]
                converted_rows.append(
                    [
                        filename,
                        class_id,
                        record["class_name"],
                        round(x_center, 6),
                        round(y_center, 6),
                        round(width, 6),
                        round(height, 6),
                        xmin,
                        ymin,
                        xmax,
                        ymax,
                        record["img_w"],
                        record["img_h"],
                    ]
                )

            if not lines:
                continue

            label_name = Path(filename).stem + ".txt"
            payload = "\n".join(lines) + "\n"
            (labels_interim / label_name).write_text(payload, encoding="utf-8")
            (labels_processed / label_name).write_text(payload, encoding="utf-8")

            source_image = image_records[0]["image_path"]
            if source_image.is_file():
                shutil.copy2(source_image, images_processed / Path(filename).name)
            else:
                missing_images += 1
            written += 1
            annotations += len(lines)

        with (interim / f"{split}_annotations_yolo.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(YOLO_HEADER)
            writer.writerows(converted_rows)

        summary["splits"][split] = {
            "images": written,
            "annotations": annotations,
            "missing_images": missing_images,
        }

    summary["data_yaml"] = {key: str(value) for key, value in write_data_yaml(classes, root).items()}
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Konversi anotasi Roboflow ke format YOLOv8")
    parser.add_argument("--root", type=Path, default=ML_ROOT, help="Direktur utama ocr-ml")
    parser.add_argument("--splits", nargs="+", default=list(SPLITS), choices=SPLITS)
    args = parser.parse_args()

    summary = build_dataset(args.root, tuple(args.splits))
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
