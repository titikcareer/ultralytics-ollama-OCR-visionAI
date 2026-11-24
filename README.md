# Receipt Recap

Sistem internal untuk merekap data struk dan payment slip menjadi file Excel secara otomatis. Karyawan cukup memindai dokumen menggunakan kamera ponsel; aplikasi mengekstrak field penting, memvalidasi hasil melalui layar tinjauan, lalu menuliskannya ke template Excel yang digunakan tim finance.

Aplikasi ini dibangun sebagai layanan web FastAPI dengan pipeline Computer Vision mandiri: **YOLOv8** mendeteksi area penting struk dan **PaddleOCR/EasyOCR** membaca teks pada area tersebut. Seluruh gambar dan data finansial diproses di infrastruktur internal tanpa LLM eksternal, Ollama, atau layanan OCR pihak ketiga.

## Daftar Isi

- [Gambaran Umum](#gambaran-umum)
- [Arsitektur](#arsitektur)
- [Struktur Proyek](#struktur-proyek)
- [Prasyarat](#prasyarat)
- [Instalasi dan Menjalankan Aplikasi](#instalasi-dan-menjalankan-aplikasi)
- [Konfigurasi Lingkungan](#konfigurasi-lingkungan)
- [Panduan Penggunaan](#panduan-penggunaan)
- [Pemetaan Template Excel](#pemetaan-template-excel)
- [Aspek Keamanan](#aspek-keamanan)
- [Implementasi untuk Kebutuhan Industri](#implementasi-untuk-kebutuhan-industri)
- [Endpoint HTTP](#endpoint-http)
- [Pengujian dan Kualitas](#pengujian-dan-kualitas)
- [Pemecahan Masalah](#pemecahan-masalah)
- [Catatan Repositori](#catatan-repositori)

## Gambaran Umum

Proses bisnis yang diotomatisasi:

1. Karyawan membuka aplikasi di ponsel melalui browser dan login dengan akun karyawan.
2. Karyawan memotret struk atau payment slip menggunakan kamera belakang, atau memilih gambar dari galeri.
3. Gambar divalidasi, dibersihkan dari metadata EXIF, dikompresi, lalu dikirim ke server.
4. YOLOv8 mendeteksi ROI (`struk_belanja`, `info_toko`, `item_belanja`, `total`). Crop diurutkan sesuai urutan baca, duplikasi box dibuang, lalu setiap crop dipertajam dalam beberapa variasi (deskew, upscale teks kecil, CLAHE, adaptive threshold, Otsu). OCR lokal membaca semua variasi; teks dengan keyakinan tertinggi dan urutan baris yang benar dipilih, lalu diubah menjadi JSON terstruktur: tanggal, vendor, nomor dokumen, keterangan, subtotal, pajak, total, mata uang, dan metode pembayaran.
5. Hasil ekstraksi ditampilkan pada halaman tinjauan. Karyawan wajib memeriksa dan dapat mengoreksi setiap field sebelum menyimpan.
6. Setiap konfirmasi/koreksi otomatis masuk ke `ocr-ml/data/feedback_loop` beserta gambar, label YOLO, identitas karyawan, dan status verifikasinya.
7. Setelah terkumpul 25 feedback terverifikasi (atau lewat tombol Admin), pipeline melakukan fine-tuning, mengevaluasi mAP50 pada data valid, dan hanya mempromosikan model jika akurasi tidak menurun.
8. Data yang dikonfirmasi diekspor ke template Excel milik perusahaan pada sheet dan kolom yang sudah dipetakan, lengkap dengan nomor urut, tanggal, keterangan, serta perhitungan saldo berjalan.
9. File Excel dapat diunduh melalui tautan yang hanya dapat diakses oleh pemilik data atau administrator.

Pendekatan *human-in-the-loop* dipilih secara sengaja. OCR dapat salah membaca angka pada struk yang buram, sehingga validasi manusia menjadi kontrol sekaligus sumber data latih baru.

## Arsitektur

| Lapisan | Teknologi | Fungsi |
| --- | --- | --- |
| Antarmuka | Jinja2, CSS dan JavaScript vanilla | Halaman login, pemindaian kamera, tinjauan data, riwayat, dan administrasi |
| API | FastAPI | Endpoint HTTP, validasi permintaan, otorisasi, dan kontrol CSRF |
| Deteksi | Ultralytics YOLOv8 (`ocr-ml/models/production/best.pt`) | Deteksi ROI area penting struk |
| OCR | PaddleOCR (default) atau EasyOCR | Pembacaan teks lokal pada crop tanpa jaringan |
| ML Ops | Cookiecutter Data Science di `ocr-ml/` | Dataset, preprocessing, training, evaluasi, registry, feedback loop, retraining |
| Penyimpanan | SQLite melalui SQLAlchemy 2.0 | Pengguna, sesi, data scan, file ekspor, dan log audit |
| Excel | `xlrd` + `xlutils`/`xlwt` untuk `.xls`, `openpyxl` untuk `.xlsx` | Penulisan data ke template tanpa mengubah format aslinya |
| Keamanan | Argon2id, sesi server-side, CSRF, RBAC | Autentikasi, otorisasi, dan jejak aktivitas |

Alur data antar komponen:

```text
[Ponsel / Browser]
        |  HTTPS, multipart upload + CSRF token
        v
[FastAPI] --> validasi gambar --> [YOLOv8: ROI] --> [enhance] --> [PaddleOCR] --> JSON
        |                                                                        |
        v                                                                        v
[SQLite: scan draft] --> [Halaman tinjauan] --> [SQLite: scan confirmed] --> [feedback_loop/]
                                                       |                          |
                                                       v                          v
                                     [Template Excel] --> [File ekspor]     [retrain_pipeline]
                                                                                 |
                                                                                 v
                                                      [evaluasi mAP50] --> [promosi best.pt + hot-reload]
```

Karakteristik pipeline mandiri:

- Tidak ada halusinasi LLM; OCR hanya membaca karakter yang benar-benar ada pada gambar.
- Berjalan pada CPU/GPU standar server pabrik dan dapat dipasang di jaringan terisolasi.
- Guardrail mAP50 mencegah model baru menggantikan model produksi bila akurasinya turun.
- Struktur Cookiecutter Data Science memisahkan data mentah, data antara, data olahan, model, dan pipeline.

Karakteristik desain yang relevan untuk produksi:

- OCR berjalan di dalam jaringan internal; tidak ada panggilan API eksternal untuk gambar.
- File gambar disimpan di luar direktori publik dengan nama acak yang dibuat server.
- Setiap aksi penting dicatat ke tabel audit beserta pengguna, alamat IP, dan waktu.
- Template Excel tidak diagregasi dari data mentah, melainkan ditulis langsung ke layout yang sudah disetujui finance.

## Struktur Proyek

```text
.
├── app/
│   ├── __main__.py            Entry point `python -m app`
│   ├── main.py                Inisialisasi aplikasi, bootstrap admin, security headers
│   ├── config.py              Skema Settings dari environment dan file .env
│   ├── db.py                  Engine SQLAlchemy dan manajemen sesi database
│   ├── models.py              Model User, Session, Scan, Export, AuditLog
│   ├── schemas.py             Model Pydantic untuk OCR dan payload API
│   ├── security.py            Hash password, sesi, CSRF, rate limiter, audit
│   ├── deps.py                Dependency FastAPI untuk autentikasi dan otorisasi
│   ├── images.py              Validasi, normalisasi, dan penghapusan EXIF gambar
│   ├── ocr.py                 Jembatan ke pipeline OCR lokal (YOLO + OCR)
│   ├── ml.py                  Loader paket ocr_ml dan akses model/feedback/retraining
│   ├── excel.py               Exporter ke template .xls/.xlsx
│   ├── render.py              Helper rendering template Jinja2
│   ├── routers/
│   │   ├── auth.py            Login, logout, ganti password
│   │   ├── scans.py           Scan, tinjauan, konfirmasi, feedback, ekspor, unduh
│   │   └── admin.py           Manajemen pengguna, log audit, dan endpoint /retrain
│   ├── templates/             Halaman HTML
│   └── static/                CSS dan JavaScript
├── config/
│   └── template_mapping.json  Pemetaan kolom template Excel
├── ocr-ml/                    Pipeline ML mandiri (Cookiecutter Data Science)
│   ├── data/
│   │   ├── raw/               Dataset Roboflow asli (train/valid/test + _annotations.csv)
│   │   ├── interim/           CSV hasil konversi YOLO dan salinan data.yaml
│   │   ├── processed/         Dataset siap latih: images/ + labels/ per split
│   │   └── feedback_loop/     Gambar, label, dan metadata koreksi karyawan
│   ├── models/
│   │   ├── baseline/          Model hasil training awal
│   │   ├── production/        best.pt aktif yang dipakai aplikasi web
│   │   └── registry.json      Versi, tanggal, dan skor mAP tiap model
│   ├── notebooks/             EDA dan evaluasi error
│   ├── src/
│   │   ├── data/              make_dataset.py dan feedback_sync.py
│   │   ├── features/          preprocess.py (deskew, upscale, CLAHE, adaptive/Otsu)
│   │   ├── models/            train_model.py, predict_model.py, evaluate_model.py
│   │   └── pipelines/         retrain_pipeline.py
│   ├── configs/               data.yaml dan classes.txt (dibuat otomatis)
│   └── requirements.txt       Dependensi YOLO + OCR lokal
├── templates/                 File template Excel perusahaan
├── tests/                     Pengujian otomatis
├── data/                      Runtime: database, gambar, dan file ekspor (gitignored)
├── .env.example               Contoh konfigurasi lingkungan
├── .env                       Konfigurasi lokal (gitignored)
├── Dockerfile                 Image container aplikasi
├── requirements.txt           Dependensi Python
└── ruff.toml                  Konfigurasi lint
```

## Prasyarat

- Python 3.12 atau lebih baru.
- Dependensi ML dari `ocr-ml/requirements.txt` (Ultralytics, OpenCV, NumPy, PaddleOCR).
- Dataset struk Roboflow di `ocr-ml/data/{train,valid,test}` dan bobot model hasil training awal.
- Tidak memerlukan Ollama, API key, atau koneksi internet saat inferensi (unduhan bobot pretrained hanya saat training pertama).
- Browser modern dengan dukungan `getUserMedia` pada ponsel.
- HTTPS untuk akses dari ponsel. Browser hanya mengizinkan kamera pada origin aman (`https://` atau `localhost`).
- File template Excel perusahaan yang ditempatkan di direktori `templates/`.

## Instalasi dan Menjalankan Aplikasi

### 1. Siapkan virtual environment dan dependensi

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt -r ocr-ml/requirements.txt
```

### 2. Buat file konfigurasi lokal

Buat file `.env` di root proyek. File ini tidak boleh masuk ke repository Git.

```dotenv
ENVIRONMENT=development
SECRET_KEY=ganti-dengan-kunci-acak-minimal-32-karakter
ADMIN_USERNAME=admin
ADMIN_PASSWORD=AdminPass2026!Secure
BASE_URL=http://localhost:8000
SECURE_COOKIES=false
DATABASE_URL=sqlite:///./data/app.db

# ML_ROOT=/path/absolut/ke/ocr-ml
OCR_ENGINE=paddleocr
OCR_CONFIDENCE=0.25
OCR_IOU=0.45
OCR_IMGSZ=640
OCR_MIN_CONFIDENCE=0.35
OCR_VARIANTS=true
FEEDBACK_THRESHOLD=25
RETRAIN_EPOCHS=20

MAX_UPLOAD_MB=10
MAX_IMAGE_PIXELS=20000000
MAX_IMAGE_SIDE=4096
SESSION_TTL_MINUTES=480
LOGIN_MAX_ATTEMPTS=5
LOGIN_LOCKOUT_MINUTES=15
```

Ketentuan penting:

- `SECRET_KEY` minimal 32 karakter. Gunakan generator nilai acak, misalnya `python -c "import secrets; print(secrets.token_hex(32))"`.
- `ADMIN_PASSWORD` minimal 12 karakter dan wajib memuat huruf besar, huruf kecil, angka, dan simbol. Aplikasi menolak startup jika ketentuan ini tidak terpenuhi.
- `SECURE_COOKIES=false` hanya untuk pengembangan lokal di `http://localhost`. Atribut `Secure` hanya ditambahkan jika permintaan memakai HTTPS; dengan demikian cookie tetap terkirim lewat HTTP termasuk akses melalui IP jaringan lokal. Pada produksi wajib `true` dan aplikasi harus diakses melalui HTTPS.

### 3. Siapkan dataset dan latih model awal

Dataset Roboflow sudah berada di `ocr-ml/data/{train,valid,test}`. Konversi anotasi CSV menjadi label YOLO dan konfigurasi `data.yaml`:

```bash
python ocr-ml/src/data/make_dataset.py
```

Kelas yang terdeteksi otomatis: `struk_belanja`, `info_toko`, `item_belanja`, `total`.

Latih model pertama kali (mengunduh `yolov8n.pt` saat eksekusi pertama), lalu salin ke folder produksi:

```bash
python ocr-ml/src/models/train_model.py --output baseline --epochs 50
cp ocr-ml/models/baseline/best.pt ocr-ml/models/production/best.pt
```

Alternatif tanpa menyalin manual: `python ocr-ml/src/models/train_model.py --output production --epochs 50`. Status "Layanan OCR" pada antarmuka akan aktif setelah `ocr-ml/models/production/best.pt` tersedia.

### 4. Jalankan aplikasi

```bash
python -m app
```

Aplikasi tersedia di `http://localhost:8000`. Saat startup, aplikasi membuat direktori `data/`, skema database, dan akun administrator awal berdasarkan `ADMIN_USERNAME` serta `ADMIN_PASSWORD`.

Untuk mengubah alamat atau port:

```bash
HOST=0.0.0.0 PORT=8000 python -m app
```

### 5. Menjalankan dengan Docker

```bash
docker build -t receipt-recap .
docker run --rm \
  --env-file .env \
  -e HOST=0.0.0.0 \
  -p 8000:8000 \
  -v receipt-recap-data:/srv/app/data \
  -v receipt-recap-ml:/srv/app/ocr-ml \
  receipt-recap
```

Volume `receipt-recap-data` menyimpan database, gambar, dan file ekspor. Volume `receipt-recap-ml` menyimpan dataset, feedback loop, registry, dan bobot model agar tidak hilang saat container diganti. Isi volume ML dari host sebelum menjalankan container.

## Konfigurasi Lingkungan

Seluruh konfigurasi dibaca oleh `app/config.py` melalui `pydantic-settings` dengan urutan prioritas: variabel lingkungan sistem, kemudian file `.env`, lalu nilai default pada kode.

| Variabel | Default | Keterangan |
| --- | --- | --- |
| `SECRET_KEY` | wajib | Kunci untuk penandatanganan token CSRF. Minimal 32 karakter. |
| `ADMIN_PASSWORD` | wajib | Password administrator awal. Minimal 12 karakter dan kompleks. |
| `ADMIN_USERNAME` | `admin` | Username administrator awal. |
| `ENVIRONMENT` | `development` | Nilai `production` mengaktifkan header HSTS. |
| `BASE_URL` | `http://localhost:8000` | Alamat publik aplikasi. |
| `SECURE_COOKIES` | `true` | Mengizinkan atribut `Secure` pada cookie; atribut ini hanya dipakai jika skema permintaan `https`. |
| `DATABASE_URL` | `sqlite:///./data/app.db` | Lokasi database. |
| `ML_ROOT` | `BASE_DIR/ocr-ml` | Path absolut root pipeline ML berisi data, model, dan feedback loop. |
| `OCR_ENGINE` | `paddleocr` | Mesin OCR lokal: `paddleocr`, `easyocr`, atau `none`. |
| `OCR_CONFIDENCE` | `0.25` | Ambang kepercayaan minimum deteksi YOLO. |
| `OCR_IOU` | `0.45` | Ambang IoU untuk non-max suppression pada deteksi YOLO. |
| `OCR_IMGSZ` | `640` | Ukuran input gambar untuk YOLO. |
| `OCR_MIN_CONFIDENCE` | `0.35` | Baris OCR dengan keyakinan di bawah nilai ini dibuang. |
| `OCR_VARIANTS` | `true` | Coba beberapa variasi kontras/threshold lalu pilih hasil OCR terbaik. |
| `FEEDBACK_THRESHOLD` | `25` | Jumlah feedback terverifikasi sebelum retraining otomatis. |
| `RETRAIN_EPOCHS` | `20` | Jumlah epoch saat fine-tuning dari feedback loop. |
| `MAX_UPLOAD_MB` | `10` | Ukuran maksimum gambar yang diunggah. |
| `MAX_IMAGE_PIXELS` | `20000000` | Batas total piksel gambar. |
| `MAX_IMAGE_SIDE` | `4096` | Sisi terbesar gambar setelah normalisasi. |
| `SESSION_TTL_MINUTES` | `480` | Masa berlaku sesi login. |
| `LOGIN_MAX_ATTEMPTS` | `5` | Jumlah kegagalan login sebelum akun dikunci sementara. |
| `LOGIN_LOCKOUT_MINUTES` | `15` | Durasi penguncian setelah kegagalan berulang. |

## Panduan Penggunaan

### Administrator

1. Login menggunakan kredensial awal dari konfigurasi.
2. Buka menu **Admin** untuk membuat akun karyawan. Sistem menghasilkan password sementara yang harus diserahkan kepada karyawan.
3. Password sementara mewajibkan perubahan pada login pertama sebelum halaman lain dapat diakses.
4. Menu Admin juga menampilkan daftar pengguna, pengaktifan atau penonaktifan akun, dan 100 aktivitas terakhir.

### Karyawan

1. Buka aplikasi di ponsel dan login.
2. Pilih **Scan**, lalu aktifkan kamera dan ambil foto struk. Pastikan seluruh struk terlihat dan cukup terang. Opsi unggah dari galeri tersedia jika kamera tidak dapat digunakan.
3. Tekan **Proses struk** dan tunggu hasil ekstraksi.
4. Periksa data pada halaman **Periksa Data Struk**. Koreksi field yang salah, tentukan arah transaksi (kredit untuk pengeluaran, debet untuk penerimaan), lalu simpan.
5. Data tersimpan dengan status *draft* sebelum dikonfirmasi dan *confirmed* setelah disimpan.
6. Buka **Riwayat** untuk melihat data yang sudah dikonfirmasi, lalu tekan **Buat Excel**.
7. Unduh file yang dihasilkan melalui tautan yang muncul.

Sistem menolak unggahan gambar yang sama persis dari pengguna yang sama dan menampilkan pesan agar hasil sebelumnya dibuka melalui menu Riwayat.

## Pemetaan Template Excel

Pemetaan disimpan di `config/template_mapping.json`. Konfigurasi bawaan ditujukan untuk template `CashOpname-November-2026-Templates.xls`:

```json
{
  "template_path": "templates/CashOpname-November-2026-Templates.xls",
  "sheet_name": "Cash on Hand Harian Nov 2026",
  "first_data_row": 11,
  "columns": {
    "no": "A",
    "date": "B",
    "description": "C",
    "debit": "F",
    "credit": "G",
    "balance": "H"
  },
  "opening_balance_cell": "H10",
  "date_style_cell": "B11",
  "description_format": "{merchant} - {description}",
  "opening_balance": 2638400
}
```

| Kunci | Fungsi |
| --- | --- |
| `template_path` | Path template relatif terhadap root proyek. Mendukung `.xls` dan `.xlsx`. |
| `sheet_name` | Nama sheet tujuan. Harus persis sama dengan sheet pada template. |
| `first_data_row` | Nomor baris pertama tempat data ditulis. Baris header dan judul tidak diubah. |
| `columns` | Pemetaan field aplikasi ke kolom Excel. |
| `opening_balance_cell` | Sel saldo awal. Dibaca otomatis jika `opening_balance` tidak diisi. |
| `date_style_cell` | Sel referensi untuk format tanggal agar tampilan tetap konsisten. |
| `description_format` | Format keterangan gabungan vendor dan deskripsi. |
| `opening_balance` | Nilai saldo awal eksplisit. |

Logika penulisan:

- Transaksi berarah `expense` ditulis ke kolom **KREDIT**, saldo berjalan dikurangi.
- Transaksi berarah `income` ditulis ke kolom **DEBET**, saldo berjalan ditambah.
- Nomor urut dimulai dari 1 untuk setiap proses ekspor.
- Tanggal ditulis sebagai nilai tanggal Excel dengan format yang diambil dari sel referensi.
- Teks yang diawali karakter formula seperti `=` atau `+` diberi awalan `'` agar tidak dieksekusi sebagai rumus.

Perhatikan bahwa konfigurasi di atas menulis mulai baris 11, sehingga baris contoh yang sudah ada pada template akan ditimpa. Untuk penggunaan produksi, siapkan salinan template dengan area data kosong pada rentang baris yang dituju, lalu perbarui `template_path`, `sheet_name`, `first_data_row`, dan `columns` sesuai format finance. Perubahan template sebaiknya melalui proses tinjauan finance dan dicatat sebagai perubahan konfigurasi.

## Aspek Keamanan

- **Autentikasi:** password di-hash dengan Argon2id. Password sementara wajib diganti pada login pertama.
- **Sesi:** token sesi disimpan sebagai hash di database, bersifat dapat dicabut, ber-`HttpOnly`, dan memiliki masa berlaku. Cookie hanya dikirim pada permintaan same-site.
- **CSRF:** seluruh permintaan yang mengubah state memerlukan token CSRF yang cocok dengan sesi. Token juga divalidasi pada halaman login.
- **Otorisasi:** karyawan hanya dapat mengakses data scan dan file ekspor miliknya. Administrator dapat mengakses seluruh data serta mengelola akun.
- **Audit trail:** login, kegagalan login, pembuatan dan konfirmasi scan, penghapusan, ekspor, unduhan, perubahan password, dan perubahan akun dicatat.
- **Validasi unggahan:** tipe MIME dan tanda tangan gambar diperiksa, ukuran dan resolusi dibatasi, metadata EXIF dihapus, lalu gambar ditulis ulang sebagai JPEG.
- **Isolasi berkas:** nama file gambar dan ekspor dibuat server, disimpan di luar direktori publik, serta diverifikasi agar tidak keluar dari direktori yang diizinkan.
- **OCR aman:** inferensi berjalan sepenuhnya lokal di dalam proses aplikasi; tidak ada permintaan jaringan keluar ke penyedia OCR/LLM. Gambar diproses sebagai data, bukan instruksi, dan prompt injection tidak mungkin terjadi karena tidak ada model bahasa yang menerima gambar.
- **Header browser:** `Content-Security-Policy`, `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, dan HSTS pada lingkungan produksi.
- **Proteksi Excel:** nilai yang berpotensi menjadi rumus dinetralisasi sebelum ditulis.
- **Pembatasan login:** percobaan login gagal berulang menyebabkan penguncian sementara per kombinasi alamat IP dan username.

Pembatas login saat ini disimpan dalam memori proses. Untuk deployment dengan beberapa replica, gunakan rate limiter terpusat seperti Redis agar batasan berlaku lintas instance.

## Implementasi untuk Kebutuhan Industri

### Topologi yang disarankan

```text
[Ponsel karyawan]
        | HTTPS melalui VPN / jaringan internal
        v
[Reverse proxy: TLS terakhir, WAF, pembatasan laju]
        |
        v
[App node: FastAPI + YOLO + OCR lokal]
        |
        v
[Volume data: SQLite, gambar, ekspor]
        |
        +--> [Volume ocr-ml: dataset, feedback_loop, registry, best.pt]
        |
        v
[Backup terenkripsi + SIEM/log audit]

Untuk kapasitas besar, inferensi dapat dipindahkan ke worker GPU terpisah yang menjalankan modul yang sama, sementara FastAPI tetap menjadi lapisan autentikasi dan validasi. Tidak ada dependency jaringan ke layanan OCR eksternal.
```

### Langkah implementasi

1. **Infrastruktur dan akses.** Tempatkan aplikasi hanya di jaringan internal atau di belakang VPN. Terminasi TLS dilakukan di reverse proxy. Gunakan sertifikat internal yang valid agar kamera pada ponsel tetap dapat diakses.

2. **Penyediaan model.** Jalankan `make_dataset.py`, latih YOLOv8 awal, lalu simpan `best.pt` ke `ocr-ml/models/production/`. Kunci versi model di `ocr-ml/models/registry.json`, uji akurasi deteksi/OCR terhadap sampel struk perusahaan sebelum rollout, dan gunakan varian YOLO yang lebih besar bila akurasi belum memadai.

3. **Manajemen identitas.** Untuk skala besar, integrasikan autentikasi dengan LDAP atau SSO perusahaan. Jika tetap menggunakan akun lokal, terapkan rotasi `SECRET_KEY`, password administrator yang dikelola secrets manager, dan peninjauan akun secara berkala. Nonaktifkan akun karyawan yang tidak aktif melalui menu Admin.

4. **Tata kelola template.** Finance menandatangani template dan pemetaan kolom. Simpan template pada path yang dikelola, simpan perubahan mapping di version control, dan lakukan uji ekspor sebelum perubahan diterapkan ke produksi.

5. **Prosedur operasi.** Tetapkan SOP: struk dipindai pada hari transaksi, hasil tinjauan diselesaikan sebelum batas waktu harian, dan ekspor diunduh oleh PIC yang bertanggung jawab. Tetapkan juga aturan siapa yang dapat mengubah data setelah dikonfirmasi.

6. **Observability.** Kirim log akses dan log audit ke sistem logging terpusat. Pantau metrik: jumlah scan per hari, persentase scan yang gagal OCR, latensi OCR, jumlah data draft yang belum dikonfirmasi, dan kegagalan ekspor. Gunakan identitas pengguna pada log agar setiap perubahan dapat ditelusur.

7. **Kapasitas dan ketersediaan.** Uji throughput dengan jumlah scan harian puncak. Jika satu node tidak mencukupi, jalankan beberapa replica aplikasi dengan volume data dan `ocr-ml` bersama, lalu pindahkan pembatas login ke Redis. Untuk beban tinggi, pisahkan worker ML ke node GPU dengan versi model yang sama.

8. **Backup dan pemulihan.** Cadangkan database, direktori `data/uploads`, `data/exports`, dan seluruh direktori `ocr-ml` (terutama `feedback_loop`, `registry.json`, dan `models/production/best.pt`) secara berkala. Simpan cadangan terenkripsi dan uji prosedur pemulihan. Tentukan retensi data struk sesuai kebijakan finansial dan aturan privasi perusahaan.

9. **Hardening lanjutan.** Jalankan container sebagai pengguna non-root (sudah diterapkan pada Dockerfile), batasi resource CPU dan memori, terapkan network policy agar aplikasi hanya dapat menghubungi layanan logging dan penyimpanan internal, serta lakukan pemindaian dependensi dan image secara berkala.

### Poin yang perlu diputuskan sebelum produksi

- Format Excel final dan lokasi area data pada template.
- Kebijakan retensi gambar struk dan file ekspor.
- Apakah data harus terhubung langsung ke ERP, atau tetap melalui unduhan Excel manual.
- SLA waktu pemrosesan dan kapasitas CPU/GPU untuk deteksi YOLO serta OCR.
- Frekuensi retraining dan siapa yang boleh memicu tombol Admin **Latih Ulang Model Sekarang**.
- Integrasi identitas korporat dan sistem logging perusahaan.

## Endpoint HTTP

| Metode | Path | Fungsi |
| --- | --- | --- |
| GET | `/login` | Halaman login dan penerbitan token CSRF awal |
| POST | `/login` | Autentikasi dan pembuatan sesi |
| POST | `/logout` | Pencabutan sesi |
| GET, POST | `/change-password` | Perubahan password |
| GET | `/api/status` | Status layanan dan ketersediaan model ML |
| GET | `/scan` | Halaman pemindaian |
| POST | `/scan` | Alias unggah gambar dan ekstraksi OCR |
| POST | `/api/scans` | Unggah gambar dan ekstraksi OCR |
| GET | `/api/scans/{id}/image` | Gambar struk milik pengguna |
| GET | `/scans/{id}/review` | Halaman tinjauan hasil ekstraksi |
| POST | `/api/scans/{id}/confirm` | Konfirmasi data dan penyimpanan feedback karyawan |
| POST | `/feedback` | Kirim koreksi manual (butuh CSRF) untuk feedback loop |
| POST | `/retrain` | Admin: memicu fine-tuning model sekarang (butuh CSRF) |
| POST | `/api/scans/{id}/delete` | Penghapusan data dan gambar |
| GET | `/history` | Riwayat pemindaian |
| POST | `/api/exports` | Pembuatan file Excel |
| GET | `/downloads/{filename}` | Unduhan file Excel yang berhak diakses |
| GET | `/admin` | Halaman administrasi |
| POST | `/admin/users` | Pembuatan akun karyawan |
| POST | `/admin/users/{id}/status` | Pengaktifan atau penonaktifan akun |

Dokumentasi OpenAPI sengaja dinonaktifkan pada aplikasi produksi.

## Pengujian dan Kualitas

```bash
python -m pytest -q
python -m ruff check app tests ocr-ml/src
python ocr-ml/src/data/make_dataset.py
```

Pengujian mencakup alur login dan logout, penerapan CSRF, alur scan hingga ekspor dan unduhan, penolakan gambar rusak, pencegahan duplikasi, pembatasan akses antar pengguna, kebijakan password administrator, validasi gambar, penulisan data ke template Excel, konversi dataset YOLO, sinkronisasi feedback, parsing field OCR, dan endpoint retraining. Dependensi `ocr-ml/requirements.txt` tidak diperlukan untuk menjalankan test karena training/inferensi nyata dimock.

## Pemecahan Masalah

| Gejala | Penyebab dan penanganan |
| --- | --- |
| Kamera tidak aktif di ponsel | Akses kamera memerlukan HTTPS atau `localhost`. Periksa izin browser dan sertifikat TLS. |
| `ValidationError` pada `admin_password` | Nilai `ADMIN_PASSWORD` di `.env` kurang dari 12 karakter atau belum memenuhi kombinasi huruf besar, huruf kecil, angka, dan simbol. Perbaiki nilai lalu jalankan ulang aplikasi. |
| `Gagal terhubung ke layanan OCR lokal` | Tidak ada layanan jaringan yang dipakai. Pastikan `ocr-ml/requirements.txt` terpasang dan `ocr-ml/models/production/best.pt` tersedia. |
| `ModuleNotFoundError: No module named 'ultralytics'` atau modul OCR lain | Jalankan `python -m pip install -r requirements.txt -r ocr-ml/requirements.txt`. |
| `Model OCR belum tersedia` | Latih model awal lalu letakkan `best.pt` di `ocr-ml/models/production/`, atau ubah konfigurasi guardrail/registri. |
| `Template tidak ditemukan` | Periksa `template_path` di `config/template_mapping.json` dan pastikan file ada di direktori `templates/`. |
| `Gagal membuat file Excel` | Periksa nama sheet pada `sheet_name`, nomor `first_data_row`, dan izin tulis pada direktori `data/exports`. |
| Hasil ekstraksi angka tidak akurat | Gunakan gambar dengan pencahayaan cukup, koreksi field pada halaman tinjauan agar masuk feedback loop, lalu latih ulang dengan lebih banyak data. |
| `Proses latih ulang gagal. Periksa log model.` | Periksa `ocr-ml/models/retrain.log`. Penyebab umum: dependensi ML belum terpasang, dataset valid kosong, atau tidak ada cukup feedback terverifikasi. |

## Catatan Repositori

File `main.py` di root adalah aplikasi Gradio untuk pengenalan plat nomor dari proyek sebelumnya dan tidak digunakan oleh sistem ini. Aplikasi rekap struk dijalankan melalui paket `app/` dengan perintah `python -m app`.

Repositori masih menyimpan nama `ultralytics-ollama-OCR-rebuild` karena alasan historis; tidak ada lagi ketergantungan runtime terhadap Ollama. Pipeline ML berada di `ocr-ml/` dan diimpor oleh aplikasi melalui alias `ocr_ml` (`ocr-ml/src/bootstrap.py`) karena direktori aslinya mengandung tanda hubung.

Lisensi proyek mengikuti berkas `LICENSE` yang tersedia di repository.
