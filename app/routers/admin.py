import re
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app import ml
from app.config import get_settings
from app.db import get_db
from app.deps import AuthContext, admin_context, validate_csrf
from app.models import AuditLog, User
from app.render import render
from app.security import add_audit, client_ip, hash_password, revoke_user_sessions

router = APIRouter(prefix="/admin", tags=["admin"])
ml_router = APIRouter(tags=["ml"])

USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]{3,64}$")


@router.get("")
async def admin_page(
    request: Request,
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(admin_context),
):
    users = db.scalars(select(User).order_by(User.username)).all()
    logs = db.scalars(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(100)).all()
    return render(
        request,
        "admin.html",
        db,
        users=users,
        logs=logs,
        model_info=(await run_in_threadpool(ml.registry_production)) or {},
        feedback_count=await run_in_threadpool(ml.verified_feedback_count),
        feedback_threshold=get_settings().feedback_threshold,
        pending_feedback=await run_in_threadpool(ml.pending_feedback),
    )


@router.post("/users")
async def create_user(
    request: Request,
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(admin_context),
    _: None = Depends(validate_csrf),
):
    form = await request.form()
    username = str(form.get("username") or "").strip().lower()
    full_name = str(form.get("full_name") or "").strip()
    role = str(form.get("role") or "user").strip()
    password = secrets.token_urlsafe(12)

    if not USERNAME_RE.fullmatch(username) or not full_name or role not in {"user", "admin"}:
        raise HTTPException(status_code=422, detail="Data karyawan belum lengkap.")
    if db.query(User).filter(User.username == username).first():
        raise HTTPException(status_code=409, detail="Nama pengguna sudah dipakai.")

    user = User(
        username=username,
        full_name=full_name[:128],
        password_hash=hash_password(password),
        role=role,
        must_change_password=True,
    )
    db.add(user)
    db.commit()
    add_audit(db, "user_created", user=ctx.user, target=username, ip_address=client_ip(request))

    return JSONResponse(
        {
            "username": username,
            "temporary_password": password,
            "message": (
                "Berikan kata sandi sementara ini kepada karyawan. Kata sandi wajib diganti saat masuk pertama kali."
            ),
        }
    )


@router.post("/users/{user_id}/status")
async def toggle_user(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(admin_context),
    _: None = Depends(validate_csrf),
):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Karyawan tidak ditemukan.")
    if user.id == ctx.user.id:
        raise HTTPException(status_code=400, detail="Akun sendiri tidak bisa dinonaktifkan.")
    if user.is_active and user.role == "admin":
        active_admins = db.scalar(
            select(func.count()).select_from(User).where(User.role == "admin", User.is_active.is_(True))
        )
        if active_admins is not None and active_admins <= 1:
            raise HTTPException(status_code=400, detail="Minimal satu administrator harus tetap aktif.")
    user.is_active = not user.is_active
    db.commit()
    if not user.is_active:
        revoke_user_sessions(db, user.id)
    add_audit(
        db,
        "user_status_changed",
        user=ctx.user,
        target=user.username,
        ip_address=client_ip(request),
        detail=f"active={user.is_active}",
    )
    return {"username": user.username, "is_active": user.is_active}


@router.post("/users/{user_id}/reset-password")
async def reset_user_password(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(admin_context),
    _: None = Depends(validate_csrf),
):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Karyawan tidak ditemukan.")
    if user.id == ctx.user.id:
        raise HTTPException(status_code=400, detail="Gunakan halaman ganti kata sandi untuk akun sendiri.")

    password = secrets.token_urlsafe(12)
    user.password_hash = hash_password(password)
    user.must_change_password = True
    db.commit()
    revoke_user_sessions(db, user.id)
    add_audit(
        db,
        "user_password_reset",
        user=ctx.user,
        target=user.username,
        ip_address=client_ip(request),
    )
    return JSONResponse(
        {
            "username": user.username,
            "temporary_password": password,
            "message": "Kata sandi sementara baru berlaku. Karyawan wajib menggantinya saat masuk.",
        }
    )


@router.post("/feedback/{feedback_id}/verify")
async def verify_feedback(
    feedback_id: str,
    request: Request,
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(admin_context),
    _: None = Depends(validate_csrf),
):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", feedback_id):
        raise HTTPException(status_code=422, detail="ID feedback tidak valid.")
    try:
        result = await run_in_threadpool(ml.verify_feedback, feedback_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Feedback tidak ditemukan.") from exc
    add_audit(
        db,
        "feedback_verified",
        user=ctx.user,
        target=feedback_id,
        ip_address=client_ip(request),
    )
    return result


@ml_router.post("/retrain")
async def retrain_model(
    request: Request,
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(admin_context),
    _: None = Depends(validate_csrf),
):
    try:
        result = await run_in_threadpool(lambda: ml.retrain(force=True))
    except Exception as exc:
        add_audit(
            db,
            "retrain_failed",
            user=ctx.user,
            ip_address=client_ip(request),
            detail=type(exc).__name__,
        )
        raise HTTPException(status_code=500, detail="Proses latih ulang gagal. Periksa log model.") from exc

    add_audit(
        db,
        "retrain_requested",
        user=ctx.user,
        ip_address=client_ip(request),
        detail=f"status={result.get('status')}",
    )
    return result


@ml_router.post("/rollback")
async def rollback_model(
    request: Request,
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(admin_context),
    _: None = Depends(validate_csrf),
):
    try:
        result = await run_in_threadpool(ml.rollback_model)
    except Exception as exc:
        add_audit(
            db,
            "model_rollback_failed",
            user=ctx.user,
            ip_address=client_ip(request),
            detail=type(exc).__name__,
        )
        raise HTTPException(status_code=500, detail="Rollback model gagal. Periksa log model.") from exc

    add_audit(
        db,
        "model_rollback",
        user=ctx.user,
        ip_address=client_ip(request),
        detail=f"status={result.get('status')}",
    )
    return result
