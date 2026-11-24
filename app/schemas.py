import math
import re
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


def _clean(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    cleaned = "".join(ch for ch in value if ch.isprintable() or ch in "\n\r\t").strip()
    return cleaned[:limit] or None


def _coerce_amount(value, default):
    if value is None:
        return default
    if isinstance(value, float) and not math.isfinite(value):
        return default
    if isinstance(value, str):
        text = value.strip().replace("\u00a0", "").replace(" ", "")
        if not text:
            return default
        comma_decimal = re.fullmatch(r"-?\d+,\d{1,2}", text) is not None
        dot_decimal = re.fullmatch(r"-?\d+\.\d{1,2}", text) is not None
        if not comma_decimal and not dot_decimal:
            text = text.replace(",", "").replace(".", "")
        elif comma_decimal:
            text = text.replace(",", ".")
        try:
            return int(round(float(text)))
        except ValueError:
            return value
    return value


class ExtractedReceipt(StrictModel):
    transaction_date: str | None = Field(default=None, max_length=10)
    merchant: str | None = Field(default=None, max_length=255)
    receipt_number: str | None = Field(default=None, max_length=128)
    description: str = Field(default="", max_length=1000)
    direction: Literal["expense", "income"] = "expense"
    subtotal: int | None = Field(default=None, ge=0, le=10**15)
    tax: int | None = Field(default=None, ge=0, le=10**15)
    total: int = Field(default=0, ge=0, le=10**15)
    currency: str = Field(default="IDR", max_length=8)
    payment_method: str | None = Field(default=None, max_length=64)

    @field_validator("transaction_date")
    @classmethod
    def validate_date(cls, value: str | None) -> str | None:
        if value in (None, "", "null"):
            return None
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d %b %Y", "%d %B %Y"):
            try:
                return datetime.strptime(value.strip(), fmt).date().isoformat()
            except ValueError:
                continue
        return None

    @field_validator("merchant", "receipt_number", "description", "payment_method", "currency")
    @classmethod
    def sanitize_text(cls, value):
        return _clean(value, 1000) or ""

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        return (value or "IDR").upper()[:8]

    @model_validator(mode="after")
    def derive_total(self):
        if self.total == 0:
            candidates = [v for v in (self.subtotal, self.tax) if v is not None]
            if candidates:
                self.total = sum(candidates)
        return self


class ReceiptUpdate(StrictModel):
    transaction_date: str | None = Field(default=None, max_length=10)
    merchant: str | None = Field(default=None, max_length=255)
    receipt_number: str | None = Field(default=None, max_length=128)
    description: str = Field(default="", max_length=1000)
    direction: Literal["expense", "income"] = "expense"
    subtotal: int | None = Field(default=None, ge=0, le=10**15)
    tax: int | None = Field(default=None, ge=0, le=10**15)
    total: int = Field(default=0, ge=0, le=10**15)
    currency: str = Field(default="IDR", max_length=8)
    payment_method: str | None = Field(default=None, max_length=64)
    notes: str = Field(default="", max_length=2000)

    @field_validator("transaction_date")
    @classmethod
    def validate_date(cls, value: str | None) -> str | None:
        if not value:
            return None
        try:
            return date.fromisoformat(value).isoformat()
        except ValueError:
            raise ValueError("Tanggal harus format YYYY-MM-DD")

    @field_validator("subtotal", "tax", mode="before")
    @classmethod
    def blank_optional_amount(cls, value):
        return _coerce_amount(value, None)

    @field_validator("total", mode="before")
    @classmethod
    def blank_total(cls, value):
        return _coerce_amount(value, 0)

    @field_validator("merchant", "receipt_number", "description", "payment_method", "currency", "notes")
    @classmethod
    def sanitize_text(cls, value):
        return _clean(value, 2000) or ""


class FeedbackRequest(StrictModel):
    scan_id: int = Field(gt=0)
    fields: ReceiptUpdate
    boxes: list[dict[str, object]] | None = Field(default=None, max_length=100)
    verified: bool = True


class ScanResponse(StrictModel):
    id: int
    status: str
    transaction_date: str | None
    merchant: str | None
    receipt_number: str | None
    description: str
    direction: str
    subtotal: int | None
    tax: int | None
    total: int
    currency: str
    payment_method: str | None
    notes: str
    created_at: str
    model_config = ConfigDict(from_attributes=True)


class MessageResponse(StrictModel):
    message: str
    scan_id: int | None = None


class TemplateColumnMapping(StrictModel):
    no: str = "A"
    date: str = "B"
    description: str = "C"
    debit: str = "F"
    credit: str = "G"
    balance: str = "H"


class TemplateMapping(StrictModel):
    template_path: str
    sheet_name: str
    first_data_row: int = Field(ge=1)
    columns: TemplateColumnMapping = Field(default_factory=TemplateColumnMapping)
    opening_balance_cell: str = "H10"
    date_style_cell: str = "B11"
    description_format: str = "{merchant} - {description}"
    opening_balance: int | None = Field(default=None, ge=0)
