import json
import subprocess
import sys
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR / "ocr-ml" / "src"))
import bootstrap  # noqa: E402

bootstrap.ensure_ocr_ml_package()

from ocr_ml.data import feedback_sync, make_dataset  # noqa: E402
from ocr_ml.models import predict_model  # noqa: E402
from ocr_ml.models.predict_model import (  # noqa: E402
    _ocr_reading,
    _region_entries,
    _suppress_duplicates,
    parse_fields,
)


def tiny_jpeg() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (10, 10), (220, 220, 220)).save(buffer, format="JPEG")
    return buffer.getvalue()


def test_make_dataset_converts_roboflow_annotations(tmp_path):
    root = tmp_path / "ocr-ml"
    train = root / "data" / "train"
    train.mkdir(parents=True)
    (train / "struk.jpg").write_bytes(tiny_jpeg())
    (train / "_annotations.csv").write_text(
        "filename,width,height,class,xmin,ymin,xmax,ymax\n"
        "struk.jpg,10,10,struk_belanja,0,0,10,10\n"
        "struk.jpg,10,10,total,2,3,8,5\n",
        encoding="utf-8",
    )

    summary = make_dataset.build_dataset(root)

    assert summary["classes"] == ["struk_belanja", "total"]
    assert summary["splits"]["train"] == {"images": 1, "annotations": 2, "missing_images": 0}
    label = (root / "data" / "processed" / "train" / "labels" / "struk.txt").read_text()
    assert label.splitlines() == ["0 0.500000 0.500000 1.000000 1.000000", "1 0.500000 0.400000 0.600000 0.200000"]
    yaml_text = (root / "configs" / "data.yaml").read_text()
    assert "nc: 2" in yaml_text
    assert "train: train/images" in yaml_text
    assert "val: valid/images" in yaml_text
    assert "  - total" in yaml_text
    assert (root / "data" / "interim" / "train_annotations_yolo.csv").is_file()


def test_feedback_sync_roundtrip_and_deduplication(tmp_path):
    root = tmp_path / "ocr-ml"
    (root / "configs").mkdir(parents=True)
    (root / "configs" / "classes.txt").write_text("struk_belanja\ntotal\n", encoding="utf-8")
    image = tiny_jpeg()

    first = feedback_sync.save_feedback(
        image,
        boxes=[{"class": "total", "xmin": 0, "ymin": 0, "xmax": 8, "ymax": 4}],
        fields={"total": 25000, "direction": "expense", "receipt_number": "INV/001", "notes": "dirty"},
        employee="staff1",
        role="user",
        scan_id=7,
        root=root,
    )
    duplicate = feedback_sync.save_feedback(
        image,
        boxes=[{"class": "total", "xmin": 0, "ymin": 0, "xmax": 8, "ymax": 4}],
        fields={"total": 25000},
        employee="staff1",
        role="user",
        scan_id=7,
        root=root,
    )
    assert first["status"] == "saved"
    assert duplicate["status"] == "duplicate"
    assert first["verified_count"] == 1

    records = feedback_sync.load_records(root / "data" / "feedback_loop")
    assert records[0]["employee"] == "staff1"
    assert records[0]["role"] == "user"
    assert records[0]["verified"] is True
    assert set(records[0]["fields"]) == {"total"}
    label = (root / "data" / "feedback_loop" / records[0]["label"]).read_text()
    assert label.strip() == "1 0.400000 0.200000 0.800000 0.400000"

    sync = feedback_sync.sync_feedback_to_dataset(root)
    assert sync["synced"] == 1
    assert list((root / "data" / "processed" / "train" / "images").glob("fb_*.jpg"))
    assert feedback_sync.sync_feedback_to_dataset(root)["synced"] == 0


def test_parse_fields_from_ocr_regions():
    regions = [
        {"class": "info_toko", "text": "TOKO BATU\n20/11/2026"},
        {"class": "item_belanja", "text": "BATERAI AA 2 pcs"},
        {"class": "total", "text": "SUBTOTAL 100.000\nPPN 10.000\nTOTAL 110.000\nTUNAI"},
    ]
    fields = parse_fields(regions)
    assert fields["merchant"] == "TOKO BATU"
    assert fields["transaction_date"] == "2026-11-20"
    assert fields["total"] == 110000
    assert fields["payment_method"] == "Tunai"
    assert set(fields) == {"transaction_date", "merchant", "description", "total", "currency", "payment_method"}


def test_parse_fields_handles_noisy_dates_and_money():
    regions = [
        {
            "class": "info_toko",
            "text": (
                "TOKO SUMBER REJEKI\nJl. Merdeka No. 12 Telp 081234567890\n"
                "NPWP 12.345.678.9-012.000\nTgl 2O/11/26 No Struk: INV/2026/11/001"
            ),
        },
        {
            "class": "item_belanja",
            "text": "Indomie Goreng 3.500\nAqua 600ml 4.000\nSUBTOTAL 9.500\nPPN 950",
        },
        {"class": "total", "text": "Grand T0tal 10.450\nTunai 20.000\nKembalian 9.550"},
    ]
    fields = parse_fields(regions)
    assert fields["transaction_date"] == "2026-11-20"
    assert fields["merchant"] == "TOKO SUMBER REJEKI"
    assert fields["description"] == "Indomie Goreng 3.500; Aqua 600ml 4.000"
    assert fields["total"] == 10450
    assert fields["payment_method"] == "Tunai"


def test_feedback_endpoint_saves_employee_correction(logged_in, sample_image, monkeypatch, tmp_path):
    import app.routers.scans as scans_module
    from app.config import get_settings

    root = tmp_path / "ocr-ml"
    (root / "configs").mkdir(parents=True)
    (root / "configs" / "classes.txt").write_text("struk_belanja\ninfo_toko\nitem_belanja\ntotal\n", encoding="utf-8")
    monkeypatch.setattr(get_settings(), "ml_root", root)
    regions = [{"class": "total", "confidence": 0.9, "bbox_normalized": [0.1, 0.2, 0.9, 0.3]}]
    monkeypatch.setattr(
        scans_module,
        "run_ocr",
        lambda data: {
            "fields": {"total": 1000},
            "regions": regions,
            "raw_text": json.dumps({"model": "v-test", "regions": regions}),
        },
    )
    csrf = logged_in.cookies.get("receipt_csrf")
    headers = {"X-CSRF-Token": csrf}
    response = logged_in.post(
        "/api/scans",
        data={"csrf": csrf},
        files={"image": ("struk.jpg", sample_image, "image/jpeg")},
        headers=headers,
    )
    assert response.status_code == 200
    scan_id = response.json()["scan_id"]

    response = logged_in.post(
        "/feedback",
        json={
            "scan_id": scan_id,
            "fields": {"total": 175000, "merchant": "Toko Koreksi"},
            "verified": True,
        },
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["status"] == "saved"
    records = feedback_sync.load_records(root / "data" / "feedback_loop")
    assert len(records) == 1
    assert records[0]["fields"]["merchant"] == "Toko Koreksi"
    assert records[0]["boxes"][0]["class"] == "total"


def test_retrain_endpoint_and_admin_status(logged_in, monkeypatch):
    import app.routers.admin as admin_module

    calls = []

    def fake_retrain(force=False):
        calls.append(force)
        return {"status": "skipped", "reason": "Belum cukup feedback"}

    monkeypatch.setattr(admin_module.ml, "retrain", fake_retrain)
    csrf = logged_in.cookies.get("receipt_csrf")
    response = logged_in.post("/retrain", headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200
    assert response.json()["status"] == "skipped"
    assert calls == [True]

    html = logged_in.get("/admin").text
    assert "Latih Ulang Model Sekarang" in html
    assert "Model OCR &amp; Self Learning Model" in html


def test_retrain_pipeline_waits_for_feedback_threshold(tmp_path):
    from ocr_ml.pipelines.retrain_pipeline import retrain

    result = retrain(tmp_path / "ocr-ml", force=False, min_feedback=25)
    assert result["status"] == "skipped"
    assert result["verified_feedback"] == 0


def test_resolved_data_yaml_uses_absolute_processed_path(tmp_path):
    from ocr_ml.models.evaluate_model import resolved_data_yaml

    root = tmp_path / "ocr-ml"
    (root / "configs").mkdir(parents=True)
    data_file = root / "configs" / "data.yaml"
    data_file.write_text("path: ../data/processed\ntrain: train/images\nval: valid/images\n", encoding="utf-8")

    with resolved_data_yaml(data_file, root) as runtime_file:
        content = runtime_file.read_text(encoding="utf-8")

    assert f"path: {root / 'data' / 'processed'}" in content
    assert "train: train/images" in content
    assert not runtime_file.exists()


@pytest.mark.parametrize("engine", ["paddleocr", "easyocr"])
def test_ocr_engine_configuration_is_valid(engine):
    from app.config import Settings

    settings = Settings(secret_key="x" * 32, admin_password="CompliantTestPass123!", ocr_engine=engine)
    assert settings.ocr_engine == engine


def test_training_script_runs_directly():
    script = BASE_DIR / "ocr-ml" / "src" / "models" / "train_model.py"
    result = subprocess.run(
        [sys.executable, str(script), "--help"],
        cwd=BASE_DIR,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "--epochs" in result.stdout


def test_parse_fields_prefers_labeled_amounts_and_ignores_container_duplicates():
    regions = [
        {"class": "struk_belanja", "text": "TOKO BATU 20/11/2026 999.999"},
        {"class": "info_toko", "text": "TOKO BATU\n20 November 2026\nNo. Struk: INV/001"},
        {"class": "item_belanja", "text": "BATERAI AA 2 pcs 55.000\nBATERAI AA 2 pcs 55.000"},
        {"class": "item_belanja", "text": "TOTAL Pembayaran 149.000"},
        {"class": "total", "text": "TOTAI 149.000\nSUBTOTAL 135.000\nPPN 14.000\nTUNAI"},
    ]

    fields = parse_fields(regions)

    assert fields["merchant"] == "TOKO BATU"
    assert fields["transaction_date"] == "2026-11-20"
    assert fields["total"] == 149000
    assert fields["description"] == "BATERAI AA 2 pcs 55.000"
    assert fields["payment_method"] == "Tunai"


def test_parse_fields_uses_container_when_no_specific_region_exists():
    fields = parse_fields([{"class": "struk_belanja", "text": "TOKO C\n21-11-2026\nGrand Total 110.000"}])
    assert fields["merchant"] == "TOKO C"
    assert fields["transaction_date"] == "2026-11-21"
    assert fields["total"] == 110000


def test_parse_fields_does_not_derive_total_from_subtotal_and_tax():
    fields = parse_fields([{"class": "total", "text": "SUBTOTAL 100.000\nPPN 10.000"}])
    assert fields["total"] == 0


def test_ocr_reading_sorts_lines_and_drops_low_confidence_results():
    class FakeEasyOCR:
        def readtext(self, crop, detail=1):
            return [
                ([(0, 50), (100, 50), (100, 60), (0, 60)], "TOKO BATU", 0.93),
                ([(0, 10), (100, 10), (100, 20), (0, 20)], "TOTAL 110.000", 0.91),
                ([(0, 30), (100, 30), (100, 40), (0, 40)], "noise", 0.12),
            ]

    text, confidence, variant = _ocr_reading(
        FakeEasyOCR(), "easyocr", object(), min_confidence=0.35, use_variants=False
    )

    assert text == "TOTAL 110.000\nTOKO BATU"
    assert variant == "original"
    assert 0.9 < confidence < 0.95


def test_ocr_reading_selects_the_best_enhancement_variant(monkeypatch):
    monkeypatch.setattr(
        predict_model,
        "enhance_variants",
        lambda crop, **kwargs: [("adaptive", "weak"), ("contrast", "strong")],
    )

    class FakeEasyOCR:
        def readtext(self, crop, detail=1):
            if crop == "weak":
                return [([(0, 0), (40, 0), (40, 10), (0, 10)], "TOKO", 0.55)]
            return [
                ([(0, 0), (60, 0), (60, 10), (0, 10)], "TOKO BATU", 0.94),
                ([(0, 20), (60, 20), (60, 30), (0, 30)], "TOTAL 149.000", 0.92),
            ]

    text, confidence, variant = _ocr_reading(FakeEasyOCR(), "easyocr", object(), min_confidence=0.3, use_variants=True)

    assert variant == "contrast"
    assert text == "TOKO BATU\nTOTAL 149.000"
    assert confidence > 0.9


def test_region_entries_are_sorted_and_duplicates_suppressed():
    class Scalar:
        def __init__(self, value):
            self.value = value

        def item(self):
            return self.value

    def box(xyxy, confidence, class_id):
        return SimpleNamespace(
            xyxy=[SimpleNamespace(tolist=lambda values=xyxy: values)],
            conf=[Scalar(confidence)],
            cls=[Scalar(class_id)],
        )

    result = SimpleNamespace(
        names={0: "struk_belanja", 1: "total"},
        boxes=[
            box([10, 80, 90, 95], 0.80, 1),
            box([10, 10, 90, 30], 0.70, 1),
            box([10, 11, 90, 31], 0.95, 1),
        ],
    )

    entries = _region_entries(result, 100, 100)
    assert [entry["bbox"][1] for entry in entries] == [10.0, 11.0, 80.0]

    kept = _suppress_duplicates(entries)
    assert len(kept) == 2
    assert kept[0]["confidence"] == 0.95
    assert kept[0]["bbox_normalized"] == [0.1, 0.11, 0.9, 0.31]


def test_enhancement_variants_upscale_small_text():
    np = pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    from ocr_ml.features.preprocess import enhance_variants

    crop = np.full((12, 80, 3), 210, dtype=np.uint8)
    variants = enhance_variants(crop)

    assert [name for name, _ in variants] == ["adaptive", "contrast", "otsu"]
    assert all(image.shape[0] > 12 for _, image in variants)


def test_ml_predict_forwards_accuracy_settings(monkeypatch):
    import app.ml as ml_module
    from app.config import get_settings

    captured = {}

    def fake_predict(image_bytes, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(as_dict=lambda: {"fields": {}})

    monkeypatch.setattr(predict_model, "predict_receipt", fake_predict)
    settings = get_settings()

    ml_module.predict(b"image")

    assert captured["ocr_min_confidence"] == settings.ocr_min_confidence
    assert captured["ocr_variants"] == settings.ocr_variants
