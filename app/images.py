import hashlib
from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError

from app.config import get_settings

settings = get_settings()

ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp"}


class ImageValidationError(Exception):
    pass


def normalize_image(data: bytes, declared_type: str | None) -> tuple[bytes, str, str]:
    if not data:
        raise ImageValidationError("File kosong")
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise ImageValidationError(f"Ukuran file melebihi {settings.max_upload_mb} MB")
    if declared_type and declared_type.split(";")[0].strip().lower() not in ALLOWED_TYPES:
        raise ImageValidationError("Tipe file tidak didukung")

    try:
        with Image.open(BytesIO(data)) as image:
            image.verify()
        with Image.open(BytesIO(data)) as image:
            image = ImageOps.exif_transpose(image)
            width, height = image.size
            if width <= 0 or height <= 0:
                raise ImageValidationError("Dimensi gambar tidak valid")
            if width * height > settings.max_image_pixels:
                raise ImageValidationError("Resolusi gambar terlalu tinggi")
            if max(width, height) > settings.max_image_side:
                image.thumbnail(
                    (settings.max_image_side, settings.max_image_side),
                    Image.Resampling.LANCZOS,
                )
            image = image.convert("RGB")
            buffer = BytesIO()
            image.save(buffer, format="JPEG", quality=88, optimize=True)
            normalized = buffer.getvalue()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ImageValidationError("Gambar tidak valid atau rusak") from exc

    digest = hashlib.sha256(normalized).hexdigest()
    return normalized, digest, "image/jpeg"
