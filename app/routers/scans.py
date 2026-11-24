import json
import logging
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse, JSONResponse
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile

from app import ml
from app.config import get_settings
from app.db import get_db
from app.deps import AuthContext, required_context, validate_csrf
from app.excel import export_scans
from app.images import ImageValidationError, normalize_image
from app.models import Export, Scan, utcnow
from app.ocr import OcrError, receipt_from_prediction, run_ocr
from app.render import render
from app.schemas import FeedbackRequest, MessageResponse, ReceiptUpdate, ScanResponse
from app.security import add_audit, client_ip, safe_relative_path

settings = get_settings()
router = APIRouter(tags=["scans"])
logger = logging.getLogger(__name__)

FILENAME_RE = re.compile(r"^[A-Za-z0-9_-]+\.xls[x]?$")
FEEDBACK_BOX_KEYS = {
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
}


def _owned_scan(db: Session, scan_id: int, ctx: AuthContext) -> Scan:
    scan = db.get(Scan, scan_id)
    if scan is None:
        raise HTTPException(status_code=404, detail="Data tidak ditemukan")
    if scan.owner_id != ctx.user.id and ctx.user.role != "admin":
        raise HTTPException(status_code=404, detail="Data tidak ditemukan")
    return scan


def _no_store(response):
    response.headers["Cache-Control"] = "no-store"
    return response


def _to_response(scan: Scan) -> ScanResponse:
    return ScanResponse(
        id=scan.id,
        status=scan.status,
        transaction_date=scan.transaction_date,
        merchant=scan.merchant,
        receipt_number=scan.receipt_number,
        description=scan.description,
        direction=scan.direction,
        subtotal=scan.subtotal,
        tax=scan.tax,
        total=scan.total,
        currency=scan.currency,
        payment_method=scan.payment_method,
        notes=scan.notes,
        created_at=scan.created_at.isoformat(),
    )


def _decode_raw(raw: str) -> dict:
    try:
        value = json.loads(raw or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _boxes_from_raw(raw: str) -> list[dict]:
    boxes = []
    for region in _decode_raw(raw).get("regions", []):
        if not isinstance(region, dict):
            continue
        normalized = region.get("bbox_normalized")
        if not isinstance(normalized, list) or len(normalized) != 4:
            continue
        x1, y1, x2, y2 = (float(value) for value in normalized)
        boxes.append(
            {
                "class": region.get("class"),
                "x_center": (x1 + x2) / 2,
                "y_center": (y1 + y2) / 2,
                "width": x2 - x1,
                "height": y2 - y1,
            }
        )
    return boxes


def _sanitize_boxes(boxes: list[dict] | None) -> list[dict]:
    cleaned = []
    for box in boxes or []:
        if isinstance(box, dict):
            cleaned.append(
                {
                    key: value
                    for key, value in box.items()
                    if key in FEEDBACK_BOX_KEYS and isinstance(value, (int, float, str)) and not isinstance(value, bool)
                }
            )
    return cleaned


def _store_feedback(
    request: Request,
    scan: Scan,
    ctx: AuthContext,
    fields: dict,
    boxes: list[dict] | None,
    verified: bool,
) -> dict:
    source = settings.upload_dir / Path(scan.image_path)
    if not source.is_file() or not safe_relative_path(settings.upload_dir, source):
        return {"status": "skipped", "reason": "gambar_tidak_ditemukan"}
    return ml.store_feedback(
        source.read_bytes(),
        boxes=_sanitize_boxes(boxes),
        fields=fields,
        employee=ctx.user.username,
        role=ctx.user.role,
        scan_id=scan.id,
        verified=verified,
        model=str(_decode_raw(scan.raw_ocr).get("model", "")),
    )


@router.get("/scan")
async def scan_page(request: Request, db: Session = Depends(get_db), ctx: AuthContext = Depends(required_context)):
    return _no_store(render(request, "scan.html", db))


@router.post("/scan")
@router.post("/api/scans")
async def create_scan(
    request: Request,
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(required_context),
    _: None = Depends(validate_csrf),
):
    form = await request.form()
    upload = form.get("image")
    if not isinstance(upload, UploadFile):
        raise HTTPException(status_code=400, detail="Foto dokumen belum dipilih.")

    data = await upload.read()
    try:
        normalized, digest, media_type = normalize_image(data, upload.content_type)
    except ImageValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    duplicate = (
        db.query(Scan)
        .filter(Scan.owner_id == ctx.user.id, Scan.image_sha256 == digest)
        .first()
    )
    if duplicate is not None:
        add_audit(db, "scan_duplicate", user=ctx.user, target=str(duplicate.id), ip_address=client_ip(request))
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"message": "Dokumen ini sudah pernah dibaca.", "scan_id": duplicate.id},
        )

    try:
        prediction = run_ocr(normalized)
        receipt = receipt_from_prediction(prediction)
        raw = str(prediction.get("raw_text", ""))
    except OcrError as exc:
        add_audit(db, "scan_ocr_failed", user=ctx.user, ip_address=client_ip(request), detail=str(exc))
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    now = datetime.now(UTC)
    relative = Path(f"{now:%Y/%m}") / f"{uuid.uuid4().hex}.jpg"
    destination = settings.upload_dir / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(normalized)

    scan = Scan(
        owner_id=ctx.user.id,
        status="draft",
        image_path=relative.as_posix(),
        image_sha256=digest,
        image_media_type=media_type,
        transaction_date=receipt.transaction_date,
        merchant=receipt.merchant,
        receipt_number=receipt.receipt_number,
        description=receipt.description,
        direction=receipt.direction,
        subtotal=receipt.subtotal,
        tax=receipt.tax,
        total=receipt.total,
        currency=receipt.currency,
        payment_method=receipt.payment_method,
        raw_ocr=raw,
    )
    db.add(scan)
    db.commit()
    db.refresh(scan)
    add_audit(db, "scan_created", user=ctx.user, target=str(scan.id), ip_address=client_ip(request))

    return {"scan_id": scan.id, "receipt": _to_response(scan).model_dump()}


@router.get("/api/scans/{scan_id}/image")
async def scan_image(
    scan_id: int,
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(required_context),
):
    scan = _owned_scan(db, scan_id, ctx)
    path = settings.upload_dir / Path(scan.image_path)
    if not path.is_file() or not safe_relative_path(settings.upload_dir, path):
        raise HTTPException(status_code=404, detail="Foto dokumen tidak ditemukan.")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@router.get("/scans/{scan_id}/review")
async def review_page(
    scan_id: int,
    request: Request,
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(required_context),
):
    scan = _owned_scan(db, scan_id, ctx)
    response = render(request, "review.html", db, scan=_to_response(scan), image_version=scan.image_sha256)
    return _no_store(response)


@router.post("/api/scans/{scan_id}/confirm")
async def confirm_scan(
    scan_id: int,
    request: Request,
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(required_context),
    _: None = Depends(validate_csrf),
):
    scan = _owned_scan(db, scan_id, ctx)
    if request.headers.get("content-type", "").startswith("application/json"):
        try:
            payload = await request.json()
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Isi data tidak valid.") from exc
    else:
        payload = dict(await request.form())
    try:
        update = ReceiptUpdate.model_validate(payload)
    except ValidationError as exc:
        fields = ", ".join(".".join(str(part) for part in error["loc"]) for error in exc.errors())
        add_audit(
            db,
            "scan_confirm_failed",
            user=ctx.user,
            target=str(scan.id),
            ip_address=client_ip(request),
            detail=fields,
        )
        logger.warning("Confirm validation failed for scan %s: %s", scan_id, fields)
        raise HTTPException(status_code=422, detail=f"Data belum lengkap. Periksa kembali: {fields}") from exc

    for field in (
        "transaction_date",
        "merchant",
        "receipt_number",
        "description",
        "direction",
        "subtotal",
        "tax",
        "total",
        "currency",
        "payment_method",
        "notes",
    ):
        setattr(scan, field, getattr(update, field))

    scan.status = "confirmed"
    scan.confirmed_at = utcnow()
    scan.confirmed_by_id = ctx.user.id
    db.commit()
    add_audit(db, "scan_confirmed", user=ctx.user, target=str(scan.id), ip_address=client_ip(request))

    try:
        feedback = _store_feedback(
            request,
            scan,
            ctx,
            update.model_dump(),
            _boxes_from_raw(scan.raw_ocr),
            verified=True,
        )
        add_audit(
            db,
            "feedback_saved",
            user=ctx.user,
            target=str(scan.id),
            ip_address=client_ip(request),
            detail=f"status={feedback.get('status')}",
        )
    except Exception as exc:
        add_audit(
            db,
            "feedback_failed",
            user=ctx.user,
            target=str(scan.id),
            ip_address=client_ip(request),
            detail=type(exc).__name__,
        )
    return MessageResponse(message="Data tersimpan", scan_id=scan.id)


@router.post("/feedback")
async def save_feedback(
    payload: FeedbackRequest,
    request: Request,
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(required_context),
    _: None = Depends(validate_csrf),
):
    scan = _owned_scan(db, payload.scan_id, ctx)
    boxes = payload.boxes if payload.boxes is not None else _boxes_from_raw(scan.raw_ocr)
    try:
        result = _store_feedback(
            request,
            scan,
            ctx,
            payload.fields.model_dump(),
            boxes,
            verified=payload.verified,
        )
    except Exception as exc:
        add_audit(
            db,
            "feedback_failed",
            user=ctx.user,
            target=str(scan.id),
            ip_address=client_ip(request),
            detail=type(exc).__name__,
        )
        raise HTTPException(status_code=500, detail="Gagal menyimpan data feedback.") from exc

    add_audit(
        db,
        "feedback_saved",
        user=ctx.user,
        target=str(scan.id),
        ip_address=client_ip(request),
        detail=f"status={result.get('status')}",
    )
    return result


@router.post("/api/scans/{scan_id}/delete")
async def delete_scan(
    scan_id: int,
    request: Request,
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(required_context),
    _: None = Depends(validate_csrf),
):
    scan = _owned_scan(db, scan_id, ctx)
    relative = Path(scan.image_path)
    absolute = settings.upload_dir / relative
    if safe_relative_path(settings.upload_dir, absolute) and absolute.is_file():
        absolute.unlink()
    db.delete(scan)
    db.commit()
    add_audit(db, "scan_deleted", user=ctx.user, target=str(scan_id), ip_address=client_ip(request))
    return MessageResponse(message="Data dihapus")


@router.get("/history")
async def history_page(request: Request, db: Session = Depends(get_db), ctx: AuthContext = Depends(required_context)):
    statement = select(Scan).order_by(Scan.created_at.desc()).limit(200)
    if ctx.user.role != "admin":
        statement = statement.where(Scan.owner_id == ctx.user.id)
    scans = db.scalars(statement).all()
    return _no_store(render(request, "history.html", db, scans=[_to_response(s) for s in scans]))


@router.post("/api/exports")
async def create_export(
    request: Request,
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(required_context),
    _: None = Depends(validate_csrf),
):
    statement = select(Scan).where(Scan.status == "confirmed").order_by(Scan.created_at)
    if ctx.user.role != "admin":
        statement = statement.where(Scan.owner_id == ctx.user.id)
    scans = db.scalars(statement).all()
    if not scans:
        raise HTTPException(status_code=400, detail="Belum ada data yang sudah disimpan.")

    filename = f"recap_{ctx.user.username}_{datetime.now(UTC):%Y%m%d%H%M%S}_{uuid.uuid4().hex[:8]}.xls"
    try:
        path, media_type = export_scans(list(scans), filename)
    except Exception as exc:
        add_audit(db, "export_failed", user=ctx.user, ip_address=client_ip(request), detail=type(exc).__name__)
        raise HTTPException(status_code=500, detail="Laporan Excel belum bisa dibuat. Silakan coba lagi.") from exc

    db.add(Export(filename=path.name, owner_id=ctx.user.id))
    db.commit()
    add_audit(
        db,
        "export_created",
        user=ctx.user,
        target=path.name,
        ip_address=client_ip(request),
        detail=f"rows={len(scans)}",
    )
    return {"download_url": f"/downloads/{path.name}", "rows": len(scans), "media_type": media_type}


@router.get("/downloads/{filename}")
async def download_export(
    filename: str,
    request: Request,
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(required_context),
):
    if not FILENAME_RE.fullmatch(filename):
        raise HTTPException(status_code=404, detail="File tidak ditemukan")
    record = db.query(Export).filter(Export.filename == filename).one_or_none()
    if record is None or (record.owner_id != ctx.user.id and ctx.user.role != "admin"):
        raise HTTPException(status_code=404, detail="File tidak ditemukan")
    path = settings.export_dir / filename
    if not path.is_file() or not safe_relative_path(settings.export_dir, path):
        raise HTTPException(status_code=404, detail="File tidak ditemukan")
    add_audit(db, "export_downloaded", user=ctx.user, target=filename, ip_address=client_ip(request))
    return FileResponse(
        path,
        filename=f"Recap-{datetime.now(UTC):%Y%m%d}.xls",
        media_type="application/vnd.ms-excel",
    )
