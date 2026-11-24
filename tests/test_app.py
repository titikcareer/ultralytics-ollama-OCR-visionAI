from fastapi.testclient import TestClient

import app.routers.scans as scans_module


def test_login_page_sets_csrf_cookie(client: TestClient):
    response = client.get("/login")
    assert response.status_code == 200
    assert client.cookies.get("receipt_csrf")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


def test_responsive_header_footer_contract(client: TestClient):
    from app.config import BASE_DIR

    html = client.get("/login").text
    assert 'width=device-width, initial-scale=1.0' in html
    css = (BASE_DIR / "app/static/css/app.css").read_text(encoding="utf-8")
    footer = css.split(".footer {", 1)[1].split("}", 1)[0]
    assert "position: fixed" not in footer
    assert "margin-top: auto" in footer
    for rule in (
        "min-height: 100vh",
        "flex-direction: column",
        "flex: 1 0 auto",
        "justify-content: space-between",
        "flex-wrap: wrap",
        "flex-shrink: 0",
        "@media (max-width: 420px)",
        "@media (max-width: 767px)",
    ):
        assert rule in css


def test_brand_assets_are_served(client: TestClient):
    for path in ("/brand/logo.png", "/brand/watermark.png"):
        response = client.get(path)
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"
        assert response.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_status_endpoint_reports_ocr_readiness(logged_in: TestClient, monkeypatch):
    import app.main as main_module

    monkeypatch.setattr(main_module, "ocr_is_ready", lambda: False)
    response = logged_in.get("/api/status")
    assert response.status_code == 200
    assert response.json() == {"ocr_ready": False}


def test_login_page_uses_friendly_labels_and_brand(client: TestClient):
    html = client.get("/login").text
    assert 'src="/brand/logo.png"' in html
    assert 'id="clockTime"' in html
    assert "Nama pengguna" in html
    assert "Kata sandi" in html


def test_pages_use_employee_friendly_labels(logged_in: TestClient):
    html = logged_in.get("/scan").text
    assert "Pilih atau Tarik Foto/Dokumen ke Sini" in html
    assert "Mulai Baca Dokumen" in html
    assert "Langkah 1: Pilih foto dokumen yang jelas" in html
    assert "Role Aktif: <strong>Administrator</strong>" in html
    assert "Server: Terhubung" in html
    assert "Layanan OCR: Aktif" in html
    assert "Batttery" not in html
    assert "© 2026 PT GS Battery Indonesia. All rights reserved." in html


def test_root_redirects_to_login_without_session(client: TestClient):
    response = client.get("/", headers={"Accept": "text/html"}, follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/login"


def test_root_redirects_to_scan_with_session(logged_in: TestClient):
    response = logged_in.get("/", headers={"Accept": "text/html"}, follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/scan"


def test_missing_session_redirects_browser_to_login(client: TestClient):
    response = client.get("/scan", headers={"Accept": "text/html"}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    assert response.content == b""


def test_missing_session_keeps_json_401_for_api(client: TestClient):
    response = client.get("/scan", headers={"Accept": "application/json"})
    assert response.status_code == 401
    assert response.json() == {"detail": "Sesi tidak valid"}

    api_response = client.get("/api/scans/1/image", headers={"Accept": "text/html"})
    assert api_response.status_code == 401


def test_session_cookie_is_not_secure_over_http(client: TestClient, monkeypatch):
    import app.routers.auth as auth_module

    monkeypatch.setattr(auth_module.settings, "secure_cookies", True)
    client.get("/login")
    token = client.cookies.get("receipt_csrf")
    response = client.post(
        "/login",
        data={"username": "admin", "password": "TestingAdmin123!", "csrf": token},
        follow_redirects=False,
    )
    cookies = response.headers.get_list("set-cookie")
    assert all("Secure" not in cookie for cookie in cookies)


def test_login_success_and_logout(logged_in: TestClient):
    assert logged_in.get("/scan").status_code == 200
    assert logged_in.get("/history").status_code == 200
    csrf = logged_in.cookies.get("receipt_csrf")
    response = logged_in.post("/logout", headers={"X-CSRF-Token": csrf}, follow_redirects=False)
    assert response.status_code == 303
    assert logged_in.get("/scan", follow_redirects=False).status_code == 401


def test_invalid_login_is_rejected(client: TestClient):
    client.get("/login")
    csrf = client.cookies.get("receipt_csrf")
    response = client.post(
        "/login",
        data={"username": "admin", "password": "wrong-password", "csrf": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 401
    assert "receipt_session" not in client.cookies


def test_state_changing_request_requires_csrf(logged_in: TestClient):
    response = logged_in.post("/api/exports")
    assert response.status_code == 403
    assert logged_in.post("/api/exports", headers={"X-CSRF-Token": "invalid"}).status_code == 403


def test_scan_review_confirm_export_download(logged_in: TestClient, sample_image, monkeypatch):
    prediction = {
        "fields": {
            "transaction_date": "2026-11-20",
            "merchant": "Toko Satu",
            "description": "Snack rapat",
            "total": 175000,
            "direction": "expense",
        },
        "regions": [],
        "raw_text": "{}",
    }
    monkeypatch.setattr(scans_module, "run_ocr", lambda data: prediction)
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

    assert logged_in.get(f"/scans/{scan_id}/review").status_code == 200
    assert logged_in.get(f"/api/scans/{scan_id}/image").status_code == 200

    response = logged_in.post(
        f"/api/scans/{scan_id}/confirm",
        json={
            "transaction_date": "2026-11-20",
            "merchant": "Toko Satu",
            "description": "Snack rapat",
            "direction": "expense",
            "total": 175000,
        },
        headers=headers,
    )
    assert response.status_code == 200

    response = logged_in.post("/api/exports", headers=headers)
    assert response.status_code == 200
    download_url = response.json()["download_url"]

    response = logged_in.get(download_url)
    assert response.status_code == 200
    assert response.content[:2] == b"\xd0\xcf"


def test_duplicate_scan_is_rejected(logged_in: TestClient, sample_image, monkeypatch):
    monkeypatch.setattr(
        scans_module,
        "run_ocr",
        lambda data: {"fields": {"total": 50000}, "regions": [], "raw_text": "{}"},
    )
    csrf = logged_in.cookies.get("receipt_csrf")
    headers = {"X-CSRF-Token": csrf}
    files = {"image": ("struk.jpg", sample_image, "image/jpeg")}

    first = logged_in.post("/api/scans", data={"csrf": csrf}, files=files, headers=headers)
    assert first.status_code == 200
    second = logged_in.post("/api/scans", data={"csrf": csrf}, files=files, headers=headers)
    assert second.status_code == 409
    assert second.json()["scan_id"] == first.json()["scan_id"]


def test_invalid_image_is_rejected(logged_in: TestClient):
    csrf = logged_in.cookies.get("receipt_csrf")
    response = logged_in.post(
        "/api/scans",
        data={"csrf": csrf},
        files={"image": ("fake.jpg", b"not an image", "image/jpeg")},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 400


def test_temporary_password_forces_change(client: TestClient):
    from app.db import SessionLocal
    from app.models import User
    from app.security import hash_password

    with SessionLocal() as db:
        db.add(
            User(
                username="newstaff",
                full_name="New Staff",
                password_hash=hash_password("TemporaryPass123!"),
                role="user",
                must_change_password=True,
            )
        )
        db.commit()

    client.get("/login")
    csrf = client.cookies.get("receipt_csrf")
    response = client.post(
        "/login",
        data={"username": "newstaff", "password": "TemporaryPass123!", "csrf": csrf},
        follow_redirects=False,
    )
    assert response.headers["location"] == "/change-password"
    assert client.get("/scan", follow_redirects=False).status_code == 303
    assert client.get("/change-password").status_code == 200


def test_user_cannot_access_other_users_scan(client: TestClient, sample_image, monkeypatch):
    from app.db import SessionLocal
    from app.models import User
    from app.security import hash_password

    monkeypatch.setattr(
        scans_module,
        "run_ocr",
        lambda data: {"fields": {"total": 1000}, "regions": [], "raw_text": "{}"},
    )

    client.get("/login")
    csrf = client.cookies.get("receipt_csrf")
    client.post(
        "/login",
        data={"username": "admin", "password": "TestingAdmin123!", "csrf": csrf},
        follow_redirects=False,
    )
    csrf = client.cookies.get("receipt_csrf")
    response = client.post(
        "/api/scans",
        data={"csrf": csrf},
        files={"image": ("s.jpg", sample_image, "image/jpeg")},
        headers={"X-CSRF-Token": csrf},
    )
    scan_id = response.json()["scan_id"]

    with SessionLocal() as db:
        db.add(
            User(
                username="staff1",
                full_name="Staff One",
                password_hash=hash_password("StaffPassword123!"),
                role="user",
            )
        )
        db.commit()

    client.post("/logout", headers={"X-CSRF-Token": csrf}, follow_redirects=False)
    client.get("/login")
    csrf = client.cookies.get("receipt_csrf")
    client.post(
        "/login",
        data={"username": "staff1", "password": "StaffPassword123!", "csrf": csrf},
        follow_redirects=False,
    )
    assert client.get(f"/scans/{scan_id}/review").status_code == 404
    assert client.get(f"/api/scans/{scan_id}/image").status_code == 404


def _image_with_color(color: tuple[int, int, int]) -> bytes:
    from io import BytesIO

    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (320, 240), color).save(buffer, format="JPEG")
    return buffer.getvalue()


def test_multiple_photos_have_independent_results(logged_in: TestClient, monkeypatch):
    from io import BytesIO

    from PIL import Image

    def fake_ocr(data: bytes) -> dict:
        pixel = Image.open(BytesIO(data)).convert("RGB").getpixel((0, 0))
        merchant = "Toko Merah" if pixel[0] > pixel[1] else "Toko Biru"
        return {
            "fields": {"merchant": merchant, "total": sum(pixel)},
            "regions": [],
            "raw_text": "{}",
        }

    monkeypatch.setattr(scans_module, "run_ocr", fake_ocr)
    csrf = logged_in.cookies.get("receipt_csrf")
    headers = {"X-CSRF-Token": csrf}
    images = [
        ("merah.jpg", _image_with_color((220, 40, 40))),
        ("biru.jpg", _image_with_color((40, 40, 220))),
    ]

    created = []
    for filename, image in images:
        response = logged_in.post(
            "/api/scans",
            data={"csrf": csrf},
            files={"image": (filename, image, "image/jpeg")},
            headers=headers,
        )
        assert response.status_code == 200
        created.append(response.json())

    assert created[0]["scan_id"] != created[1]["scan_id"]
    assert created[0]["receipt"]["merchant"] == "Toko Merah"
    assert created[1]["receipt"]["merchant"] == "Toko Biru"

    previews = []
    for record in created:
        scan_id = record["scan_id"]
        review = logged_in.get(f"/scans/{scan_id}/review")
        assert review.headers["cache-control"] == "no-store"
        assert f"/api/scans/{scan_id}/image?v=" in review.text
        image_response = logged_in.get(f"/api/scans/{scan_id}/image")
        assert image_response.headers["cache-control"] == "no-store"
        previews.append(image_response.content)

    assert previews[0] != previews[1]


def test_scan_ids_are_not_reused_after_delete(logged_in: TestClient, monkeypatch):
    monkeypatch.setattr(
        scans_module,
        "run_ocr",
        lambda data: {"fields": {"total": 50000}, "regions": [], "raw_text": "{}"},
    )
    csrf = logged_in.cookies.get("receipt_csrf")
    headers = {"X-CSRF-Token": csrf}

    first = logged_in.post(
        "/api/scans",
        data={"csrf": csrf},
        files={"image": ("first.jpg", _image_with_color((220, 40, 40)), "image/jpeg")},
        headers=headers,
    )
    first_id = first.json()["scan_id"]
    assert logged_in.post(f"/api/scans/{first_id}/delete", headers=headers).status_code == 200

    second = logged_in.post(
        "/api/scans",
        data={"csrf": csrf},
        files={"image": ("second.jpg", _image_with_color((40, 40, 220)), "image/jpeg")},
        headers=headers,
    )
    assert second.json()["scan_id"] > first_id


def test_confirm_normalizes_blank_and_formatted_amounts(logged_in, monkeypatch):
    from app.db import SessionLocal
    from app.models import Scan

    monkeypatch.setattr(
        scans_module,
        "run_ocr",
        lambda data: {"fields": {"total": 0}, "regions": [], "raw_text": "{}"},
    )
    csrf = logged_in.cookies.get("receipt_csrf")
    headers = {"X-CSRF-Token": csrf}
    created = logged_in.post(
        "/api/scans",
        data={"csrf": csrf},
        files={"image": ("struk.jpg", _image_with_color((220, 40, 40)), "image/jpeg")},
        headers=headers,
    )
    scan_id = created.json()["scan_id"]

    response = logged_in.post(
        f"/api/scans/{scan_id}/confirm",
        json={
            "transaction_date": "",
            "merchant": "",
            "receipt_number": "",
            "description": "",
            "direction": "expense",
            "subtotal": "",
            "tax": "",
            "total": "1.750.000",
            "currency": "IDR",
            "payment_method": "",
            "notes": "",
        },
        headers=headers,
    )
    assert response.status_code == 200

    with SessionLocal() as db:
        scan = db.get(Scan, scan_id)
        assert scan.total == 1750000
        assert scan.subtotal is None
        assert scan.tax is None


def test_confirm_reports_invalid_field_in_error(logged_in, monkeypatch):
    monkeypatch.setattr(
        scans_module,
        "run_ocr",
        lambda data: {"fields": {"total": 0}, "regions": [], "raw_text": "{}"},
    )
    csrf = logged_in.cookies.get("receipt_csrf")
    headers = {"X-CSRF-Token": csrf}
    created = logged_in.post(
        "/api/scans",
        data={"csrf": csrf},
        files={"image": ("struk.jpg", _image_with_color((220, 40, 40)), "image/jpeg")},
        headers=headers,
    )
    scan_id = created.json()["scan_id"]

    response = logged_in.post(
        f"/api/scans/{scan_id}/confirm",
        json={"total": "bukan-angka"},
        headers=headers,
    )
    assert response.status_code == 422
    assert "total" in response.json()["detail"]


def test_confirm_accepts_payload_without_total(logged_in, monkeypatch):
    from app.db import SessionLocal
    from app.models import Scan

    monkeypatch.setattr(
        scans_module,
        "run_ocr",
        lambda data: {"fields": {"total": 0}, "regions": [], "raw_text": "{}"},
    )
    csrf = logged_in.cookies.get("receipt_csrf")
    headers = {"X-CSRF-Token": csrf}
    created = logged_in.post(
        "/api/scans",
        data={"csrf": csrf},
        files={"image": ("struk.jpg", _image_with_color((220, 40, 40)), "image/jpeg")},
        headers=headers,
    )
    scan_id = created.json()["scan_id"]

    response = logged_in.post(
        f"/api/scans/{scan_id}/confirm",
        json={"merchant": "Toko Satu", "subtotal": "50.000", "tax": "5.000"},
        headers=headers,
    )
    assert response.status_code == 200

    with SessionLocal() as db:
        scan = db.get(Scan, scan_id)
        assert scan.total == 0
        assert scan.subtotal == 50000
        assert scan.tax == 5000
