"""Peningkatan kualitas gambar struk thermal sebelum dibaca OCR.

OpenCV dipakai bila tersedia (CLAHE, adaptive threshold, deskew, sharpening).
Jika belum terpasang, fallback memakai PIL agar aplikasi tetap dapat dijalankan
untuk pengujian tanpa dependensi deep learning.
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path


def load_rgb(source: bytes | bytearray | str | Path | object) -> object:
    """Muat gambar menjadi array RGB numpy."""
    import numpy as np
    from PIL import Image

    if isinstance(source, (bytes, bytearray)):
        with Image.open(BytesIO(source)) as image:
            image.load()
            return np.asarray(image.convert("RGB"))
    if isinstance(source, (str, Path)):
        with Image.open(source) as image:
            image.load()
            return np.asarray(image.convert("RGB"))
    return np.asarray(source)


def crop_region(image: object, box: object, padding: int = 2) -> object:
    """Potong area bounding box dengan padding kecil."""
    height, width = image.shape[:2]
    x1, y1, x2, y2 = (int(value) for value in box)
    x1 = max(0, min(width, x1 - padding))
    y1 = max(0, min(height, y1 - padding))
    x2 = max(0, min(width, x2 + padding))
    y2 = max(0, min(height, y2 + padding))
    if x2 <= x1 or y2 <= y1:
        return image
    return image[y1:y2, x1:x2]


def _to_gray(crop: object) -> object:
    import numpy as np

    array = np.asarray(crop)
    if array.ndim == 2:
        return array
    rgb = array[..., :3]
    try:
        import cv2

        return cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    except ImportError:
        weights = np.array([0.299, 0.587, 0.114])
        return np.dot(rgb, weights).astype("uint8")


def _deskew(gray: object) -> object:
    import cv2

    inverted = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
    coordinates = cv2.findNonZero(inverted)
    if coordinates is None or len(coordinates) < 20:
        return gray

    angle = cv2.minAreaRect(coordinates)[-1]
    if angle < -45:
        angle += 90
    if abs(angle) > 15 or abs(angle) < 0.5:
        return gray

    height, width = gray.shape[:2]
    matrix = cv2.getRotationMatrix2D((width / 2, height / 2), angle, 1.0)
    return cv2.warpAffine(gray, matrix, (width, height), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)


def _pil_enhance(gray: object) -> object:
    import numpy as np
    from PIL import Image, ImageFilter, ImageOps

    image = Image.fromarray(gray).convert("L")
    image = ImageOps.autocontrast(image, cutoff=1).filter(ImageFilter.SHARPEN)
    return np.asarray(image)


def _with_border(image: object) -> object:
    import cv2

    return cv2.copyMakeBorder(image, 8, 8, 8, 8, cv2.BORDER_CONSTANT, value=255)


def _upscale_small(gray: object, min_side: int = 48, factor: int = 2) -> object:
    import cv2

    height, width = gray.shape[:2]
    if min(height, width) >= min_side:
        return gray
    return cv2.resize(gray, None, fx=factor, fy=factor, interpolation=cv2.INTER_CUBIC)


def _adaptive_variant(gray: object) -> object:
    import cv2
    import numpy as np

    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
    binary = cv2.adaptiveThreshold(clahe, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 11)
    kernel = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]], dtype=np.float32)
    return _with_border(cv2.filter2D(binary, -1, kernel))


def _contrast_variant(gray: object) -> object:
    import cv2

    clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8)).apply(gray)
    blur = cv2.GaussianBlur(clahe, (0, 0), 1.0)
    return _with_border(cv2.addWeighted(clahe, 1.6, blur, -0.6, 0))


def _otsu_variant(gray: object) -> object:
    import cv2

    denoised = cv2.medianBlur(gray, 3)
    _, binary = cv2.threshold(denoised, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return _with_border(binary)


def enhance_variants(crop: object, *, deskew: bool = True, upscale: bool = True) -> list[tuple[str, object]]:
    """Hasilkan beberapa versi crop agar OCR dapat memilih yang paling terbaca."""
    gray = _to_gray(crop)
    if deskew:
        gray = _deskew(gray)
    if upscale:
        gray = _upscale_small(gray)

    try:
        import cv2  # noqa: F401
    except ImportError:
        return [("grayscale", _pil_enhance(gray))]

    return [
        ("adaptive", _adaptive_variant(gray)),
        ("contrast", _contrast_variant(gray)),
        ("otsu", _otsu_variant(gray)),
    ]


def enhance_for_ocr(crop: object, deskew: bool = True, upscale: bool = True) -> object:
    """Terapkan deskew, kontras adaptif, threshold, dan sharpening."""
    return enhance_variants(crop, deskew=deskew, upscale=upscale)[0][1]


def enhance_many(crops: list[object], deskew: bool = True, upscale: bool = True) -> list[object]:
    """Terapkan peningkatan kualitas pada beberapa crop sekaligus."""
    return [enhance_for_ocr(crop, deskew=deskew, upscale=upscale) for crop in crops]
