from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Receipt Recap"
    environment: str = "development"
    secret_key: str = Field(min_length=32)
    base_url: str = "http://localhost:8000"
    database_url: str = f"sqlite:///{BASE_DIR / 'data' / 'app.db'}"
    upload_dir: Path = BASE_DIR / "data" / "uploads"
    export_dir: Path = BASE_DIR / "data" / "exports"
    template_mapping_file: Path = BASE_DIR / "config" / "template_mapping.json"

    ml_root: Path = BASE_DIR / "ocr-ml"
    ocr_engine: str = "paddleocr"
    ocr_confidence: float = 0.25
    ocr_iou: float = 0.45
    ocr_imgsz: int = 640
    ocr_min_confidence: float = Field(default=0.35, ge=0.0, le=1.0)
    ocr_variants: bool = True
    feedback_threshold: int = 25
    retrain_epochs: int = 20

    max_upload_mb: int = 10
    max_image_pixels: int = 20_000_000
    max_image_side: int = 4096
    session_ttl_minutes: int = 480
    login_max_attempts: int = 5
    login_lockout_minutes: int = 15
    secure_cookies: bool = True
    admin_username: str = "admin"
    admin_password: str = Field(min_length=12)

    @field_validator("admin_password", mode="before")
    @classmethod
    def validate_admin_password(cls, value):
        if not isinstance(value, str):
            raise ValueError("ADMIN_PASSWORD harus berupa teks")
        if len(value) < 12:
            raise ValueError("ADMIN_PASSWORD minimal 12 karakter")
        if not any(char.isupper() for char in value):
            raise ValueError("ADMIN_PASSWORD harus memuat huruf besar")
        if not any(char.islower() for char in value):
            raise ValueError("ADMIN_PASSWORD harus memuat huruf kecil")
        if not any(char.isdigit() for char in value):
            raise ValueError("ADMIN_PASSWORD harus memuat angka")
        if not any(char in "!@#$%^&*()-_=+[]{};:,.<>?/" for char in value):
            raise ValueError("ADMIN_PASSWORD harus memuat simbol")
        return value

    @field_validator("ocr_engine")
    @classmethod
    def validate_ocr_engine(cls, value: str) -> str:
        engine = value.strip().lower()
        if engine not in {"paddleocr", "easyocr", "none"}:
            raise ValueError("OCR_ENGINE harus paddleocr, easyocr, atau none")
        return engine

    @field_validator("secure_cookies", "ocr_variants", mode="before")
    @classmethod
    def parse_bool(cls, value):
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return value

    @property
    def base_dir(self) -> Path:
        return BASE_DIR

    @property
    def is_production(self) -> bool:
        return self.environment.lower() in {"production", "prod"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
