from __future__ import annotations

import sys
from pathlib import Path

_SRC_DIR = Path(__file__).resolve().parents[1]
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from bootstrap import ensure_ocr_ml_package  # noqa: E402

ensure_ocr_ml_package()

import json  # noqa: E402
import re  # noqa: E402
from collections import defaultdict  # noqa: E402
from dataclasses import dataclass  # noqa: E402
from datetime import date  # noqa: E402

from ocr_ml.features.preprocess import crop_region, enhance_variants, load_rgb  # noqa: E402

ML_ROOT = Path(__file__).resolve().parents[2]
CONTAINER_CLASSES = {"struk_belanja"}

_detector: object | None = None
_detector_path: str | None = None
_ocr_engine: object | None = None
_ocr_engine_name: str | None = None


class ModelNotReadyError(RuntimeError):
    """Model lokal atau engine OCR belum tersedia."""


@dataclass
class ReceiptPrediction:
    fields: dict
    regions: list[dict]
    raw_text: str
    model: str
    ocr_engine: str

    def as_dict(self) -> dict:
        return {
            "fields": self.fields,
            "regions": self.regions,
            "raw_text": self.raw_text,
            "model": self.model,
            "ocr_engine": self.ocr_engine,
        }


def model_candidates(root: Path = ML_ROOT) -> Path | None:
    """Model produksi lebih diutamakan, dengan fallback ke baseline."""
    base = Path(root) / "models"
    for name in ("production", "baseline"):
        candidate = base / name / "best.pt"
        if candidate.is_file() and candidate.stat().st_size > 0:
            return candidate
    return None


def is_model_ready(root: Path = ML_ROOT) -> bool:
    return model_candidates(Path(root)) is not None


def model_version(root: Path = ML_ROOT) -> str:
    candidate = model_candidates(Path(root))
    if candidate is None:
        return "unavailable"
    return f"{candidate.parent.name}/{candidate.name}"


def load_detector(root: Path = ML_ROOT, force: bool = False) -> object:
    """Muat detector YOLO sekali lalu simpan di cache proses."""
    global _detector, _detector_path

    candidate = model_candidates(Path(root))
    if candidate is None:
        raise ModelNotReadyError(
            "Model deteksi belum tersedia. Jalankan training awal: "
            "python ocr-ml/src/models/train_model.py --output baseline"
        )
    if not force and _detector is not None and _detector_path == str(candidate):
        return _detector

    try:
        from ultralytics import YOLO
    except ImportError as exc:
        raise ModelNotReadyError(
            "Paket ultralytics belum terpasang. Pasang dengan: pip install -r ocr-ml/requirements.txt"
        ) from exc

    _detector = YOLO(str(candidate))
    _detector_path = str(candidate)
    return _detector


def reload_detector() -> None:
    """Hot-reload setelah model produksi diganti."""
    global _detector, _detector_path
    _detector = None
    _detector_path = None


def _load_ocr(engine: str) -> tuple[object | None, str]:
    global _ocr_engine, _ocr_engine_name

    engine = (engine or "none").strip().lower()
    if engine == "none":
        return None, "none"
    if _ocr_engine is not None and _ocr_engine_name == engine:
        return _ocr_engine, _ocr_engine_name

    try:
        if engine == "paddleocr":
            from paddleocr import PaddleOCR

            try:
                reader = PaddleOCR(use_angle_cls=True, lang="en", show_log=False, use_gpu=False)
            except TypeError:
                reader = PaddleOCR(lang="en")
        elif engine == "easyocr":
            import easyocr

            reader = easyocr.Reader(["en"], gpu=False)
        else:
            return None, "unavailable"
    except ImportError:
        return None, "unavailable"

    _ocr_engine = reader
    _ocr_engine_name = engine
    return reader, engine


def _as_score(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 1.0


def _poly_position(points: object) -> tuple[float, float]:
    """Kembalikan posisi tengah poligon sebagai (y, x)."""
    if points is None:
        return 0.0, 0.0
    try:
        coordinates = [(float(point[0]), float(point[1])) for point in points]
    except (TypeError, ValueError, IndexError, KeyError):
        return 0.0, 0.0
    if not coordinates:
        return 0.0, 0.0
    count = len(coordinates)
    return (
        sum(point[1] for point in coordinates) / count,
        sum(point[0] for point in coordinates) / count,
    )


def _paddle_entries(result: object) -> list[tuple[str, float, float, float]]:
    entries: list[tuple[str, float, float, float]] = []
    for page in result or []:
        if isinstance(page, dict):
            texts = page.get("rec_texts") or []
            scores = page.get("rec_scores") or []
            polygons = page.get("dt_polys") or []
            for index, text in enumerate(texts):
                score = _as_score(scores[index]) if index < len(scores) else 1.0
                y, x = _poly_position(polygons[index]) if index < len(polygons) else (float(index), 0.0)
                entries.append((str(text), score, y, x))
            continue
        for item in page or []:
            if not isinstance(item, (list, tuple)) or len(item) < 2:
                continue
            label = item[1]
            if isinstance(label, (list, tuple)):
                text = str(label[0]) if label else ""
                score = _as_score(label[1]) if len(label) > 1 else 1.0
            else:
                text, score = str(label), 1.0
            y, x = _poly_position(item[0])
            entries.append((text, score, y, x))
    return entries


def _easyocr_entries(result: object) -> list[tuple[str, float, float, float]]:
    entries: list[tuple[str, float, float, float]] = []
    for item in result or []:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        y, x = _poly_position(item[0])
        score = _as_score(item[2]) if len(item) > 2 else 1.0
        entries.append((str(item[1]), score, y, x))
    return entries


def _ocr_lines(reader: object, engine: str, crop: object) -> list[tuple[str, float, float, float]]:
    """Ambil teks beserta keyakinan dan posisi baca setiap baris OCR."""
    if reader is None:
        return []
    try:
        if engine == "paddleocr":
            try:
                result = reader.ocr(crop, cls=False)
            except TypeError:
                result = reader.ocr(crop)
            return _paddle_entries(result)
        if engine == "easyocr":
            return _easyocr_entries(reader.readtext(crop, detail=1))
    except Exception:
        return []
    return []


def _assemble_lines(entries: list[tuple[str, float, float, float]], min_confidence: float) -> tuple[str, float]:
    """Susun baris sesuai urutan baca dan buang hasil OCR yang terlalu lemah."""
    accepted = [
        (text.strip(), score, y, x)
        for text, score, y, x in entries
        if text and text.strip() and score >= min_confidence
    ]
    if not accepted:
        return "", 0.0

    accepted.sort(key=lambda entry: (entry[2], entry[3]))
    groups: list[list[tuple[str, float, float, float]]] = []
    for entry in accepted:
        if groups:
            group = groups[-1]
            center = sum(item[2] for item in group) / len(group)
            if abs(entry[2] - center) <= 12.0:
                group.append(entry)
                continue
        groups.append([entry])

    text = "\n".join(" ".join(item[0] for item in group) for group in groups)
    confidence = sum(item[1] for item in accepted) / len(accepted)
    return text, confidence


def _ocr_reading(
    reader: object | None,
    engine: str,
    crop: object,
    *,
    min_confidence: float = 0.0,
    use_variants: bool = True,
) -> tuple[str, float, str]:
    """Baca crop pada beberapa variasi gambar dan pilih hasil paling lengkap."""
    if reader is None:
        return "", 0.0, "none"
    try:
        variants = enhance_variants(crop) if use_variants else [("original", crop)]
    except Exception:
        variants = [("original", crop)]

    best = (0.0, "", "original", 0.0)
    for name, image in variants:
        entries = _ocr_lines(reader, engine, image)
        text, confidence = _assemble_lines(entries, min_confidence)
        if not text:
            continue
        accepted = [entry for entry in entries if entry[0].strip() and entry[1] >= min_confidence]
        score = sum(entry[1] * len(entry[0].strip()) for entry in accepted)
        if score > best[0]:
            best = (score, text, name, confidence)
    return best[1], best[3], best[2]


def _ocr_text(
    reader: object | None,
    engine: str,
    crop: object,
    *,
    min_confidence: float = 0.0,
    use_variants: bool = True,
) -> str:
    return _ocr_reading(
        reader, engine, crop, min_confidence=min_confidence, use_variants=use_variants
    )[0]


MONEY_RE = re.compile(
    r"(?<![\d.,])(?:rp\.?\s*)?(?:\d{1,3}(?:[.,]\d{3})+(?:[.,]\d{1,2})?|\d+)(?![\d.,])",
    re.IGNORECASE,
)
DATE_RE = re.compile(r"\b(\d{1,4})[/\-.](\d{1,2})[/\-.](\d{2,4})\b")
DATE_TEXT_RE = re.compile(r"\b(\d{1,2})[\s/.\-]+([A-Za-z]{3,9})[\s/.\-]+(\d{2,4})\b")
DATE_TEXT_REVERSED_RE = re.compile(
    r"\b([A-Za-z]{3,9})[\s/.\-]+(\d{1,2})(?:st|nd|rd|th)?[\s,.\-]+(\d{2,4})\b"
)
RECEIPT_NO_RE = re.compile(
    r"\b(?:no\.?|nomor|nos?)(?=\s|$)\s*(?:struk|nota|invoice|kwitansi|transaksi|faktur)?\s*"
    r"[:#\-]?\s*([A-Za-z0-9][A-Za-z0-9\-/]{2,})",
    re.IGNORECASE,
)
MONTH_ALIASES = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "mei": 5,
    "jun": 6,
    "jul": 7,
    "ags": 8,
    "agu": 8,
    "aug": 8,
    "sep": 9,
    "okt": 10,
    "oct": 10,
    "nov": 11,
    "des": 12,
    "dec": 12,
}
_LABEL_TRANSLATION = str.maketrans({"0": "o", "1": "l", "5": "s", "8": "b", "|": "l"})
SUBTOTAL_LABEL_RE = re.compile(r"\b(?:sub[\s\-]?total|subtot)\b", re.IGNORECASE)
TAX_LABEL_RE = re.compile(r"\b(?:pajak|ppn|tax|vat)\b", re.IGNORECASE)
TOTAL_LABEL_RE = re.compile(
    r"\b(?:grand[\s\-]?total|total[\s\-]?(?:bayar|pembayaran|belanja|pembelian)"
    r"|jumlah[\s\-]?(?:bayar|total)|to+ta[l1]|totai|tagihan|total)\b",
    re.IGNORECASE,
)
PHONE_RE = re.compile(r"\b0\d{8,}\b")
IGNORED_MERCHANT_RE = re.compile(
    r"^(?:no\.?|nomor|tanggal|tgl|date|terima kasih|thank you|struk|nota|invoice|kwitansi)\b",
    re.IGNORECASE,
)


def _label_text(value: str) -> str:
    """Samakan kekeliruan huruf/angka OCR hanya untuk pencocokan label."""
    return value.lower().translate(_LABEL_TRANSLATION)


def _money(raw: str) -> int | None:
    value = re.sub(r"[^\d.,]", "", raw)
    if not value:
        return None
    decimal_sep = "," if value.rfind(",") > value.rfind(".") else "."
    thousands_sep = "." if decimal_sep == "," else ","
    if decimal_sep in value:
        head, separator, tail = value.rpartition(decimal_sep)
        if separator and len(tail) == 2:
            value = head
    digits = re.sub(r"\D", "", value.replace(thousands_sep, ""))
    return int(digits) if digits else None


def _first_amount(value: str) -> int | None:
    for match in MONEY_RE.finditer(value):
        amount = _money(match.group(0))
        if amount:
            return amount
    return None


def _labeled_amounts(lines: list[str], pattern: re.Pattern[str]) -> list[int]:
    """Ambil nominal yang mengikuti sebuah label, dengan fallback label di akhir baris."""
    found: list[int] = []
    for line in lines:
        matches = list(pattern.finditer(_label_text(line)))
        if not matches:
            continue
        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(line)
            amount = _first_amount(line[match.end() : end])
            if amount is None:
                raw_before = MONEY_RE.findall(line[: match.start()])
                before = [value for value in (_money(item) for item in raw_before) if value]
                amount = before[-1] if before else None
            if amount:
                found.append(amount)
    return found


def _build_date(year: int, month: int, day: int) -> str | None:
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def _normalize_year(value: str) -> int:
    year = int(value)
    if year < 100:
        year += 2000 if year < 70 else 1900
    return year


def _date_from(lines: list[str]) -> str | None:
    for line in lines:
        for match in DATE_RE.finditer(line):
            first_text = match.group(1)
            first, month, last = (int(part) for part in match.groups())
            if len(first_text) == 4 or first > 31:
                year, day = first, last
            else:
                day = first
                year = _normalize_year(match.group(3))
            result = _build_date(year, month, day)
            if result:
                return result

        for match in DATE_TEXT_RE.finditer(line):
            day = int(match.group(1))
            month = MONTH_ALIASES.get(match.group(2)[:3].lower())
            if month is None:
                continue
            result = _build_date(_normalize_year(match.group(3)), month, day)
            if result:
                return result

        for match in DATE_TEXT_REVERSED_RE.finditer(line):
            month = MONTH_ALIASES.get(match.group(1)[:3].lower())
            if month is None:
                continue
            result = _build_date(_normalize_year(match.group(3)), month, int(match.group(2)))
            if result:
                return result
    return None


def _clean_text(value: str, limit: int) -> str:
    cleaned = re.sub(r"[^\w\s.,&/()\-]", " ", value)
    return re.sub(r"\s+", " ", cleaned).strip()[:limit]


def _merchant_from(lines: list[str]) -> str:
    """Pilih nama toko pertama dan buang tanggal, nomor, serta nomor telepon."""
    for line in lines:
        value = line.strip(" :-|")
        if len(value) < 3 or IGNORED_MERCHANT_RE.match(value):
            continue
        cut = len(value)
        for pattern in (DATE_RE, DATE_TEXT_RE, DATE_TEXT_REVERSED_RE, RECEIPT_NO_RE, PHONE_RE):
            match = pattern.search(value)
            if match:
                cut = min(cut, match.start())
        value = value[:cut].strip(" .,:;-|")
        if len(value) > 2:
            return value
    return ""


def parse_fields(regions: list[dict]) -> dict:
    """Ubah teks hasil OCR per kelas menjadi field struk terstruktur."""
    by_class: dict[str, list[str]] = defaultdict(list)
    for region in regions:
        class_name = str(region.get("class", ""))
        for line in str(region.get("text", "")).splitlines():
            if line.strip():
                by_class[class_name].append(line.strip())

    specific = {name: lines for name, lines in by_class.items() if name not in CONTAINER_CLASSES}
    source = specific or by_class
    all_lines = [line for lines in source.values() for line in lines]

    total_lines = list(source.get("total", []))
    for line in all_lines:
        if TOTAL_LABEL_RE.search(_label_text(line)) and line not in total_lines:
            total_lines.append(line)

    subtotal_values = _labeled_amounts(all_lines, SUBTOTAL_LABEL_RE)
    tax_values = _labeled_amounts(all_lines, TAX_LABEL_RE)
    total_values = _labeled_amounts(total_lines, TOTAL_LABEL_RE)
    subtotal = subtotal_values[0] if subtotal_values else None
    tax = tax_values[0] if tax_values else None
    total = total_values[0] if total_values else None
    if total is None and subtotal is not None and tax is not None:
        total = subtotal + tax
    if total is None:
        amounts = [
            amount
            for line in all_lines
            if not DATE_RE.search(line)
            and not DATE_TEXT_RE.search(line)
            and not RECEIPT_NO_RE.search(line)
            for amount in (_money(value) for value in MONEY_RE.findall(line))
            if amount
        ]
        total = max(amounts) if amounts else None
    if total is None:
        total = (subtotal or 0) + (tax or 0)

    receipt_no = ""
    for line in all_lines:
        match = RECEIPT_NO_RE.search(line)
        if not match:
            continue
        candidate = match.group(1).upper()[:128]
        if re.fullmatch(r"\d{1,3}[.,]\d{3}", candidate):
            continue
        receipt_no = candidate
        break

    joined = " ".join(all_lines).lower()
    payment_method = ""
    for keyword, label in (
        ("qris", "QRIS"),
        ("tunai", "Tunai"),
        ("cash", "Tunai"),
        ("debit", "Debit"),
        ("kredit", "Kredit"),
        ("transfer", "Transfer"),
    ):
        if re.search(rf"\b{re.escape(keyword)}\b", joined):
            payment_method = label
            break

    item_lines: list[str] = []
    for line in source.get("item_belanja", []):
        translated = _label_text(line)
        if any(pattern.search(translated) for pattern in (TOTAL_LABEL_RE, SUBTOTAL_LABEL_RE, TAX_LABEL_RE)):
            continue
        if not item_lines or item_lines[-1] != line:
            item_lines.append(line)
    description = "; ".join(item_lines)
    direction = (
        "income"
        if re.search(r"\b(?:penerimaan|uang masuk|refund|setoran|pendapatan)\b", joined)
        else "expense"
    )

    return {
        "transaction_date": _date_from(all_lines),
        "merchant": _clean_text(_merchant_from(source.get("info_toko", [])), 255),
        "receipt_number": receipt_no,
        "description": _clean_text(description, 1000),
        "direction": direction,
        "subtotal": subtotal,
        "tax": tax,
        "total": total,
        "currency": "IDR",
        "payment_method": payment_method,
    }


def _tensor_value(value: object, default: float = 0.0) -> float:
    try:
        return float(value.item())
    except (AttributeError, TypeError, ValueError):
        try:
            return float(value)
        except (TypeError, ValueError):
            return default


def _region_entries(result: object, width: int, height: int) -> list[dict]:
    """Ubah boxes YOLO menjadi entri yang sudah diurutkan sesuai urutan baca."""
    names = getattr(result, "names", {}) or {}
    boxes = getattr(result, "boxes", None)
    entries: list[dict] = []
    if boxes is None:
        return entries

    for box in boxes:
        try:
            xyxy = [float(value) for value in box.xyxy[0].tolist()]
        except (AttributeError, IndexError, TypeError, ValueError):
            continue
        x1, y1, x2, y2 = xyxy
        if x2 <= x1 or y2 <= y1:
            continue
        confidence = _tensor_value(box.conf[0]) if box.conf is not None else 0.0
        class_id = int(_tensor_value(box.cls[0], -1.0)) if box.cls is not None else -1
        try:
            class_name = str(names[class_id])
        except (KeyError, IndexError, TypeError):
            class_name = str(class_id)
        entries.append(
            {
                "class_id": class_id,
                "class": class_name,
                "confidence": round(confidence, 4),
                "bbox": [round(value, 1) for value in xyxy],
                "bbox_normalized": [
                    round(x1 / width, 6),
                    round(y1 / height, 6),
                    round(x2 / width, 6),
                    round(y2 / height, 6),
                ],
            }
        )

    entries.sort(key=lambda item: (item["bbox"][1], item["bbox"][0], -item["confidence"]))
    return entries


def _box_overlap(first: list[float], second: list[float]) -> float:
    ix1, iy1 = max(first[0], second[0]), max(first[1], second[1])
    ix2, iy2 = min(first[2], second[2]), min(first[3], second[3])
    intersection = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    first_area = max(0.0, first[2] - first[0]) * max(0.0, first[3] - first[1])
    second_area = max(0.0, second[2] - second[0]) * max(0.0, second[3] - second[1])
    union = first_area + second_area - intersection
    return intersection / union if union else 0.0


def _suppress_duplicates(entries: list[dict], threshold: float = 0.85) -> list[dict]:
    """Buang deteksi ganda yang saling tumpang tindih pada kelas yang sama."""
    kept: list[dict] = []
    for entry in entries:
        duplicate = next(
            (
                other
                for other in kept
                if entry["class"] == other["class"]
                and _box_overlap(entry["bbox"], other["bbox"]) >= threshold
            ),
            None,
        )
        if duplicate is None:
            kept.append(entry)
        elif entry["confidence"] > duplicate["confidence"]:
            duplicate.update(entry)
    kept.sort(key=lambda item: (item["bbox"][1], item["bbox"][0], -item["confidence"]))
    return kept


def predict_receipt(
    image_bytes: bytes,
    *,
    root: Path = ML_ROOT,
    conf: float = 0.25,
    iou: float = 0.45,
    imgsz: int = 640,
    ocr_engine: str = "paddleocr",
    ocr_min_confidence: float = 0.35,
    ocr_variants: bool = True,
) -> ReceiptPrediction:
    """Deteksi ROI struk lalu baca teks setiap ROI dengan OCR lokal."""
    detector = load_detector(Path(root))
    rgb = load_rgb(image_bytes)
    results = detector.predict(source=rgb, conf=conf, iou=iou, imgsz=imgsz, verbose=False)
    result = results[0]
    height, width = rgb.shape[:2]

    reader, engine_name = _load_ocr(ocr_engine)
    entries = _suppress_duplicates(_region_entries(result, width, height))
    regions: list[dict] = []
    for entry in entries:
        crop = crop_region(rgb, entry["bbox"], padding=4)
        text, ocr_confidence, variant = _ocr_reading(
            reader,
            engine_name,
            crop,
            min_confidence=ocr_min_confidence,
            use_variants=ocr_variants,
        )
        region = dict(entry)
        region["text"] = text
        if ocr_confidence:
            region["ocr_confidence"] = round(ocr_confidence, 4)
            region["ocr_variant"] = variant
        regions.append(region)

    fields = parse_fields(regions)
    raw = {
        "fields": fields,
        "regions": regions,
        "model": model_version(Path(root)),
        "ocr_engine": engine_name,
        "ocr": {"min_confidence": ocr_min_confidence, "variants": ocr_variants},
    }
    return ReceiptPrediction(
        fields=fields,
        regions=regions,
        raw_text=json.dumps(raw, ensure_ascii=False),
        model=model_version(Path(root)),
        ocr_engine=engine_name,
    )
