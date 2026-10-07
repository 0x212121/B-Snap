# AGENTS.md - B-Snap

Panduan kerja untuk agent yang mengubah repository B-Snap. Detail instalasi,
operasional, dan riwayat fitur ada di dokumentasi yang ditautkan di bawah.

## Project dan sumber informasi

B-Snap adalah aplikasi self-hosted untuk capture snapshot/video IP camera,
monitoring kesehatan kamera dan recording NVR, serta notifikasi email/WhatsApp.

- Backend: FastAPI, SQLAlchemy 2.0, Alembic, APScheduler.
- Frontend: Jinja2, Tailwind CSS, JavaScript, SweetAlert2, Toast.js.
- Database utama: PostgreSQL. Jangan mengasumsikan aplikasi lengkap mendukung
  SQLite: konfigurasi engine memakai opsi PostgreSQL dan beberapa model memakai
  `JSONB`. SQLite hanya untuk tes yang memang kompatibel.
- Minimum Python package mengikuti `requires-python` di [pyproject.toml](pyproject.toml)
  (saat ini 3.11+). Runtime container mengikuti [Dockerfile](Dockerfile)
  (saat ini Python 3.14). Bedakan keduanya saat mengubah dependency.
- Versi aplikasi: gunakan `pyproject.toml`; riwayat perubahan: [CHANGELOG.md](CHANGELOG.md).
- Struktur utama: `app/routes/`, `app/api/`, `app/models/`, `app/utils/`,
  `app/jobs/`, `app/tests/`, `templates/`, `static/`, dan `alembic/`.

Periksa implementasi dan konfigurasi repository sebelum menggunakan contoh dari
panduan lama. Jangan menyalin nilai default, nomor versi, atau struktur folder
sebagai fakta tanpa memeriksa sumbernya.

## Perintah kerja

Jalankan perintah dari root repository menggunakan environment Python project.
Setup lengkap ada di [README.md](README.md) dan
[docs/source/development.md](docs/source/development.md).

```powershell
# Windows: web/all default 8080; dev default 8000.
# PORT/BIND dapat mengubah alamat; periksa start-local.ps1 dan environment.
.\start-local.ps1 check
.\start-local.ps1 dev
.\start-local.ps1 all

# Tes terarah dan pemeriksaan file yang diubah.
python -m pytest app/tests/test_scheduler_timezone.py -v
python -m ruff check path/to/changed_file.py
python -m black --check --line-length 100 path/to/changed_file.py

# Build CSS setelah mengubah class/source Tailwind.
npm run build
```

```bash
# Linux: server dan scheduler adalah proses terpisah.
make dev
make dev-worker

# Docker: gunakan nama service dari docker-compose.yml.
docker compose up -d
docker compose logs -f b-snap
docker compose exec b-snap alembic upgrade head
```

Gunakan mode `dev` untuk auto-reload web. Mode `web` dan `all` tidak otomatis
mengaktifkan reload. Verifikasi server pada `/version` menggunakan port aktif.
Untuk migration, jalankan `alembic upgrade head` pada database pengembangan atau
`docker compose exec b-snap alembic upgrade head` pada deployment yang dituju.

## Standar implementasi

- Ikuti pola modul dan fitur yang sudah ada; gunakan helper bersama sebelum
  membuat implementasi baru.
- Python: Black dengan line length 100, Ruff, type hints untuk argumen dan return,
  serta docstring Google style bila penjelasan diperlukan. Gunakan
  `from __future__ import annotations` pada modul baru sesuai kebutuhan anotasi.
- Kelompokkan import standard library, third-party, lalu aplikasi lokal.
- Konfigurasi mypy dan tool lainnya mengikuti `pyproject.toml` dan `Makefile`.
- Tambahkan model dan registrasinya pada pola model yang berlaku; perubahan schema
  harus disertai migration Alembic. Periksa chain migration sebelum menambahkan revision.
- Daftarkan router baru di `app/main.py` dan gunakan dependency autentikasi/role
  sesuai endpoint yang sejenis.
- Tambahkan entry singkat di `CHANGELOG.md` bagian `[Unreleased]` untuk setiap
  perubahan kode atau perilaku pengguna dalam change set yang sama.

## Frontend consistency

### Komponen, dialog, dan AJAX

- Gunakan komponen, Tailwind classes, dan dark-mode behavior dari halaman sejenis.
- Gunakan `themedSwal()`, `showGlobalConfirm()`, atau `showGlobalAlert()` untuk
  dialog SweetAlert2. Hindari native `alert()`/`confirm()` dan pemanggilan
  `Swal.fire()` langsung. Custom modal mengikuti pola komponen yang sudah ada.
- Gunakan `Toast.success/error/info/warning()` untuk feedback operasi non-blocking.
- Request AJAX yang mengharapkan JSON harus mengirim `Accept: application/json`,
  memeriksa status HTTP, dan menangani error response serta error jaringan.
- Escape output Jinja2 dan data yang dimasukkan ke HTML/atribut JavaScript.
  Jangan menganggap data dari API aman untuk interpolasi `innerHTML`.

### Pagination

- Ikuti pola `audit_logs.html` dan `cameras.html`: range
  `Showing x-y of N results`, indikator `Page x of y`, nomor halaman dengan
  window dua halaman, ellipsis, dan first/previous/next/last untuk beberapa halaman.
- Reuse renderer atau markup, Tailwind classes, dark-mode support, dan behavior
  yang sudah ada. Jangan mengganti pola tersebut dengan previous/next saja.

### Tampilan tanggal dan waktu

- Bahasa user mengatur format: `document.documentElement.lang`, dengan
  `en` -> `en-US` dan `id` -> `id-ID`. Hindari locale tampilan yang tetap atau
  mengikuti bahasa browser/OS.
- Konfigurasi database `timezone` mengatur clock. Pergantian bahasa tidak boleh
  mengubah timezone atau waktu kejadian.
- Reuse `static/js/date-format.js`, yang dimuat global oleh `base.html`:
  - `BSnapDates.locale()` untuk formatter `Intl` / `toLocale*` yang sudah ada.
  - `BSnapDates.format(value, options)` untuk plain text. Saat menerima ISO mentah,
    berikan `{ timeZone: configuredTimezone }` secara eksplisit.
  - `BSnapDates.html(value)` atau `data-localized-date` untuk tanggal HTML yang
    ikut berubah saat bahasa diganti; gunakan style `'date'` atau
    `data-date-style="date"` untuk date-only.
- Helper tidak otomatis mengambil timezone config untuk ISO mentah.
  `BSnapDates.html()` saat ini menerima value dan style, bukan opsi timezone.
  Gunakan timestamp backend yang sudah dikonversi, atau perluas helper bersama
  sebelum memakainya untuk ISO mentah yang perlu timezone config.
- Simpan dan bandingkan timestamp dalam UTC. API sebaiknya mengirim ISO 8601
  dengan `Z` atau offset eksplisit. Perlakukan timestamp database naive sebagai
  UTC secara eksplisit sebelum konversi.
- String dari `format_datetime_standard()` sudah berada di timezone config.
  Helper mendukung `DD/MM/YYYY - HH:mm:ss TZ`; pertahankan clock dan labelnya,
  tanpa konversi kedua atau reinterpretasi sebagai UTC.
- Gunakan clock 24 jam. Historical events, Last Run, Next Run, incident, dan logs
  menampilkan tanggal serta jam. Time-only boleh jika tanggalnya jelas di konteks.
- Letakkan label timezone pada header tanggal/laporan atau header kolom jika
  berlaku untuk seluruh bagian. Hindari pengulangan per baris jika header sudah
  jelas; pertahankan label yang sudah ada.
- Label mengikuti zone: `Asia/Jakarta` -> `WIB`, `Asia/Makassar` -> `WITA`,
  `Asia/Jayapura` -> `WIT`. Untuk zone lain, gunakan abbreviation atau nama zone
  yang benar; jangan hardcode WITA sebagai label semua timezone.
- Visible dates harus langsung diformat ulang pada `bsnap:languagechange`.
  Helper menangani marked dates; custom renderer harus menangani event tersebut.
  Dynamic/AJAX rows memakai aturan yang sama.
- Nilai mesin tetap stabil: date input, parameter API, filter, filename, dan
  chronological sorting key memakai format ISO seperti `YYYY-MM-DD`.
  Locale tetap untuk menghasilkan nilai mesin diperbolehkan, bukan label tampilan.
- Export/report yang memerlukan tanggal lokal menerima bahasa pilihan secara
  eksplisit di backend, memakai timezone config, dan mencantumkan timezone pada
  header. Jangan mengandalkan locale proses backend.
- Verifikasi EN/ID, pergantian bahasa dua arah, waktu dekat tengah malam, WIB/WITA/WIT,
  date-only yang tidak bergeser hari, dan konversi timezone tepat satu kali.

```javascript
label.textContent = BSnapDates.format(isoTimestamp, { timeZone: configuredTimezone });
const timestampCell = BSnapDates.html(backendFormattedTimestamp);
```

```html
<span data-localized-date="2026-10-07" data-date-style="date"></span>
```

## Konfigurasi, scheduler, dan notifikasi

- Environment settings memakai loader terkait; `get_config()` di
  `app/core/config.py` membaca database `Configuration`, kemudian default.
  Jangan mengasumsikan environment selalu mengalahkan nilai database untuk semua key.
- Default database settings ada di `app/core/config_initializer.py`. Seed hanya
  key yang belum ada; jangan menimpa pengaturan user.
- Jadwal per job terpusat di `app/core/job_schedules.py`. Cron override tiap job
  berdiri sendiri; nilai kosong mengembalikan default/legacy schedule yang berlaku.
- Saat menambah job: daftarkan fungsi/wrapper di scheduler, schedule di
  `JOB_SCHEDULES`, serta nama/deskripsi di `app/routes/jobs.py`. Pastikan job dapat
  dilihat dan diatur melalui `/admin/jobs`.
- Semua cron/interval trigger dan report date header memakai timezone config.
  Perubahan schedule/timezone harus berlaku pada config reload. Scheduler worker
  saat ini melakukan polling setiap 60 detik melalui `scheduler_main.py`.
- Verifikasi startup, edit schedule, reload, reset ke default, timezone, dan bahwa
  perubahan satu job tidak mengubah jadwal job lain. Jangan menjalankan scheduler
  riil yang dapat mengirim notifikasi/capture kamera dalam tes terisolasi.
- Alert incident memakai waktu deteksi: selalu teruskan `snapshot.timestamp`
  sebagai `incident_time` ke `send_tamper_alert()` dari snapshot detection.
  Default fungsi adalah UTC now; retry harus mempertahankan waktu incident asli.
- Pertahankan deduplikasi, consecutive-confirmation, cooldown, rate limiting,
  dan circuit breaker. Gunakan sumber konfigurasi/konstanta di kode; jangan
  menduplikasi angka threshold dalam panduan. Cooldown tamper dipasang setelah
  pemeriksaan deduplikasi dan sebelum pengiriman; jangan mengubah urutan tanpa
  memeriksa dampak terhadap flooding dan retry.

## Invariant keamanan

- Jangan commit `.env`, token, password, atau encryption key. Jangan memasukkan
  secret ke log, response, contoh dokumentasi, atau artefak tes.
- Validasi input dan pertahankan autentikasi, role checks, CSRF protection, serta
  group restrictions pada endpoint baru maupun endpoint yang diubah.
- Akses grup kamera memakai relasi `Camera.groups` / association table, seperti
  `Camera.groups.any(CameraGroup.id == group_id)`. Jangan memakai nama grup atau
  legacy `Camera.group_id` sebagai satu-satunya pembatas akses.
- `group_id` user yang NULL berarti tanpa pembatas grup; fitur online-user detail
  tetap admin-only. Ikuti helper/dependency akses yang berlaku.
- File snapshot/video harus dilayani melalui endpoint authenticated. Pertahankan
  blokir akses langsung `/static/snapshots/*` dan `/static/videos/*`, pemeriksaan
  group access, serta audit access/download.
- Pertahankan SHA-256 evidence integrity dan append-only audit trail. Pencatatan
  thumbnail/gallery memakai pola minimal/batch yang ada agar audit tidak flooding.
- Delete footage menggunakan soft delete. Retention hold harus dihormati oleh
  cleanup/purge; restore dan purge mengikuti pemeriksaan akses yang ada.
- Password kamera memakai helper enkripsi yang ada; jangan menyimpan plaintext
  baru atau melewati mekanisme enkripsi pada import/update.
- Safety classification dan GPS coordinates harus memakai validasi model/schema
  yang berlaku. Token expiry, session expiry, dan Remember Me mengikuti auth code.

## Pengujian dan verifikasi

- Tes berada di `app/tests/`; periksa file/marker yang benar sebelum memilih suite.
  Jangan mengasumsikan ada subfolder `unit/` atau `integration/`.
- Jalankan tes terarah sesuai perubahan, lalu pemeriksaan lint/format yang relevan.
  Pilih database test atau mock yang terisolasi dari data aplikasi.
- Fixture umum `app/tests/conftest.py` membuat schema SQLite. Model PostgreSQL
  seperti `JSONB` dapat membuat setup gagal; bedakan kegagalan fixture dari
  kegagalan kode. Tes database PostgreSQL memerlukan database test yang sesuai.
- Tes terisolasi yang memock DB tidak memerlukan schema lengkap. Dokumentasikan
  fixture override lokal jika dipakai; jangan mengklaim seluruh suite lulus
  berdasarkan tes terarah saja.
- Jalankan `npm run build` bila diperlukan untuk menghasilkan CSS, dan periksa
  JavaScript/template serta tampilan light/dark pada perubahan frontend.
- Laporkan perubahan, hasil verifikasi, dan keterbatasan material secara jelas.

## Dokumentasi lanjutan

- [README.md](README.md): overview dan setup.
- [Development](docs/source/development.md): workflow pengembangan.
- [Deployment](docs/source/deployment.md): deployment dan layanan.
- [Configuration](docs/source/configuration.md): environment/configuration.
- [Security](docs/source/security.md): fitur dan mekanisme keamanan.
- [Audit logging](docs/source/audit_logging.md): audit trail.
- [Notifications](docs/source/notifications.md): email dan notifikasi.
- [Record mounts](docs/source/record-mounts.md): akses recording SMB/NFS.
- [CHANGELOG.md](CHANGELOG.md): riwayat rilis dan perbaikan.
