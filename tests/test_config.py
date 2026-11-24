import pytest
from pydantic import ValidationError

from app.config import BASE_DIR, Settings


def make_settings(**overrides) -> Settings:
    values = {
        "secret_key": "x" * 32,
        "admin_password": "CompliantTestPass123!",
    }
    values.update(overrides)
    return Settings(**values)


def test_admin_password_minimum_length_is_enforced():
    with pytest.raises(ValidationError, match="minimal 12 karakter"):
        make_settings(admin_password="Admin123!")


@pytest.mark.parametrize(
    "password, message",
    [
        ("alllowercase1!", "huruf besar"),
        ("ALLUPPERCASE1!", "huruf kecil"),
        ("NoDigitsHere!!", "angka"),
        ("NoSymbolHere123", "simbol"),
    ],
)
def test_admin_password_complexity_is_enforced(password, message):
    with pytest.raises(ValidationError, match=message):
        make_settings(admin_password=password)


def test_compliant_admin_password_is_accepted():
    settings = make_settings()
    assert settings.admin_password == "CompliantTestPass123!"


def test_env_template_password_satisfies_settings_contract():
    text = (BASE_DIR / ".env.example").read_text(encoding="utf-8")
    password = next(line.split("=", 1)[1].strip() for line in text.splitlines() if line.startswith("ADMIN_PASSWORD="))
    assert make_settings(admin_password=password).admin_password == password


def test_ocr_accuracy_options_are_validated():
    settings = make_settings(ocr_min_confidence=0.8, ocr_variants="false")
    assert settings.ocr_min_confidence == 0.8
    assert settings.ocr_variants is False

    with pytest.raises(ValidationError):
        make_settings(ocr_min_confidence=1.5)


def test_env_file_is_gitignored():
    rules = (BASE_DIR / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".env" in [rule.strip() for rule in rules]
