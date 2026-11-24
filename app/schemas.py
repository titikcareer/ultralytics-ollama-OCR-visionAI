import math
import re
from datetime import date

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


CORE_RECEIPT_FIELDS = ("transaction_date", "merchant", "description", "total", "currency", "payment_method")


def core_receipt_fields(payload: dict | None) -> dict:
    values = payload or {}
    return {name: values[name] for name in CORE_RECEIPT_FIELDS if values.get(name) not in (None, "")}


def _clean(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    cleaned = "".join(ch for ch in value if ch.isprintable() or ch in "\n\r\t").strip()
    return cleaned[:limit] or None


def _coerce_amount(value, default):
    if value is None:
        return default
    if isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            return value
        return int(value)
    if isinstance(value, str):
        text = re.sub(r"[\s\u00a0]", "", value)
        if not text:
            return default
        if re.fullmatch(r"-?\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?", text):
            normalized = text.replace(".", "").replace(",", ".")
        elif re.fullmatch(r"-?\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?", text):
            normalized = text.replace(",", "")
        elif re.fullmatch(r"-?\d+[.,]\d{1,2}", text):
            normalized = text.replace(",", ".")
        elif re.fullmatch(r"-?\d+", text):
            normalized = text
        else:
            return value
        try:
            number = float(normalized)
        except ValueError:
            return value
        return int(number) if number.is_integer() else value
    return value


_DIGIT_TRANSLATION = str.maketrans(
    {
        "O": "0",
        "o": "0",
        "Q": "0",
        "D": "0",
        "l": "1",
        "I": "1",
        "|": "1",
        "i": "1",
        "S": "5",
        "s": "5",
        "B": "8",
        "b": "8",
        "Z": "2",
        "z": "2",
    }
)
_MONTH_TRANSLATION = str.maketrans({"0": "o", "1": "l", "5": "s", "8": "b"})
_MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "mei": 5,
    "jun": 6,
    "jul": 7,
    "agu": 8,
    "ags": 8,
    "aug": 8,
    "sep": 9,
    "okt": 10,
    "oct": 10,
    "nov": 11,
    "des": 12,
    "dec": 12,
}
_DATE_CHARS = r"[0-9OoQDIl|iSsBbZz]"
_DATE_NUMERIC_RE = re.compile(rf"\b({_DATE_CHARS}{{1,4}})[/.\-]({_DATE_CHARS}{{1,2}})[/.\-]({_DATE_CHARS}{{2,4}})\b")
_DATE_TEXT_RE = re.compile(rf"\b({_DATE_CHARS}{{1,2}})[ .\-/]+([A-Za-z0-9]{{3,9}})[ .\-/]+({_DATE_CHARS}{{2,4}})\b")
_DATE_TEXT_REVERSED_RE = re.compile(
    rf"\b([A-Za-z0-9]{{3,9}})[ .\-/]+({_DATE_CHARS}{{1,2}})(?:st|nd|rd|th)?[ ,.\-]+({_DATE_CHARS}{{2,4}})\b"
)


def _digits(value: str) -> str:
    return "".join(
        char if char.isdigit() else char.translate(_DIGIT_TRANSLATION)
        for char in value
        if char.isdigit() or char.translate(_DIGIT_TRANSLATION)
    )


def _month_index(value: str) -> int | None:
    token = value.lower().translate(_MONTH_TRANSLATION)
    return _MONTHS.get(token[:3])


def _year(value: str) -> int:
    number = int(_digits(value))
    if number < 100:
        return 2000 + number if number < 70 else 1900 + number
    return number


def _build_date(year: int, month: int, day: int) -> str | None:
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def _parse_flexible_date(value: str | None) -> str | None:
    if value in (None, "", "null"):
        return None
    text = str(value).strip()
    for pattern in (_DATE_NUMERIC_RE, _DATE_TEXT_RE, _DATE_TEXT_REVERSED_RE):
        for match in pattern.finditer(text):
            groups = match.groups()
            if pattern is _DATE_NUMERIC_RE:
                first, second, third = groups
                if len(_digits(first)) == 4 or int(_digits(first)) > 31:
                    result = _build_date(int(_digits(first)), int(_digits(second)), int(_digits(third)))
                else:
                    result = _build_date(_year(third), int(_digits(second)), int(_digits(first)))
                if result:
                    return result
                continue
            if pattern is _DATE_TEXT_RE:
                day_text, month_text, year_text = groups
            else:
                month_text, day_text, year_text = groups
            month = _month_index(month_text)
            if month is None:
                continue
            result = _build_date(_year(year_text), month, int(_digits(day_text)))
            if result:
                return result
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return None


class ExtractedReceipt(StrictModel):
    transaction_date: str | None = Field(default=None, max_length=10)
    merchant: str | None = Field(default=None, max_length=255)
    description: str = Field(default="", max_length=1000)
    total: int = Field(default=0, ge=0, le=10**15)
    currency: str = Field(default="IDR", max_length=8)
    payment_method: str | None = Field(default=None, max_length=64)

    @field_validator("transaction_date", mode="before")
    @classmethod
    def validate_date(cls, value):
        if value is None:
            return None
        return _parse_flexible_date(str(value))

    @field_validator("total", mode="before")
    @classmethod
    def coerce_total(cls, value):
        return _coerce_amount(value, 0)

    @field_validator("merchant", "description", "payment_method", "currency")
    @classmethod
    def sanitize_text(cls, value):
        return _clean(value, 1000) or ""

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        return (value or "IDR").upper()[:8]


class ReceiptUpdate(StrictModel):
    transaction_date: str | None = Field(default=None, max_length=10)
    merchant: str | None = Field(default=None, max_length=255)
    description: str = Field(default="", max_length=1000)
    total: int = Field(default=0, ge=0, le=10**15)
    currency: str = Field(default="IDR", max_length=8)
    payment_method: str | None = Field(default=None, max_length=64)

    @field_validator("transaction_date")
    @classmethod
    def validate_date(cls, value: str | None) -> str | None:
        if not value:
            return None
        try:
            return date.fromisoformat(value).isoformat()
        except ValueError as exc:
            raise ValueError("Tanggal harus format YYYY-MM-DD") from exc

    @field_validator("total", mode="before")
    @classmethod
    def coerce_total(cls, value):
        return _coerce_amount(value, 0)

    @field_validator("merchant", "description", "payment_method", "currency")
    @classmethod
    def sanitize_text(cls, value):
        return _clean(value, 2000) or ""


CONFIRM_REQUIRED_FIELDS = ("transaction_date", "merchant", "total")
CONFIRM_FIELD_LABELS = {
    "transaction_date": "Tanggal Transaksi",
    "merchant": "Nama Toko",
    "total": "Total (Rp)",
}


class ConfirmationRequest(ReceiptUpdate):
    verified_fields: list[str] = Field(default_factory=list, max_length=32)

    @model_validator(mode="before")
    @classmethod
    def require_verified_fields(cls, values):
        if not isinstance(values, dict):
            return values
        missing = []
        for name in CONFIRM_REQUIRED_FIELDS:
            supplied = values.get(name)
            if supplied is None or supplied == "" or (name == "total" and supplied in (0, "0")):
                missing.append(name)
        if missing:
            raise ValueError(f"Field wajib belum diisi: {', '.join(missing)}")
        verified = values.get("verified_fields") or []
        if isinstance(verified, str):
            verified = [verified]
        unchecked = [name for name in CONFIRM_REQUIRED_FIELDS if name not in verified]
        if unchecked:
            raise ValueError(f"Field wajib belum diverifikasi: {', '.join(unchecked)}")
        values["verified_fields"] = verified
        return values


class FeedbackRequest(StrictModel):
    scan_id: int = Field(gt=0)
    fields: ReceiptUpdate
    boxes: list[dict[str, object]] | None = Field(default=None, max_length=100)
    verified: bool = False


class ScanResponse(StrictModel):
    id: int
    status: str
    transaction_date: str | None
    merchant: str | None
    description: str
    total: int
    currency: str
    payment_method: str | None
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
    last_data_row: int | None = Field(default=None, ge=1)
    report_period: str | None = Field(default=None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    columns: TemplateColumnMapping = Field(default_factory=TemplateColumnMapping)
    opening_balance_cell: str = "H10"
    date_style_cell: str = "B11"
    description_format: str = "{merchant} - {description}"
    opening_balance: int | None = Field(default=None, ge=0)
    transaction_direction: str = Field(default="expense", pattern="^expense$")

    @model_validator(mode="after")
    def validate_rows(self):
        if self.last_data_row is not None and self.last_data_row < self.first_data_row:
            raise ValueError("last_data_row tidak boleh sebelum first_data_row")
        return self
