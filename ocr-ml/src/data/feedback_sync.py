"""Human-in-the-Loop feedback pool dan sinkronisasi ke dataset latih.

Setiap konfirmasi/koreksi karyawan disimpan sebagai gambar + label YOLO di
``data/feedback_loop`` bersama metadata JSONL (waktu, karyawan, role, status
verifikasi, dan versi model). Data terverifikasi kemudian digabungkan ke
``data/processed/train`` sebelum fine-tuning.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

try:
    import fcntl
except ImportError:
    fcntl = None

ML_ROOT = Path(__file__).resolve().parents[2]
FEEDBACK_DIR = ML_ROOT / "data" / "feedback_loop"
METADATA_FILE = "metadata.jsonl"
CLASS_FILE = ML_ROOT / "configs" / "classes.txt"

BOX_KEYS = (
    "class_id",
    "class",
    "class_name",
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
)
FEEDBACK_FIELDS = (
    "transaction_date",
    "merchant",
    "description",
    "total",
    "currency",
    "payment_method",
)


def _clean_fields(fields: dict | None) -> dict:
    if not isinstance(fields, dict):
        return {}
    return {name: fields[name] for name in FEEDBACK_FIELDS if fields.get(name) not in (None, "")}


def load_classes(root: Path = ML_ROOT) -> list[str]:
    classes_file = Path(root) / "configs" / "classes.txt"
    if not classes_file.is_file():
        return []
    return [line.strip() for line in classes_file.read_text(encoding="utf-8").splitlines() if line.strip()]


def metadata_path(feedback_dir: Path = FEEDBACK_DIR) -> Path:
    return Path(feedback_dir) / METADATA_FILE


def load_records(feedback_dir: Path = FEEDBACK_DIR) -> list[dict]:
    path = metadata_path(feedback_dir)
    if not path.is_file():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            value["fields"] = _clean_fields(value.get("fields"))
            records.append(value)
    return records


def _write_records(records: list[dict], feedback_dir: Path) -> None:
    path = metadata_path(feedback_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".jsonl.tmp")
    temporary.write_text(
        "".join(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n" for record in records),
        encoding="utf-8",
    )
    temporary.replace(path)


@contextmanager
def _metadata_lock(feedback_dir: Path):
    feedback_dir.mkdir(parents=True, exist_ok=True)
    lock_path = feedback_dir / "metadata.lock"
    with lock_path.open("a+") as handle:
        if fcntl is not None:
            fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(handle, fcntl.LOCK_UN)


def _payload_hash(scan_id: int | None, fields: dict | None, boxes: list[dict] | None) -> str:
    payload = json.dumps(
        {"scan_id": scan_id, "fields": fields or {}, "boxes": boxes or []},
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _image_size(image_bytes: bytes) -> tuple[float, float]:
    from io import BytesIO

    from PIL import Image

    with Image.open(BytesIO(image_bytes)) as image:
        return float(image.width), float(image.height)


def _box_to_yolo(box: dict, classes: list[str], image_w: float, image_h: float) -> str | None:
    class_id = box.get("class_id")
    if class_id is None:
        class_name = str(box.get("class") or box.get("class_name") or "")
        if class_name not in classes:
            return None
        class_id = classes.index(class_name)
    try:
        class_id = int(class_id)
    except (TypeError, ValueError):
        return None
    if class_id < 0 or class_id >= len(classes):
        return None

    width = float(box.get("image_width") or image_w)
    height = float(box.get("image_height") or image_h)
    if width <= 0 or height <= 0:
        return None

    if all(box.get(key) is not None for key in ("x_center", "y_center", "width", "height")):
        x_center = float(box["x_center"])
        y_center = float(box["y_center"])
        box_w = float(box["width"])
        box_h = float(box["height"])
    else:
        try:
            xmin = float(box["xmin"])
            ymin = float(box["ymin"])
            xmax = float(box["xmax"])
            ymax = float(box["ymax"])
        except (KeyError, TypeError, ValueError):
            return None
        x_center = (xmin + xmax) / 2
        y_center = (ymin + ymax) / 2
        box_w = xmax - xmin
        box_h = ymax - ymin

    values = (
        x_center / width,
        y_center / height,
        box_w / width,
        box_h / height,
    )
    if values[2] <= 0 or values[3] <= 0:
        return None
    clamped = [min(1.0, max(0.0, value)) for value in values]
    return f"{class_id} " + " ".join(f"{value:.6f}" for value in clamped)


def save_feedback(
    image_bytes: bytes,
    *,
    boxes: list[dict] | None = None,
    fields: dict | None = None,
    employee: str = "",
    role: str = "",
    scan_id: int | None = None,
    verified: bool = True,
    model_version: str = "",
    root: Path = ML_ROOT,
    feedback_dir: Path | None = None,
) -> dict:
    """Simpan satu contoh feedback terverifikasi ke feedback pool."""
    if not image_bytes:
        raise ValueError("Gambar feedback kosong")

    fields = _clean_fields(fields)
    pool = Path(feedback_dir) if feedback_dir is not None else Path(root) / "data" / "feedback_loop"
    images_dir = pool / "images"
    labels_dir = pool / "labels"
    images_dir.mkdir(parents=True, exist_ok=True)
    labels_dir.mkdir(parents=True, exist_ok=True)

    digest = _payload_hash(scan_id, fields, boxes)
    with _metadata_lock(pool):
        records = load_records(pool)
        for record in records:
            if record.get("payload_hash") == digest and record.get("scan_id") == scan_id:
                return {
                    "status": "duplicate",
                    "feedback_id": record.get("id"),
                    "verified_count": sum(1 for item in records if item.get("verified")),
                }

        image_w, image_h = _image_size(image_bytes)
        classes = load_classes(Path(root))
        lines: list[str] = []
        for box in boxes or []:
            if not isinstance(box, dict):
                continue
            converted = _box_to_yolo(box, classes, image_w, image_h)
            if converted:
                lines.append(converted)
        if not lines and classes:
            lines.append(f"{classes.index('struk_belanja') if 'struk_belanja' in classes else 0} " + "0.5 0.5 1.0 1.0")

        feedback_id = f"{datetime.now(UTC):%Y%m%d%H%M%S}-{uuid.uuid4().hex[:8]}"
        image_name = f"{feedback_id}.jpg"
        label_name = f"{feedback_id}.txt"
        (images_dir / image_name).write_bytes(image_bytes)
        (labels_dir / label_name).write_text("\n".join(lines) + "\n", encoding="utf-8")

        record = {
            "id": feedback_id,
            "created_at": datetime.now(UTC).isoformat(),
            "scan_id": scan_id,
            "employee": employee[:64],
            "role": role[:16],
            "verified": bool(verified),
            "model_version": model_version[:64],
            "image": f"images/{image_name}",
            "label": f"labels/{label_name}",
            "image_width": image_w,
            "image_height": image_h,
            "boxes": boxes or [],
            "fields": fields or {},
            "payload_hash": digest,
            "synced_to_train": False,
        }
        records.append(record)
        _write_records(records, pool)
        verified_count_value = sum(1 for item in records if item.get("verified"))
    return {
        "status": "saved",
        "feedback_id": feedback_id,
        "verified_count": verified_count_value,
    }


def verified_count(feedback_dir: Path = FEEDBACK_DIR) -> int:
    return sum(1 for record in load_records(feedback_dir) if record.get("verified"))


def pending_feedback(feedback_dir: Path = FEEDBACK_DIR) -> list[dict]:
    return [record for record in load_records(feedback_dir) if not record.get("verified")]


def verify_feedback(feedback_id: str, feedback_dir: Path = FEEDBACK_DIR) -> dict:
    pool = Path(feedback_dir)
    with _metadata_lock(pool):
        records = load_records(pool)
        for record in records:
            if str(record.get("id")) == str(feedback_id):
                record["verified"] = True
                record["verified_at"] = datetime.now(UTC).isoformat()
                record["synced_to_train"] = False
                _write_records(records, pool)
                return {"status": "verified", "feedback_id": str(feedback_id)}
    raise KeyError(str(feedback_id))


def sync_feedback_to_dataset(
    root: Path = ML_ROOT,
    feedback_dir: Path | None = None,
    processed_dir: Path | None = None,
    only_verified: bool = True,
) -> dict:
    """Salin feedback baru ke folder train yang dipakai YOLO."""
    ml_root = Path(root)
    pool = Path(feedback_dir) if feedback_dir is not None else ml_root / "data" / "feedback_loop"
    processed = Path(processed_dir) if processed_dir is not None else ml_root / "data" / "processed" / "train"
    images_dir = processed / "images"
    labels_dir = processed / "labels"
    images_dir.mkdir(parents=True, exist_ok=True)
    labels_dir.mkdir(parents=True, exist_ok=True)

    with _metadata_lock(pool):
        records = load_records(pool)
        synced = 0
        for record in records:
            if only_verified and not record.get("verified"):
                continue
            if record.get("synced_to_train"):
                continue
            source_image = pool / str(record.get("image", ""))
            source_label = pool / str(record.get("label", ""))
            if not source_image.is_file() or not source_label.is_file():
                continue
            pool_root = pool.resolve()
            if pool_root not in source_image.resolve().parents or pool_root not in source_label.resolve().parents:
                continue

            identifier = str(record.get("id") or source_image.stem)
            target_image = images_dir / f"fb_{identifier}{source_image.suffix.lower()}"
            target_label = labels_dir / f"fb_{identifier}.txt"
            if not target_image.is_file():
                shutil.copy2(source_image, target_image)
            if not target_label.is_file():
                shutil.copy2(source_label, target_label)
            record["synced_to_train"] = True
            synced += 1

        if synced:
            _write_records(records, pool)
        verified = sum(1 for record in records if record.get("verified"))
        total = len(records)
    return {"synced": synced, "verified": verified, "total": total}


def main() -> None:
    parser = argparse.ArgumentParser(description="Sinkronisasi feedback pool ke dataset latih")
    parser.add_argument("--root", type=Path, default=ML_ROOT)
    parser.add_argument("--feedback-dir", type=Path, default=None)
    args = parser.parse_args()
    print(json.dumps(sync_feedback_to_dataset(args.root, args.feedback_dir), indent=2))


if __name__ == "__main__":
    main()
