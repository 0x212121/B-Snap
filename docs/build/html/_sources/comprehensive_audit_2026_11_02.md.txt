# Comprehensive audit B-Snap — 2 November 2026

Audit mengikuti `COMPREHENSIVE_AUDIT.MD` dan `AGENTS.md`. Analisis, reproduksi,
remediasi, dan verifikasi dilakukan pada working tree lokal; tidak ada deployment,
migration ke database aplikasi, capture kamera, atau pengiriman notifikasi nyata.

**Pembaruan 2.6.1:** seluruh temuan High yang tersisa (CA-19, CA-20, CA-27,
CA-28) telah diperbaiki pada source dan diverifikasi secara terisolasi. Hasil
rilis awal tetap dicatat sebagai riwayat. Bagian 12 mencatat perbaikan WebSocket
2.6.2; bagian 13 mencatat penghentian inbox CA-22 pada 2.6.3, dan bagian 14
mencatat perbaikan timezone monitor CA-24 pada 2.6.4.
Bagian 15 mencatat optimasi dan benchmark CA-21 pada 2.6.5.
Bagian 16 mencatat konsolidasi routing/review legacy CA-23 pada 2.6.6.

## 1. Arsitektur dan cakupan

- Baseline paket/aplikasi **2.5.2**, hasil akhir **2.6.6**. Minimum Python **3.11**, default image Docker
  **Python 3.14.7**; verifikasi lokal memakai Python **3.13** dan Node **22.16.0**.
- FastAPI menyediakan halaman Jinja2 dan API; SQLAlchemy 2 dan Alembic memakai
  PostgreSQL. Model JSONB dan trigger audit membuat schema penuh tidak kompatibel
  dengan fixture SQLite umum.
- Web, scheduler APScheduler, notifier PostgreSQL, dan proses migration merupakan
  layanan terpisah. Scheduled capture, health checks, record checks, retry email,
  WhatsApp schedules, dan arsip audit dijalankan melalui worker/helper terkait.
- Frontend memakai Tailwind 3, design-system CSS, JavaScript, shared date formatter,
  Toast, SweetAlert bertema, serta komponen peta lokal.
- Jalur request: error/observability middleware → proxy headers → gzip → CSRF → signed
  session → online tracking → auth/setup → router/dependency role → SQLAlchemy →
  JSON/template/file/stream. WebSocket memerlukan penanganan autentikasi tersendiri.
- Kamera memakai association table `camera_camera_groups`; snapshot/video memakai
  soft delete, SHA-256, dan retention hold. Akses footage publik harus ditolak pada
  file server, sedangkan endpoint authenticated memeriksa grup dan mencatat audit.

[Inventaris sumber dan endpoint](comprehensive_audit_2026_11_02_inventory.json)
mencatat file, hash, syntax/compile check, serta dependency yang dideklarasikan.
Inventaris hasil 2.6.6 mencakup **292 file Python, 55 template, 15 JavaScript,
6 harness/test CJS, dan 261 deklarasi endpoint produksi**. Jumlah endpoint mencakup router legacy yang
belum tentu didaftarkan; tidak sama dengan jumlah URL unik yang aktif.

Semua file pada inventaris menerima pemeriksaan statis. Penelusuran mendalam dan
tes dilakukan pada boundary autentikasi, proxy, media, retensi, notifikasi, API
error, scheduler, bot, report, peta, dan UI yang memiliki tes terisolasi. Ini bukan
klaim bahwa setiap cabang business logic atau setiap halaman sudah diuji runtime.

## 2. Daftar prioritas sebelum implementasi

Daftar CA-01 sampai CA-12 dibuat sebelum perubahan kode. CA-13 dan seterusnya
ditambahkan ketika pemeriksaan lanjutan menemukan bukti tambahan.

| Urutan | Finding | Urgensi/impact | Effort dan risiko regresi | Hasil |
| --- | --- | --- | --- | --- |
| 1 | CA-01 | Critical: akses file bukti langsung | Kecil; menjaga asset publik | Diperbaiki |
| 2 | CA-02–06, CA-13–14, CA-18 | High: MFA, permission, hold, proxy, notifikasi | Sedang; auth, bot, dan media saling bergantung | Diperbaiki + regresi |
| 3 | CA-07–11, CA-17, CA-25 | Medium: kontrak error, sesi, waktu, UI | Kecil–sedang; backward compatibility dijaga | Diperbaiki |
| 4 | CA-12, CA-15–16, CA-26 | Test/dependency/release consistency | Pembaruan kompatibel; tanpa deploy | Diperbaiki sebagian/seluruhnya sesuai rincian |
| 5 | CA-19–20, CA-27–28 | High: CSRF, produksi, webhook, health group | Middleware, migration, dan kontrak integrasi | Diperbaiki pada 2.6.1; lihat bagian 11 |
| 6 | CA-22 | Inbox persisten tidak digunakan pada jalur standar | Hapus fitur dan arsipkan data legacy | Dihapus pada 2.6.3 |
| 7 | CA-24 | Timezone monitor pesan bot | API timezone metadata, formatter, dan filter UTC | Diperbaiki pada 2.6.4 |
| 8 | CA-21 | Query counts per grup | Satu query agregasi dan benchmark PostgreSQL | Diperbaiki pada 2.6.5 |
| 9 | CA-23 | Routing dokumentasi dan review legacy | Canonical routes, alias, deprecation inventory | Diselesaikan pada 2.6.6 |
| Berikutnya | Sisa CA-12/15/17 | UI/debt, test/dependency | Memerlukan desain/validasi tambahan | Backlog non-High |

## 3. Findings: Backend

### CA-02 — Enrollment MFA membocorkan secret akun yang sudah aktif

- **Layer / Severity / Status:** Backend / High / confirmed issue, diperbaiki.
- **Location:** `app/routes/auth.py`, `mfa_setup_form`, `mfa_setup_post`.
- **Issue / Evidence:** token tahap password dapat digunakan kembali pada
  `/mfa/setup`; GET sebelumnya membuat provisioning QR tanpa memeriksa
  `is_2fa_enabled`. POST juga tidak menolak akun yang sudah enrolled.
- **Root Cause:** eligibility enrollment berbeda dari alur login OTP.
- **Impact:** pemegang password dapat mengambil secret MFA yang sudah aktif.
- **Recommended Fix / Implementasi:** tolak GET/POST enrollment ulang sebelum
  rendering QR, validasi OTP, commit, atau pembuatan sesi.
- **Regression Risk:** onboarding MFA baru; tetap diverifikasi lewat tes positif.
- **Verification:** tes enabled-MFA GET/POST dan enrollment awal pada
  `test_audit_security.py`; tidak menggunakan user/database aplikasi.

### CA-04 — Mutasi media dan purge mengabaikan grup/retention hold

- **Layer / Severity / Status:** Backend / High / confirmed issue, diperbaiki.
- **Location:** `app/routes/snap_gallery.py`, `app/routes/videos.py`,
  `app/utils/snapshot_service.py`, `app/utils/media_access.py`.
- **Issue / Evidence:** restore/verify/hold/delete tertentu hanya memeriksa role;
  single hard delete tidak memeriksa hold, dan bulk purge menerima
  `skip_retention_hold=false`. Role video hard delete dibaca dari session lama.
- **Root Cause:** kebijakan akses tersebar antara handler dan service.
- **Impact:** perubahan bukti lintas grup, hilangnya bukti yang masih ditahan,
  perbedaan perilaku browser versus Bearer API.
- **Recommended Fix / Implementasi:** gunakan current database user dan helper
  grup sebelum efek samping; filter trash/bulk purge; hard delete mensyaratkan
  soft delete dan tanpa hold. Flag override ditolak, service tetap menjaga hold.
  Restore/purge snapshot individual mendapat audit record.
- **Regression Risk:** client yang sengaja memakai override hold kini menerima
  409 dan harus melepas hold melalui alur eksplisit terlebih dahulu.
- **Verification:** real query pada tabel SQLite yang kompatibel; akses denied,
  orphan, stale legacy group, hold, bulk purge, serta restore yang diizinkan diuji.

### CA-05 — Dependency role tidak merata pada management/integration routes

- **Layer / Severity / Status:** Backend / High / confirmed issue, diperbaiki.
- **Location:** recipient, whitelist, telemetry log, WhatsApp lookup, toast demo,
  reload-config, dan ping routers.
- **Issue / Evidence:** beberapa handler hanya memiliki dependency DB; middleware
  juga meneruskan Bearer header ke route tanpa memvalidasinya sendiri.
- **Root Cause:** asumsi bahwa middleware atau pembatasan tombol sudah cukup.
- **Impact:** pemanggil tanpa role yang sesuai dapat membaca/mengubah data,
  mengirim demo notifikasi, atau menjalankan ping.
- **Recommended Fix / Implementasi:** dependency admin pada management/integration
  routers dan reload; ping membutuhkan operator. Tidak mengandalkan UI/session role.
- **Regression Risk:** integrasi lama harus memakai token admin yang valid untuk
  endpoint administratif; format endpoint/payload tetap.
- **Verification:** request anonim/viewer/operator ditolak sebelum query, commit,
  transport email, atau jaringan ping; tes SMTP admin yang sudah ada tetap dijalankan.

### CA-08 — Malformed web token menghasilkan exception

- **Layer / Severity / Status:** Backend / Medium / confirmed issue, diperbaiki.
- **Location:** `app/utils/auth_token.py`, `get_current_user`, optional auth.
- **Issue / Evidence:** helper mengindeks field token/expiry tanpa validasi shape;
  expiry naive dibandingkan dengan datetime aware. Optional auth memakai field
  legacy `User.token` dan hanya memeriksa user ID untuk sesi browser.
- **Root Cause:** beberapa implementasi validasi token yang berbeda.
- **Impact:** error autentikasi, ketidakkonsistenan revocation/expiry, sesi parsial
  dianggap authenticated oleh endpoint snapshot lama.
- **Recommended Fix / Implementasi:** helper bersama menerima JSON/list yang
  valid, menolak shape/expiry rusak, dan menafsirkan expiry naive lama sebagai UTC;
  optional auth memakai validasi yang sama.
- **Regression Risk:** token legacy tanpa expiry valid ditolak, bukan dipulihkan.
- **Verification:** malformed JSON, null, tipe salah, expiry rusak/kedaluwarsa,
  serialisasi JSON, legacy UTC, Bearer, dan sesi tanpa token diuji.

### CA-11 — Duplikasi handler tamper membuat filter tidak terjangkau

- **Layer / Severity / Status:** Backend / Medium / confirmed issue, diperbaiki.
- **Location:** `app/routes/snap_gallery.py`, `/snapshots/tampered`.
- **Issue / Evidence:** dua GET pada path yang sama; handler pertama mengembalikan
  seluruh tampered snapshot tanpa group/deletion/range filter.
- **Root Cause:** implementasi rentang ditambahkan sebagai route kedua.
- **Impact:** filter diabaikan dan metadata footage lintas grup/trash terbaca.
- **Recommended Fix / Implementasi:** satu handler, response model yang sebelumnya
  aktif dipertahankan, filter opsional berpasangan, end-day inklusif dalam timezone
  config lalu dibandingkan dalam UTC.
- **Regression Risk:** client yang memasang filter invalid kini mendapat 400.
- **Verification:** satu deklarasi route, grup/orphan/deletion, tanggal invalid,
  dan event UTC yang berada pada tanggal lokal berikutnya diuji.

### CA-13 — Menghapus kamera menghapus video evidence

- **Layer / Severity / Status:** Backend / High / confirmed issue, diperbaiki.
- **Location:** `Camera.videos`, `app/routes/cameras.py::delete_camera`.
- **Issue / Evidence:** relationship `delete-orphan` dan loop `os.remove()`
  menghapus video kamera, termasuk video yang memiliki hold.
- **Root Cause:** lifecycle video berbeda dari snapshot dan FK `ON DELETE SET NULL`.
- **Impact:** kehilangan permanen video/hash/hold ketika konfigurasi kamera dihapus.
- **Recommended Fix / Implementasi:** pertahankan record/file video dan gunakan
  FK SET NULL; hapus cascade delete ORM. Tidak mengubah schema atau FK database.
- **Regression Risk:** video orphan kini tetap memakai storage dan harus mengikuti
  kebijakan retention/purge evidence yang sudah tersedia.
- **Verification:** tes ORM/HTTP handler dengan FK SQLite aktif memastikan kamera
  hilang, lima video tetap ada, camera_id menjadi NULL, hash/hold tetap, serta tidak
  ada penghapusan file; PostgreSQL runtime tetap perlu verifikasi staging.

### CA-14 — Legacy group column masih mengizinkan akses

- **Layer / Severity / Status:** Backend / High / confirmed issue, diperbaiki.
- **Location:** resolver kamera dan `WABotHandler` lookup/access helpers.
- **Issue / Evidence:** kondisi `Camera.group_id == group OR groups.any(...)`
  menerima primary group lama walaupun membership aktual berbeda.
- **Root Cause:** fallback otorisasi tertinggal setelah migrasi many-to-many.
- **Impact:** pembatasan whitelist/current user bisa dilewati oleh data legacy stale.
- **Recommended Fix / Implementasi:** gunakan association relationship untuk
  keputusan akses; group NULL tetap berarti tanpa pembatasan.
- **Regression Risk:** data legacy yang belum dimigrasikan perlu memperbaiki
  membership, bukan diberi akses melalui scalar fallback.
- **Verification:** fixture resolver menambahkan kamera dengan scalar allowed tetapi
  relasi denied; tes WhatsApp access dan media menguji kondisi yang sama.

### CA-21 — Query counts camera groups bertumbuh per grup

- **Layer / Severity / Status:** Backend / Low / improvement opportunity,
  diperbaiki dan dibenchmark pada 2.6.5.
- **Location / Evidence sebelum perbaikan:** loop `get_camera_groups` pada
  `app/routes/camera_groups.py` melakukan query count kamera/NVR/user per grup,
  sehingga query data bertambah sebagai 1 + 3G.
- **Issue / Root Cause:** agregasi per item, bukan grouped aggregation.
- **Impact:** pada PostgreSQL lokal, 250 grup memerlukan 751 SELECT dan median
  674,469 ms. Latency deployment pengguna belum diukur.
- **Implemented Fix:** agregasi kamera/association, NVR dan pengguna dalam subquery
  terpisah, kemudian LEFT JOIN/COALESCE ke daftar grup. Satu SELECT untuk setiap
  jumlah grup; urutan, field, total dan admin dependency tetap sama.
- **Regression Risk:** join multiplication dicegah oleh agregasi terpisah;
  shared camera dihitung per membership, legacy scalar tidak menjadi sumber
  count. Kasus kosong memiliki overhead tetap sekitar 2 ms lebih tinggi dalam
  benchmark ini, bukan klaim semua request selalu lebih cepat.
- **Verification:** tujuh regresi mencakup ukuran 0/1/10/100, grup kosong,
  shared camera, stale primary group, orphan association, NULL group user,
  count/total/order dan role checks. Benchmark before/after memakai dataset yang
  sama; body SHA-256 identik untuk semua ukuran. Pada 250 grup, median menjadi
  15,282 ms dan satu SELECT; lihat bagian 15 dan laporan benchmark.

## 4. Findings: Middleware / integration

### CA-06 — Header proxy menimpa trust policy

- **Layer / Severity / Status:** Middleware / High / confirmed issue, diperbaiki.
- **Location:** middleware stack `app/main.py`.
- **Issue / Evidence:** setelah `ProxyHeadersMiddleware`, dua middleware tambahan
  langsung menulis client/scheme dari header tanpa memeriksa trusted peer.
- **Root Cause:** pemrosesan forwarded header ganda.
- **Impact:** spoofing IP audit dan scheme walaupun proxy trust dikonfigurasi ketat.
- **Recommended Fix / Implementasi:** gunakan standard proxy middleware yang sudah
  ada; keluarkan kedua override dari stack.
- **Regression Risk:** deployment yang hanya mengirim X-Real-IP harus menyediakan
  X-Forwarded-For/Proto dari proxy tepercaya.
- **Verification:** peer trusted/untrusted dan header X-Real-IP diuji terhadap
  konfigurasi stack; default wildcard selanjutnya dihapus pada perbaikan CA-20.

### CA-07 — Respons error kehilangan format/status/header

- **Layer / Severity / Status:** Middleware / Medium / confirmed issue, diperbaiki.
- **Location:** error wrapper dan exception handlers pada `app/main.py`.
- **Issue / Evidence:** wrapper mengubah status 5xx menjadi 500 HTML; exception
  catch juga mengembalikan HTML untuk API; HTTPException headers tidak disalin.
- **Root Cause:** lapisan error memakai klasifikasi request dan format berbeda.
- **Impact:** AJAX gagal parse, 503/Retry-After dan Bearer challenge hilang.
- **Recommended Fix / Implementasi:** satu klasifikasi JSON, generic JSON untuk
  unhandled API error, status/header dipertahankan, redundant error middleware
  dihapus. HTML error development di-escape.
- **Regression Risk:** browser tetap menerima halaman error; live-stream errors
  tetap diteruskan. Auth middleware mempertahankan kebijakan 503-nya sendiri.
- **Verification:** isolated ASGI stack memakai handler/wrapper aktual: 500 JSON,
  pesan generic, 503 browser/API, Retry-After, WWW-Authenticate, serta regresi
  API error dan live preview yang sudah ada.

### CA-10 — Session cookie berbeda dari cookie autentikasi

- **Layer / Severity / Status:** Middleware / Medium / confirmed issue, diperbaiki.
- **Location:** `SessionMiddleware` pada `app/main.py`.
- **Issue / Evidence:** secure=false, umur 86400, dan SameSite lax di-hardcode,
  sementara auth menyediakan konfigurasi terkait.
- **Root Cause:** konfigurasi cookie diduplikasi.
- **Impact:** session cookie tidak mengikuti kebijakan deployment yang dipilih.
- **Recommended Fix / Implementasi:** reuse setting auth untuk ketiga opsi.
- **Regression Risk:** cookie secure memerlukan scheme/proxy yang benar.
- **Verification:** konfigurasi middleware dibandingkan dengan konfigurasi auth;
  tes session/Remember Me failure dan rotation dijalankan.

### CA-18 — WebSocket anonim dan toast tidak memeriksa penerima

- **Layer / Severity / Status:** Middleware / High / confirmed issue, diperbaiki.
- **Location:** `app/ws/routes.py`, `app/ws/record_notifications.py`,
  `NotificationService._broadcast`.
- **Issue / Evidence:** `/ws` sebelumnya langsung accept; middleware HTTP tidak
  mengautentikasi WebSocket. Direct toast broadcast mengirim ke semua socket,
  sedangkan queued ordinary events hanya difilter untuk recording private.
- **Root Cause:** autentikasi HTTP dan recording-specific filter dianggap mencakup
  seluruh delivery channel.
- **Impact:** event dapat diterima tanpa sesi, oleh penerima lain atau grup lain.
- **Recommended Fix / Implementasi:** validasi sesi sebelum accept, periksa browser
  Origin, dan gunakan shared delivery filter pada jalur direct/queued. Setiap
  ordinary event memvalidasi token live, user target, dan camera membership;
  aturan private recording yang sudah ada tetap dipertahankan.
- **Regression Risk:** client WebSocket memerlukan browser session/cookie valid;
  tambahan query per delivery memerlukan pengukuran fan-out pada staging.
- **Verification:** handshake anonim/valid/cross-origin, revoked session, target
  user berbeda, dan group-denied notifications diuji tanpa layanan notifier nyata.

### CA-19 — Proteksi CSRF belum merata

- **Layer / Severity / Status:** Middleware / High / potential risk, diperbaiki pada source 2.6.1.
- **Location / Evidence sebelum perbaikan:** guard Origin/non-simple request terdapat pada record
  mount actions, tetapi tidak ada mekanisme CSRF global untuk seluruh form; logout
  masih GET. Cookie SameSite lax merupakan mitigasi yang sudah ada.
- **Issue / Root Cause:** perlindungan diterapkan per fitur, belum menjadi kontrak
  lintas aplikasi. Audit ini tidak mereproduksi serangan pada deployment nyata.
- **Impact:** residual risk pada request berbasis cookie, khususnya topology
  same-site berbeda origin dan state-changing navigation.
- **Implemented Fix:** `CSRFMiddleware` memeriksa Origin/Referer/Fetch Metadata
  pada seluruh unsafe method sebelum autentikasi/DB. Bearer tanpa cookie browser
  dikecualikan; POST webhook memakai HMAC tersendiri. GET logout hanya konfirmasi,
  logout aktual dan seluruh tombol logout memakai POST. Signed media GET tetap
  mengikuti pemeriksaan akses yang ada.
- **Regression Risk:** klien cookie tanpa provenance ditolak 403; proxy yang
  mengubah Host memerlukan `PUBLIC_ORIGIN` canonical. GET MFA tetap hanya menyiapkan
  secret akun yang belum aktif dengan token tahap-login valid; tidak mengaktifkan MFA.
- **Verification:** matriks ASGI origin invalid/duplicate, sibling-origin,
  same-origin, proxy-host rewrite, Bearer dengan/tanpa cookie, GET/POST logout;
  Chrome nyata menerima form/AJAX same-origin dan menolak form cross-site serta
  same-site beda port. Penerimaan pada proxy deployment tetap perlu diverifikasi.

### CA-20 — Default development/proxy/secret perlu kebijakan deployment

- **Layer / Severity / Status:** Middleware / High / potential risk, diperbaiki pada source 2.6.1.
- **Location / Evidence sebelum perbaikan:** `app/main.py` menyediakan development/default secret dan
  proxy trust wildcard; pemakaian default hanya diperingatkan. Audit tidak membaca
  atau menyimpulkan nilai secret/policy production pengguna.
- **Issue / Root Cause:** default pengembangan dapat dipakai deployment yang belum
  lengkap konfigurasinya; belum ada fail-fast policy produksi terpusat.
- **Impact:** keamanan session, debug output, dan keandalan IP audit bergantung pada
  deployment; tidak membuktikan bahwa production saat ini salah konfigurasi.
- **Implemented Fix:** `app/core/security.py` menjadi sumber bersama session,
  auth token, dan Remember Me. Production menolak secret pendek/default, Secure
  cookie nonaktif, SameSite invalid, proxy wildcard `/0`/invalid, dan HTTPS proxy
  tanpa IP/CIDR eksplisit. Default proxy trust development hanya loopback.
  Counter password/OTP persisten memakai upsert atomik PostgreSQL dan mengembalikan
  429 + Retry-After; kegagalan penyimpanan counter gagal tertutup dengan 503.
- **Regression Risk:** konfigurasi production lama yang tidak aman akan gagal
  startup; jalankan migration dan ikuti `high_security_rollout.md`. Nilai secret
  environment deployment tidak diperiksa, ditulis ulang, atau dicatat oleh audit.
- **Verification:** matriks konfigurasi valid/invalid, representasi tanpa secret,
  route password/OTP/enrollment dan reset window; 20 percobaan concurrent melalui
  session PostgreSQL terpisah menghasilkan tepat 10 diterima dan 10 ditolak.

## 5. Findings: Frontend

### CA-09 — Log viewer memasukkan metadata/error ke HTML

- **Layer / Severity / Status:** Frontend / Medium / confirmed issue, diperbaiki.
- **Location:** `templates/logs.html`, renderer dan fetch helpers.
- **Issue / Evidence:** message sudah di-escape, tetapi logger/level/pid dan error
  JSON/network masih diinterpolasi langsung ke innerHTML. Request tidak konsisten
  mengirim Accept atau memeriksa HTTP status.
- **Root Cause:** metadata/API error dianggap aman untuk HTML.
- **Impact:** HTML injection pada field yang dirender dan error AJAX membingungkan.
- **Recommended Fix / Implementasi:** escape seluruh data dinamis, encode URL path,
  dan gunakan fetch helper dengan Accept JSON serta HTTP/network failure handling.
- **Regression Risk:** markup di log kini terlihat sebagai teks, sesuai tujuan viewer.
- **Verification:** Node harness dan Chrome memakai payload `<img>` pada metadata
  serta error; tidak ada elemen/payload yang dieksekusi atau page error.

### CA-17 — Log viewer terpotong di mobile dan badge tidak diproses

- **Layer / Severity / Status:** Frontend / Medium / confirmed issue, diperbaiki
  untuk Logs Viewer; pola CSS sejenis pada halaman lain masih backlog.
- **Location / Evidence:** sidebar w-72 dan main flex row tanpa min-width constraint;
  screenshot 390px memperlihatkan kontrol di luar viewport. CSS badge memakai
  Tailwind `@apply` pada style inline yang tidak melewati build.
- **Root Cause:** layout desktop dipakai di semua viewport; template CSS dianggap
  ikut diproses oleh CLI Tailwind.
- **Impact:** kontrol sulit dijangkau dan warna badge tidak konsisten.
- **Recommended Fix / Implementasi:** stack sidebar pada layar kecil, bounded scroll,
  min-width/min-height untuk viewer, dan reuse ui-badge variants.
- **Regression Risk:** sidebar/file list memiliki scroll tersendiri pada mobile.
- **Verification:** build CSS dan screenshot Chrome 1440×1000/390×844, light/dark;
  kontrol tersedia dan tidak ada horizontal overflow halaman.
- **Remaining:** raw `@apply` ditemukan juga pada delapan template lain. Pindahkan
  ke stylesheet build atau utility classes setelah memeriksa benturan selector
  generik seperti `.status-badge`/`.role-badge`; jangan memindahkannya global secara
  otomatis. Verifikasi visual masing-masing halaman diperlukan.

### CA-24 — Monitor bot masih mengikuti clock browser untuk tanggal tertentu

- **Layer / Severity / Status:** Frontend / Medium / confirmed source inconsistency,
  diperbaiki pada source 2.6.4; deployment pengguna belum diverifikasi.
- **Location / Evidence sebelum perbaikan:** `static/js/wa-bot-builder.js` memakai
  `new Date(item.timestamp).toLocaleString(BSnapDates.locale())` tanpa timeZone;
  detail juga menampilkan timestamp mentah.
- **Issue / Root Cause:** locale sudah mengikuti bahasa, tetapi clock belum selalu
  memakai timezone database.
- **Impact:** browser di timezone lain dapat menampilkan clock berbeda.
- **Implemented Fix:** API mengirim timezone/label dan ISO dengan offset eksplisit,
  termasuk timestamp naive yang diperlakukan UTC. Tabel, Details, dan refresh
  memakai BSnapDates.format dengan timezone konfigurasi, header WIB/WITA/WIT atau
  nama IANA yang benar, serta re-render EN/ID langsung. Filter datetime-local
  dikonversi melalui helper bersama parseLocal menjadi instant UTC; rentang
  reversed/nonexistent ditolak, fold DST memakai bound earlier/later inklusif.
- **Regression Risk:** reinterpretasi ISO naive atau filter mesin dapat menggeser
  rentang waktu bila diperbaiki hanya pada label.
- **Verification:** API isolated, Node parser/format tests, Chrome EN–ID–EN pada
  browser Los Angeles/Jakarta dengan WIB/WITA/WIT/London, midnight, filter dan
  Details terbuka. Light/dark/mobile diperiksa; hasil rinci pada bagian 14.

### CA-25 — Format log baru tidak dikenali dan waktu file ambigu

- **Layer / Severity / Status:** Cross-Layer / Medium / confirmed issue, diperbaiki.
- **Location:** `LogEntry`, file metadata API, `BSnapDates`, log renderer.
- **Issue / Evidence:** formatter logger menghasilkan ISO dengan offset, parser
  hanya menerima format lama dengan spasi; modified time dikirim naive dan date
  markup belum menyimpan timezone eksplisit untuk language refresh.
- **Root Cause:** perubahan formatter belum diteruskan ke parser/API/UI.
- **Impact:** timestamp log hilang atau clock mengikuti browser/host.
- **Recommended Fix / Implementasi:** parser mendukung format baru/lama sebagai
  UTC; file metadata aware UTC; halaman menyertakan timezone config. Shared
  `html(value, style, {timeZone})` menyimpan `data-date-timezone` untuk refresh.
- **Regression Risk:** format lama naive diasumsikan UTC sesuai UTCFormatter;
  timestamp backend yang sudah dilokalkan tetap dipertahankan tanpa konversi kedua.
- **Verification:** parser tiga format; Node EN–ID–EN, midnight, WIB/WITA/WIT,
  date-only, clock lokal backend. Chrome memakai browser America/Los_Angeles
  tetapi log Asia/Makassar tetap 00:30 pada tanggal berikutnya.

### CA-26 — Template legacy tidak dapat dikompilasi

- **Layer / Severity / Status:** Frontend / Low / confirmed issue, diperbaiki.
- **Location / Evidence:** `templates/user_management2.html` mempunyai ekspresi
  Jinja rusak di komentar JavaScript dan dua block scripts dengan nama sama;
  compile test sebelumnya mengecualikan template ini.
- **Root Cause:** view legacy tidak tercakup compile gate.
- **Impact:** template gagal bila dipakai kembali; tidak ada bukti route aktif
  saat ini merendernya.
- **Recommended Fix / Implementasi:** perbaiki komentar, satukan block scripts,
  dan masukkan seluruh template dalam compile check.
- **Regression Risk:** template tetap legacy/unrouted dan belum dinyatakan layak
  diaktifkan; business/security flow-nya memerlukan review sebelum reuse.
- **Verification:** compile seluruh 54 template dan regression UI tests.

## 6. Findings: Cross-Layer / delivery / tooling

### CA-01 — Alias URL melewati pemblokiran static footage

- **Layer / Severity / Status:** Cross-Layer / Critical / confirmed issue, diperbaiki.
- **Location:** static mount `app/main.py`, `app/core/static_assets.py`.
- **Issue / Evidence:** reproduksi temporary file menghasilkan 403 untuk path
  biasa, tetapi **200** dengan body file pada `/static//snapshots/...` dan
  `/static/%2Fsnapshots/...`; HEAD mengembalikan 200. Varian case juga terbaca pada
  Windows. Tidak menggunakan file footage pengguna.
- **Root Cause:** GET router blocker memeriksa path sebelum StaticFiles melakukan
  normalisasi filesystem.
- **Impact:** footage dapat diambil tanpa autentikasi/grup/audit endpoint media.
- **Recommended Fix / Implementasi:** `PublicStaticFiles` memeriksa normalized path
  dan resolved target, termasuk symlink di dalam static directory. Route blocker
  dan authenticated endpoints tetap tersedia.
- **Regression Risk:** asset publik di luar folder footage harus tetap terbaca.
- **Verification:** GET/HEAD, separator biasa/ganda/encoded, case, kedua folder,
  symlink, serta CSS publik diuji pada temporary directory.

### CA-03 — Boundary legacy snapshot tidak konsisten

- **Layer / Severity / Status:** Cross-Layer / High / confirmed issue, diperbaiki.
- **Location:** `app/routes/snapshots.py`, auth/setup middleware.
- **Issue / Evidence:** penerbit signed token tidak memiliki dependency auth/grup;
  middleware menerima sembarang query token untuk melewati session gate. Raw file
  dan latest info tidak selalu memeriksa grup atau deleted_at.
- **Root Cause:** signed-link capability dan autentikasi pengguna dicampur, dengan
  asumsi middleware sudah membatasi akses.
- **Impact:** akses metadata/file lintas grup, minting capability tanpa hak, serta
  pengambilan footage yang sudah soft-deleted.
- **Recommended Fix / Implementasi:** minting memerlukan current user/group dan
  active snapshot; raw signed link tetap dapat dipakai integration tanpa browser
  session, tetapi harus valid dan footage aktif. Ordinary authenticated access
  memeriksa grup. Query-token exemption hanya untuk signed file route. Path file
  di-resolve dalam root; audit identity berasal dari auth/capability, bukan phone
  yang dikirim pemanggil.
- **Regression Risk:** expires_in dibatasi 1–3600 detik dan batch 100 file; client
  harus mempunyai akses ke file yang ditokenkan. Format token tetap kompatibel.
- **Verification:** anonymous minting/info, bogus query token, denied/deleted file,
  positive token/file, invalid signature, dan link yang dicabut melalui soft delete.

### CA-12 — Test discovery/fixtures legacy tidak aman untuk full-suite run

- **Layer / Severity / Status:** Cross-Layer / Medium / confirmed issue, sebagian
  diperbaiki; restrukturisasi test suite masih backlog.
- **Location / Evidence:** syntax error pada `test_tamper_alert.py`; beberapa skrip
  memakai `SessionLocal` langsung. `test_email_notifier.py` memiliki pemanggilan
  saat import dan transport belum sepenuhnya dimock; `test_docker_import.py`
  memanggil sys.exit saat import. Venv repository menunjuk interpreter lama yang
  tidak tersedia. Fixture global membuat semua tabel melalui SQLite.
- **Root Cause:** skrip diagnosis manual dan test otomatis berada di discovery
  directory yang sama, ditambah fixture tidak sesuai PostgreSQL models.
- **Impact:** collection gagal/keluar atau berisiko efek samping; false failure pada
  JSONB/trigger. Karena itu full-suite discovery tidak dijalankan sembarangan.
- **Recommended Fix / Implementasi:** syntax diperbaiki; mock scheduler sync,
  checkbox image_hd, context map_title, dan readiness/session fixture diperbarui
  berdasarkan kontrak sekarang. Tes baru override schema fixture secara lokal.
  Jalankan hanya modul yang telah ditinjau aman; tidak mengubah venv pengguna.
- **Regression Risk:** memperbaiki syntax tidak menjadikan skrip manual aman untuk CI.
- **Verification:** AST semua Python, selected pytest, unittest terisolasi, tanpa
  database aplikasi atau scheduler nyata. Pisahkan skrip manual dan siapkan fixture
  PostgreSQL disposable sebagai pekerjaan berikutnya.

### CA-15 — Dependency build memiliki advisory npm

- **Layer / Severity / Status:** Cross-Layer / High (hasil scanner awal) / confirmed
  scanner finding, sebagian diremediasi; exploitability aplikasi tidak dibuktikan.
- **Location / Evidence:** npm audit awal melaporkan **11 dependency findings:
  8 High, 3 Moderate**, seluruhnya pada graph build/dev. Paket runtime langsung
  tidak berubah pada pembaruan ini.
- **Root Cause:** transitive lockfile belum memuat patch yang tersedia.
- **Impact:** risiko supply-chain/build, bukan bukti eksploitasi runtime B-Snap.
- **Recommended Fix / Implementasi:** audit fix dalam semver proyek, lockfile only,
  tanpa lifecycle scripts atau force; kemudian npm ci dan build CSS. Direct project
  ranges tetap. Hasil setelah pembaruan: **0 High/Critical, 5 Moderate**.
- **Remaining:** typography, glob, minimatch, selector-parser, sucrase. Rekomendasi
  resolver tertentu memerlukan perubahan dependency yang tidak dipaksakan otomatis;
  evaluasi graph/versi upstream dan output CSS sebelum pembaruan berikutnya.
- **Regression Risk:** CSS hasil typography/autoprefixer dan package resolution;
  build, template tests, dan screenshot lokal diverifikasi.
- **Verification:** npm audit JSON sebelum/sesudah, npm ci, npm run build. Python
  dependency CVE audit dan Docker image vulnerability scan belum dilakukan.

### CA-16 — Default Compose image tertinggal dari versi aplikasi

- **Layer / Severity / Status:** Cross-Layer / Medium / confirmed issue, diperbaiki.
- **Location / Evidence:** Compose memakai default v2.4.1; package, app version,
  Dockerfile, dan `test_default_release_versions_match` mengharapkan 2.5.2.
- **Root Cause:** release metadata tidak diperbarui bersama.
- **Impact:** deployment default dapat menjalankan image berbeda dari source.
- **Recommended Fix / Implementasi:** default tag menjadi v2.5.2; BSNAP_IMAGE
  override dan shared image alias tetap dipertahankan.
- **Regression Risk:** ketersediaan tag registry memerlukan verifikasi deployment;
  audit ini tidak melakukan pull/build/push atau menjalankan Compose.
- **Verification:** version-check script dan release-version regression test.

### CA-22 — Inbox persisten legacy: fitur dihentikan

- **Layer / Severity / Status:** Cross-Layer / Medium (potensial/bersyarat) /
  bug laten dikonfirmasi dengan data sintetis; fitur dihapus pada 2.6.3.
- **Evidence sebelum penghapusan:** `Notification.is_read` berada pada satu row
  broadcast; satu pengguna membaca/menghapus dapat memengaruhi pengguna lain bila
  data broadcast ada. Camera ID Integer tidak sesuai UUID kamera dan query inbox
  belum memeriksa grup. Dampak pada database deployment tidak diverifikasi.
- **Koreksi cakupan:** ini bukan email atau `/email-logs`. Email memakai
  `CameraEmailNotificationLog`/recipients/retry terpisah. Jalur `send_toast()`
  standar tidak mengaktifkan penyimpanan dan frontend tidak ditemukan memakai
  API inbox. `duration=0` hanya mengatur tampilan toast, bukan penyimpanan.
- **Implementasi atas instruksi pengguna:** hapus model, enam endpoint
  `/api/notifications/*`, registrasi router/model, argumen `db`, dan blok insert
  yang tidak aktif. Live toast dan pemeriksaan sesi/penerima/grup tetap tersedia.
- **Migration:** `20261103_retire_inbox` mengarsipkan tabel sebagai
  `retired_notifications`; tidak menghapus data historis. Downgrade memulihkan
  nama/data lama. Tabel arsip dikecualikan dari Alembic autogenerate.
- **Regression Risk:** integrasi eksternal inbox harus berhenti memakai endpoint
  yang dihapus. Jalankan migration sebelum start worker baru; audit tidak
  menjalankan migration pada database aplikasi pengguna.
- **Verification:** endpoint tidak terdaftar/404, live toast authenticated dan
  data `/api/email-logs` tetap berfungsi, PostgreSQL upgrade/downgrade/re-upgrade
  mempertahankan inbox legacy, email log dan queue transport. Lihat bagian 13.

### CA-23 — Routing/dead code masih perlu konsolidasi

- **Layer / Severity / Status:** Cross-Layer / Low / improvement opportunity,
  konsolidasi dan review deprecation diselesaikan pada 2.6.6.
- **Location / Evidence sebelum perbaikan:** GET `/docs` didaftarkan oleh docs router dan app utama;
  beberapa template/router legacy tidak dirender/didaftarkan pada jalur utama.
- **Issue / Root Cause:** riwayat fitur meninggalkan jalur alternatif; routing order
  menentukan handler yang dipakai, bukan hanya nama fungsi.
- **Impact:** maintenance/confusing documentation, bukan bukti akses tanpa izin.
- **Implemented Fix:** satu router dokumentasi: `/docs` mengarah ke
  `/docs/environment`, Swagger admin-only pada `/docs/openapi`, alias
  `/developer/docs` tetap protected, serta alias schema lama menuju `/openapi.json`.
  Handler tertutup di main dan modul dev_docs yang redundan dihapus; navigation
  dan tautan diperbarui. Dead block setelah redirect `/stats` dihapus dan parameter
  camera/days di-URL-encode dengan status/tujuan redirect tetap.
- **Legacy review:** enam template tanpa referensi produksi statis dan tiga modul
  nonaktif ditandai deprecated serta dipertahankan untuk downstream review;
  map_live/map_heatmap dan audit APIs dipastikan aktif, bukan ikut dihapus.
  Lihat [inventaris dan keputusan](legacy_routing_review_2026_11_03.md).
- **Regression Risk:** bookmark, embedded Swagger, dan integrasi dokumentasi.
- **Verification:** tidak ada duplikasi method/path pada registry produksi,
  redirect/bookmark/schema/role tests, encoded stats filters, registry Maps/MFA,
  dan compile semua template. Chrome memuat Swagger assets dan schema melalui
  alias, tanpa page error; Sphinx build dengan warnings-as-errors lulus.
  Legacy view tidak diaktifkan hanya karena kompilasinya lulus.

## 6a. Temuan tambahan pada boundary integrasi

### CA-27 — Identitas sender webhook belum membuktikan sumber request

- **Layer / Severity / Status:** Middleware / High / authentication gap diperbaiki
  pada source 2.6.1; exploit deployment tidak diuji.
- **Location / Evidence sebelum perbaikan:** `/webhook/gowa` sengaja public; handler mengambil sender
  dari body, lalu memeriksa whitelist. Tidak ditemukan signature/shared-secret
  verification pada handler. Pembatasan ingress/proxy di luar aplikasi belum diverifikasi.
- **Issue / Root Cause:** whitelist memvalidasi nilai sender yang diklaim payload,
  bukan asal HTTP request dari GoWA.
- **Impact:** apabila endpoint dapat diakses pihak tidak tepercaya, payload yang
  mengaku berasal dari nomor whitelist dapat memicu aksi bot sesuai role nomor itu.
- **Implemented Fix:** verifikasi native GoWA `X-Hub-Signature-256` memakai
  HMAC-SHA256/constant-time comparison terhadap raw body sebelum parsing/aksi.
  `GOWA_WEBHOOK_SECRET` minimal 32 karakter wajib; konfigurasi kosong/pendek ->503,
  signature invalid/missing/duplicate ->401, body >1 MiB ->413. Tidak ada fallback
  unsigned. Whitelist, role, group, dan deduplikasi provider ID tetap berlaku.
- **Regression Risk:** operator harus menyamakan secret B-Snap dengan
  `WHATSAPP_WEBHOOK_SECRET`/device secret GoWA dan mengoordinasikan rotasi.
  HMAC bukan nonce/timestamp; deduplikasi mengikuti retensi provider message ID.
- **Verification:** raw bytes berubah, signature palsu/hilang/duplicate, body
  oversized, JSON invalid, sender non-whitelist, dan provider ID duplikat diuji
  tanpa transport nyata. Stack aplikasi menolak unsigned webhook sebelum query
  autentikasi. Konfigurasi GoWA/TLS/rotasi nyata tetap pemeriksaan deployment.

### CA-28 — Health monitoring belum konsisten membatasi grup

- **Layer / Severity / Status:** Backend / High / confirmed source issue, diperbaiki pada 2.6.1.
- **Location / Evidence sebelum perbaikan:** `get_health_status_api` memanggil
  `_get_all_devices_health_data(db)` tanpa current group. Query UNION kamera/NVR
  helper tersebut tidak memiliki membership filter; route cukup memerlukan operator.
- **Issue / Root Cause:** role monitoring dianggap sebagai cakupan seluruh device,
  berbeda dari pembatasan grup pada Maps/gallery/media.
- **Impact:** operator dengan group_id tertentu dapat memperoleh metadata/status
  device di luar grup melalui status API. Cakupan history/report/trigger sejenis
  perlu diperiksa dan diselaraskan dalam change set yang sama.
- **Implemented Fix:** scope kamera memakai association, NVR memakai relasi grup
  yang berlaku, pada status/uptime/history/count/dropdown/SLA/export/report dan
  single/bulk trigger. Worker memeriksa ulang membership sebelum network work.
  Progress per grup dipisahkan dari tracker global dan start dikunci per scope;
  scheduled-report definitions global hanya dapat dikelola user group NULL.
  Group NULL mempertahankan cakupan global. Restricted run tidak menjalankan
  cleanup email-log global.
- **Regression Risk:** perubahan memengaruhi count dashboard, SLA/report, pilihan
  filter, dan job monitoring. Hindari hanya menyaring label UI atau satu response.
- **Verification:** subset SQLite terisolasi dengan dua grup, shared camera dan
  primary-group stale, admin/operator restricted/global, semua query/report/trigger,
  serta mock ping. PostgreSQL membuktikan 20 start concurrent menerima satu run
  untuk scope yang sama, tanpa memakai tracker global ID 1.

## 7. Ringkasan implementasi

| Area/file | Perubahan | Finding |
| --- | --- | --- |
| main, static_assets, auth middleware, auth/auth_token | file boundary, proxy trust, cookie, error, sesi/MFA | 01–03, 06–08, 10 |
| media_access, snapshot/video routes/service | grup, hold, active footage, signed token, tamper filter, audit | 03–04, 11 |
| Camera.videos, cameras delete | pertahankan video/file/hash/hold | 13 |
| management/integration routers, resolver, bot | dependency role dan membership aktual | 05, 14 |
| ws routes/filter, NotificationService | handshake dan validasi setiap live delivery | 18 |
| logs backend/template, date-format | HTML safety, JSON, UTC/locale/zone, mobile/badge | 09, 17, 25 |
| Compose, package-lock, generated output.css | versi default dan patch build dependencies | 15–16 |
| new security/frontend regressions, existing fixtures, legacy syntax | test isolation dan compile coverage | 12, 26 |
| AGENTS.md, CHANGELOG.md, laporan/inventaris ini | kontrak helper dan traceability | lintas finding |

Rilis awal 2.6.0 tidak menambah dependency runtime, credential, schema, atau migration.
Rilis lanjutan 2.6.1 menambah migration security; lihat bagian 11. File CSS hasil
build ikut diperbarui.

## 8. Test dan verifikasi

Hasil rilis awal 2.6.0 dan batas cakupan dicatat di tabel berikut. Hasil 2.6.1
menggantikan batas PostgreSQL yang sudah diverifikasi; lihat bagian 11.
Log command rinci tersedia di `tmp/comprehensive-audit-*` pada workspace lokal.
File tmp bukan bagian deployment maupun artefak yang berisi data pengguna.

| Pemeriksaan | Hasil teramati |
| --- | --- |
| Pytest terpilih, 37 modul | **678 passed, 11 skipped**, 55 warnings; seluruh skip memerlukan PostgreSQL test/concurrency |
| Unittest terisolasi, 11 modul | **142 tests, OK** |
| Total tes lulus pada kedua kelompok yang berbeda | **820**; bukan full-suite/coverage claim |
| Python AST | 276 file, tidak ada syntax error |
| Jinja compile | Seluruh 54 template lulus, termasuk legacy view yang dahulu dikecualikan |
| Node syntax | 15 file JS dan 5 harness CJS lulus |
| Regresi browser Logs Viewer | Chrome lokal, 1440×1000 dan 390×844, light/dark, EN–ID–EN, payload HTML tidak dieksekusi, tidak ada page error/page overflow |
| Browser timezone berbeda | America/Los_Angeles; timestamp log tetap Asia/Makassar |
| CSS | npm ci --ignore-scripts dan npm run build berhasil |
| npm audit final | 0 Critical/High, 5 Moderate; exit 1 karena advisory yang masih terbuka |
| Runtime dependency import / image-version scripts | Berhasil untuk environment lokal dan versi 2.5.2 |
| Alembic heads | Satu head: 20261012_email_retry_expiry; tidak menjalankan upgrade |
| Ruff pada helper/security/frontend test baru dan core yang difokuskan | Lulus pada test_audit_security, test_audit_frontend, static_assets, auth_token, media_access |
| Black --check pada lima file tersebut | Lulus, line length 100; blok Python lain yang disentuh diformat secara terarah |
| Ruff seluruh app/scripts | **4.058 temuan masih terbuka** dibanding 4.244 pada scan awal; tidak ada invalid-syntax/F821. Ini masih gagal lint global, bukan gate hijau seluruh repository |
| git diff --check | Lulus |

Artefak visual lokal: `tmp/comprehensive-audit-ui-light.png`,
`tmp/comprehensive-audit-ui-dark.png`, `tmp/comprehensive-audit-ui-mobile.png`.
Ringkasan browser tersimpan pada `tmp/comprehensive-audit-browser.json`.

Metode isolasi:

- DATABASE_URL proses tes diarahkan ke loopback port yang tidak melayani DB;
  SECRET_KEY diganti khusus tes, bukan nilai environment deployment.
- Tes baru memakai subset tabel SQLite kompatibel, mock transport, temporary
  footage, dan override lokal `setup_test_database`. Ini tidak menyatakan aplikasi
  lengkap mendukung SQLite.
- Pytest memakai `-o addopts=''` untuk regression suite terpilih; tidak mengklaim
  coverage 70% atau keberhasilan full test suite.
- Browser Chrome headless memakai hasil render fixture, response fetch mock, asset
  CSS lokal, dan base scripts dinonaktifkan. Ini pemeriksaan Logs Viewer, bukan
  end-to-end seluruh aplikasi, login nyata, atau validasi semua kontrol base layout.

Keterbatasan pada rilis awal: belum ada PostgreSQL disposable/production-like
E2E, upgrade schema nyata, Docker build/pull, kamera/NVR/GoWA/SMTP nyata, full
accessibility/contrast audit semua halaman, atau benchmark fan-out notifikasi.

## 9. Tindak lanjut

1. Terapkan migration/configuration 2.6.1 dan acceptance test pada staging untuk
   CA-19/20/27/28 yang sudah diperbaiki di source. CA-22 dihentikan pada 2.6.3;
   jalankan migration arsipnya saat menerapkan versi tersebut.
2. Pisahkan manual diagnostic scripts dari pytest discovery dan siapkan database
   PostgreSQL test agar migration/trigger/schema bisa diuji rutin.
3. Tuntaskan advisory build Moderate setelah analisis compatibility; tambahkan
   Python dependency/image scan pada pipeline yang memiliki tool sesuai.
4. Selesaikan raw @apply halaman lain; periksa EN/ID,
   keyboard, focus, empty/error/loading states per halaman. Timezone monitor bot
   telah diperbaiki pada 2.6.4. Duplikasi docs dan deprecation review CA-23
   diselesaikan pada 2.6.6.
5. Ukur performa notification delivery dengan dataset/jumlah browser representatif.
   Group counts telah diagregasikan dan dibenchmark pada 2.6.5; ukur kembali pada
   deployment bila membutuhkan angka kapasitas produksi.

Audit ini memberikan bukti dan perbaikan yang dapat ditinjau, beserta backlog yang
masih terbuka; tidak memberi sertifikasi bahwa aplikasi bebas seluruh kerentanan.

## 10. Persiapan rilis 2.6.0

Atas instruksi pengguna, perubahan dipersiapkan sebagai versi **2.6.0** pada
2 November 2026. Minor version dipakai karena mencakup fitur retensi yang sebelumnya
berada pada Unreleased. Versi package, app, Dockerfile, Compose default, dan heading
changelog diselaraskan; rilis historis 2.5.2 tidak ditulis ulang.

Baseline dan hasil audit di atas tetap merujuk source 2.5.2 sebelum version bump.
Inventaris diperbarui terhadap source hasil rilis. Gate regresi mempunyai 820 kasus
lulus dan 11 skip (98,7% kasus lulus, tanpa kegagalan); angka ini mengukur hasil tes
terpilih, bukan persentase keselamatan aplikasi, coverage, atau penutupan findings.
Saat rilis awal, backlog High, advisory Moderate, dan batas PostgreSQL/Docker masih
berlaku. High dan pengujian PostgreSQL ditangani pada rilis lanjutan di bawah.
Push repository tidak menjalankan deployment atau menerbitkan image container.

Verifikasi tambahan setelah version bump: **178 tes release/runtime/UI/security
lulus**, serta script version sync berhasil untuk 2.6.0. Hash inventaris memakai
source dengan line ending LF agar stabil lintas Windows/Linux.

## 11. Penyelesaian seluruh High — rilis 2.6.1

CA-19, CA-20, CA-27, dan CA-28 merupakan empat High yang tersisa setelah 2.6.0.
Semuanya sekarang diperbaiki pada source, disertai regresi; tidak ada High audit
yang masih menjadi backlog implementasi. Advisory build Moderate dan backlog
non-High tetap dicatat pada finding masing-masing.

| Pemeriksaan 2.6.1 | Hasil |
| --- | --- |
| Pytest terpilih, 41 modul | **772 passed, 1 skipped**; bukan full-suite/coverage claim |
| Unittest terisolasi, 11 modul berbeda | **142 tests, OK** |
| Total kedua kelompok | **914 tes lulus**, tanpa kegagalan |
| PostgreSQL 17 disposable | Upgrade database kosong/existing, repeat upgrade, migration security upgrade/downgrade/re-upgrade; tracker global dipertahankan |
| Concurrency PostgreSQL | 20 percobaan login, tepat 10 diterima/10 ditolak; 20 start health scope sama, tepat satu diterima; session/koneksi terpisah |
| Chrome headless / loopback server | Native form + AJAX same-origin 200; same-site origin beda port dan cross-site form 403; hanya dua mutasi sah dieksekusi |
| Fixture tampilan logout | Template dan asset lokal: mobile light/dark, EN–ID–EN, form POST; tidak ada overflow atau page error. Bukan E2E base layout |
| AST / Jinja / Node | 287 Python, 55 template, 15 JavaScript + 5 CJS: lulus |
| Ruff / Black pada 11 file Python baru | Lulus; line length 100 |
| Ruff seluruh app/scripts | 3.971 diagnostic legacy; tidak ada F821/invalid-syntax. Lint global belum hijau |
| CSS build / version sync | `npm run build` dan `check_image_version.py 2.6.1` lulus |
| npm audit | 0 Critical/High, 5 Moderate (belum ditangani pada lingkup High) |
| Alembic | Satu head `20261102_high_security` |

PostgreSQL tes dibuat khusus pada direktori tmp dan port loopback terpisah,
dengan schema unik yang dibersihkan fixture; bukan database aplikasi pengguna.
Skip tunggal merupakan tes DELETE RETURNING concurrency yang tidak dijalankan
oleh fixture SQLite; pengujian PostgreSQL lainnya benar-benar dijalankan, tidak
di-skip. Perbaikan fixture Maps memakai file SQLite sementara agar koneksi request
concurrent terpisah; tes lifecycle migration memverifikasi satu head dan ancestry,
tanpa mengunci nama head lama.

Log lokal: `tmp/high-security-final-pytest.txt`, `tmp/high-security-final-unit.txt`,
`tmp/high-security-browser.json`, dan `tmp/high-security-global-ruff.json`.
Inventaris di-refresh terhadap source 2.6.1 dengan normalisasi CRLF ke LF.

**Penerapan:** ikuti [security rollout](high_security_rollout.md), jalankan migration
pada target yang dipilih, pasang signing secret sama pada B-Snap/GoWA, dan atur
SECRET_KEY/cookie/proxy production dengan benar. Audit tidak mengubah `.env`,
menjalankan migration pada database aplikasi, melakukan deployment/image publish,
atau menghubungi kamera/GoWA/SMTP nyata. Validasi HTTPS proxy, integrasi nyata,
restart worker, dan rotasi secret pada deployment masih diperlukan; hasil ini
bukan sertifikasi bahwa seluruh aplikasi bebas kerentanan.

## 12. Regresi WebSocket Cloudflare Tunnel — rilis 2.6.2

Setelah pengujian deployment pengguna, ditemukan `/ws` masih membandingkan Origin
browser dengan URL backend walaupun `PUBLIC_ORIGIN` telah dipasang untuk CSRF HTTP.
Hal ini dapat menolak koneksi cloudflared yang mengubah Host/port/protokol. Saran
konfigurasi saja pada 2.6.1 belum menyelesaikan jalur tersebut.

Perbaikan menggunakan public origin yang sama untuk handshake WebSocket, dengan
normalisasi host/default port. Tanpa public origin eksplisit, URL request setelah
trusted proxy headers tetap menjadi acuan; X-Forwarded-Host tidak dipercaya sebagai
allowlist. Autentikasi session/token, revocation, recipient, dan group checks pada
setiap delivery tetap aktif. Penolakan handshake mencatat alasan origin/session
tanpa cookie/token/payload. Tidak ada perubahan schema atau dependency baru.

Verifikasi 2.6.2:

- Sebelum perbaikan, enam kasus positif origin/proxy/toast ditolak oleh kode lama;
  setelah perbaikan, **206 pytest terarah lulus**, termasuk **17 kasus proxy**,
  serta **33 unittest sesi/notifikasi** dan **8 Node toast layout tests** lulus.
- Chrome headless membuka HTTPS melalui reverse proxy TLS lokal ke backend HTTP
  dengan Host internal berbeda, cookie Secure/HttpOnly dan session nyata; WebSocket
  terbuka dan toast dari fungsi delivery produksi tampil, tanpa page error.
  Database dan publisher dimock; ini bukan koneksi ke jaringan Cloudflare nyata.
- Invalid/duplicate/cross-origin, port berbeda, revoked/missing session, penerima
  berbeda, dan revocation setelah handshake tetap ditolak/tidak menerima payload.
- Syntax inventory diperbarui terhadap 2.6.2: 288 Python, 55 template, 15 JS dan
  5 CJS. Script version sync, Ruff test baru, serta whitespace check lulus.

Artefak browser lokal: `tmp/tunnel-toast-browser.json`. Hasil 2.6.1 sebelumnya
tetap merupakan catatan historis; tidak menjalankan ulang migrasi PostgreSQL pada
patch tanpa schema ini. Deployment perlu build/pakai image 2.6.2, mengatur origin
HTTPS publik yang tepat, dan recreate web agar environment berubah. Petunjuk ada
di [Cloudflare Tunnel setup](high_security_rollout.md#cloudflare-tunnel-websocket-and-toast-262).

## 13. Penghapusan inbox persisten CA-22 — rilis 2.6.3

Pada 3 November 2026, pengguna memilih menghapus fitur inbox persisten yang tidak
dipakai daripada mengaktifkan ulang model broadcast yang bermasalah. Catatan
temuan historis dipertahankan untuk traceability; tidak lagi menjadi backlog.
Email, `/email-logs`, email retry, WhatsApp, toast langsung, dan notification_queue
bukan bagian fitur yang dihapus. Sticky toast tetap hanya perilaku tampilan.

- **799 pytest lulus, 1 skip**, pada 44 modul yang ditinjau aman, serta **142
  unittest lulus**; bukan full-suite atau persentase keamanan aplikasi.
- PostgreSQL 17 disposable memverifikasi migrasi dari database kosong/existing,
  data inbox arsip tetap utuh saat upgrade/downgrade/re-upgrade, serta email log
  dan queue transport tidak berubah. Autogenerate dengan hook environment nyata
  tidak mengusulkan drop arsip. Schema fixture dibersihkan setelah tes.
- Enam endpoint lama diuji 404 memakai daftar route produksi tanpa startup DB.
  Toast diuji melalui service/delivery authenticated; email-log API memakai
  subset SQLite yang kompatibel dan tidak mengirim email nyata.
- Satu skip tetap tes DELETE RETURNING concurrency milik fixture SQLite.
  PostgreSQL migration/concurrency tests lainnya dijalankan, bukan di-skip.
- Script sinkronisasi versi 2.6.3, AST/Jinja/Node inventory, Ruff/Black pada
  migration dan dua modul tes baru, serta `git diff --check` lulus.

Log lokal: `tmp/retired-inbox-pytest.txt` dan `tmp/retired-inbox-unit.txt`.
Migration baru `20261103_retire_inbox` adalah satu head setelah
`20261102_high_security`. Apply dengan `alembic upgrade head` atau layanan migrate
Compose sebelum menjalankan worker baru. Tidak ada deployment, perubahan `.env`,
penghapusan data production, atau penerbitan image registry dalam pekerjaan ini.

## 14. Timezone monitor bot CA-24 — rilis 2.6.4

Pada 3 November 2026, monitor pesan Bot Builder diselaraskan dengan timezone
konfigurasi B-Snap. Locale tetap EN/ID, sementara instant pesan dan input mesin
tidak bergeser saat bahasa diganti. API tetap memberi 100 pesan terbaru secara
default (limit maksimum 250), dan filter UI tetap bekerja pada hasil tersebut.
Jadwal bot, pengiriman WhatsApp/email, dan schema database tidak diubah.

- **149 pytest terarah lulus**, mencakup monitor API baru, existing bot monitor,
  preview/schedule, date/log frontend, UI, SweetAlert i18n dan sinkronisasi versi.
- **6 Node tests lulus**: WIB/WITA/WIT, Kathmandu, host timezone berbeda, parsing
  invalid, gap/fold New York DST, fractional seconds, EN–ID–EN, date-only dan
  backend-local timestamp. Helper baru membandingkan filter sebagai UTC instant;
  waktu tanpa timezone konfigurasi tidak diam-diam mengikuti browser.
- **Chrome: 8 kombinasi** browser Los Angeles/Jakarta × konfigurasi
  Jakarta/Makassar/Jayapura/London. Timestamp dekat midnight, batas From/To sama
  yang inklusif, range terbalik, dialog detail terbuka saat language change, dan
  nilai input mesin tetap konsisten. Tidak ada page error; desktop light/dark dan
  mobile 390px diperiksa pada fixture template/asset nyata dengan API mock.
- `npm run build`, Node syntax, AST/Jinja inventory, Ruff/Black test API baru,
  version check 2.6.4, dan whitespace check lulus. Ini bukan klaim full-suite atau
  verifikasi integrasi GoWA/deployment nyata. Tidak ada migration baru.

Artefak lokal: `tmp/wa-monitor-browser.json`, `tmp/wa-monitor-light-1440.png`,
`tmp/wa-monitor-dark-1440.png`, dan `tmp/wa-monitor-dark-390.png`.

## 15. Optimasi dan benchmark CA-21 — rilis 2.6.5

Pada 3 November 2026, endpoint daftar grup diubah dari 1+3G SELECT menjadi satu
SELECT. Subquery agregasi independen mempertahankan hitungan kamera berdasarkan
association, NVR/user berdasarkan group_id, grup kosong dan sort nama. Tidak ada
perubahan schema, migration, frontend, maupun aturan akses admin.

[Laporan benchmark](benchmarks/camera_groups_2026_11_03.md) dan
[raw measurements](benchmarks/camera_groups_2026_11_03.json) memuat lima dataset
yang sama sebelum/sesudah, lima warmup dan 30 sampel per variant/dataset, median,
p95, query count, source hash, dan response hash. PostgreSQL 17.9 memakai schema
disposable; HTTP diukur melalui FastAPI TestClient dengan auth override dan
session baru per request. Ini tidak mencakup jaringan Cloudflare atau auth nyata.

| Grup | SELECT sebelum → sesudah | Median sebelum → sesudah |
| ---: | ---: | ---: |
| 0 | 1 → 1 | 4,340 → 6,315 ms |
| 10 | 31 → 1 | 25,567 → 7,607 ms |
| 50 | 151 → 1 | 113,492 → 8,864 ms |
| 100 | 301 → 1 | 246,131 → 12,014 ms |
| 250 | 751 → 1 | 674,469 → 15,282 ms |

Pada 250 grup, median turun 97,73% (44,13× lebih cepat), p95 turun dari 736,654
menjadi 25,173 ms, dan respons JSON identik. Ada overhead tetap sekitar 2 ms pada
kasus tanpa grup; seluruh hasil merupakan pengukuran lokal, bukan SLA produksi.

**810 pytest terpilih lulus, 1 skip**, pada 46 modul termasuk tujuh regresi count
baru, security, timestamp, serta migration/concurrency PostgreSQL. Satu skip adalah
tes DELETE RETURNING concurrency pada fixture SQLite yang sudah dicatat sebelumnya.
Ruff/Black pada script benchmark dan test baru, syntax inventory, version sync
2.6.5 serta whitespace check lulus. Log lokal: `tmp/ca21-tests.txt`.

Script `scripts/benchmark_camera_groups.py` disertakan agar pengukuran dapat
diulang pada PostgreSQL test yang dipilih. Tidak ada database aplikasi, kamera,
SMTP/GoWA atau deployment production yang disentuh oleh benchmark ini.

## 16. Konsolidasi routing/review legacy CA-23 — rilis 2.6.6

Pada 3 November 2026, dokumentasi diarahkan ke tujuan canonical tanpa bergantung
pada urutan pendaftaran router. `/docs` mempertahankan entry lama melalui 307 ke
environment docs. Swagger admin-only memakai `/docs/openapi`; bookmark developer
dan schema lama tetap berfungsi melalui alias. Registrasi GET `/docs` yang
tertutup serta modul developer-docs yang redundan dihapus. Static documentation
mount/absence policy dipertahankan. Deskripsi proxy kini memakai peer IP/CIDR.

Block rendering lama setelah return pada `/stats` dihapus; redirect 301 tetap
mempertahankan filter camera/days dengan URL encoding. Enam template legacy dan
tiga modul nonaktif diberi penanda deprecation dan dicatat di
[legacy review](legacy_routing_review_2026_11_03.md). Penghapusan file legacy lain
tidak dipaksakan tanpa review pemakai downstream; tidak ada route MFA lama yang
diaktifkan. Maps nested routers, stats/insights APIs, password POST dan audit APIs
tetap aktif. Tidak ada migration atau perubahan data.

- **826 pytest terpilih lulus, 1 skip**, pada 47 modul, termasuk **16 regresi
  dokumentasi/routing baru**. Recheck 16 kasus setelah penyelesaian signature/
  metadata docs juga lulus. Skip tunggal tetap tes concurrency fixture SQLite.
- Registry method/path produksi tidak mempunyai duplikasi. Tes memeriksa tujuan
  redirect, HTML Swagger, schema, role admin/anonymous/operator/viewer, parameter
  URL yang mengandung spasi/ampersand/Unicode, dan status route legacy/nested.
- Chrome memverifikasi environment docs light/dark, link Swagger di mobile 390px,
  alias ke canonical Swagger, pemuatan remote assets dan schema, tanpa page error.
  Fixture memakai template/routes nyata dengan auth override; database aplikasi
  dan camera/notifier startup tidak dijalankan.
- Build CSS, Sphinx HTML dengan **warnings-as-errors**, Python/Jinja/Node syntax
  inventory, version sync 2.6.6, Ruff/Black pada docs router/test baru, dan
  whitespace check lulus. Heading anchors Markdown konsisten dengan GitHub;
  audit, rollout, benchmark dan review legacy masuk navigasi generated docs.

Log/artefak lokal: `tmp/routing-tests.txt`, `tmp/documentation-browser.json`,
`tmp/docs-environment-light.png`, `tmp/docs-environment-dark.png`,
`tmp/docs-api-canonical-link.png`, dan `tmp/routing-sphinx-final.log`.
Hasil ini tidak menyatakan seluruh file legacy sudah dihapus atau semua konten
dokumentasi lama telah diaudit ulang. Rilis tetap memerlukan pembaruan source/image
di deployment; tidak melakukan deployment atau image registry publish otomatis.
