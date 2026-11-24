"""Register ``ocr-ml/src`` as the importable ``ocr_ml`` package.

The project directory uses a hyphen (``ocr-ml``), so it cannot be imported with a
normal ``import`` statement. This helper builds a package alias in ``sys.modules``
without renaming the Cookiecutter Data Science folder layout.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent


def ensure_ocr_ml_package() -> None:
    """Make ``ocr_ml.*`` modules importable from any working directory."""
    if "ocr_ml" in sys.modules:
        return

    spec = importlib.util.spec_from_file_location(
        "ocr_ml",
        SRC_DIR / "__init__.py",
        submodule_search_locations=[str(SRC_DIR)],
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Tidak dapat memuat paket ocr_ml dari {SRC_DIR}")

    module = importlib.util.module_from_spec(spec)
    sys.modules["ocr_ml"] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop("ocr_ml", None)
        raise
