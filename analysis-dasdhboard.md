## Task: Analyze the Existing Analytics Dashboard

Lakukan analisis menyeluruh terhadap **Analytics Dashboard yang saat ini sudah tersedia di aplikasi**.

Tujuan utama adalah memahami kualitas dashboard dari sisi **fungsi, data, usability, visualisasi, performa, dan maintainability**, lalu mengidentifikasi kelebihan, kekurangan, serta area yang layak diperbaiki.

Jangan langsung melakukan perubahan kode.

Fokus tahap ini adalah **analysis and assessment only**.

---

## Scope Analysis

Pertama, inspect implementasi Analytics Dashboard yang ada saat ini, termasuk jika tersedia:

- Frontend page/component
- Backend API
- Database query
- Aggregation logic
- Filter
- Date range
- KPI / summary cards
- Charts
- Tables
- Status indicators
- Drill-down
- Export functionality
- Refresh mechanism
- Real-time / polling mechanism
- Permission / access control
- Responsive layout
- Loading state
- Empty state
- Error handling

Trace juga sumber datanya:

```text
Database / API
      ↓
Aggregation / Processing
      ↓
Analytics Endpoint
      ↓
Frontend
      ↓
KPI / Chart / Table
```

Pastikan setiap metric yang ditampilkan dapat dijelaskan sumber dan logikanya.

---

## 1. Understand the Purpose of the Dashboard

Identifikasi terlebih dahulu tujuan Analytics Dashboard saat ini.

Jelaskan:

- siapa target user dashboard
- informasi apa yang ingin diberikan
- keputusan apa yang diharapkan dapat dibuat user dari dashboard
- apakah dashboard lebih bersifat:
  - operational monitoring
  - analytical
  - management summary
  - historical reporting
  - troubleshooting
  - atau kombinasi beberapa fungsi

Jangan hanya menilai visual tanpa memahami tujuan dashboard.

---

## 2. Inventory Existing Dashboard Components

Buat inventaris seluruh komponen dashboard.

Contoh:

```text
Analytics Dashboard
├── Summary KPI
│   ├── Total Cameras
│   ├── Online
│   ├── Offline
│   └── Availability
│
├── Charts
│   ├── Availability Trend
│   ├── Offline Distribution
│   └── Camera Group Distribution
│
├── Filters
│   ├── Date Range
│   ├── Camera Group
│   └── Camera
│
└── Tables
    └── Camera Event History
```

Gunakan struktur aktual dari repository, jangan mengarang komponen yang tidak tersedia.

---

## 3. Analyze KPI Quality

Untuk setiap KPI yang tersedia, evaluasi:

- apa arti KPI tersebut
- sumber datanya
- formula/perhitungannya
- apakah nilainya mudah dipahami
- apakah KPI tersebut actionable
- apakah ada kemungkinan misleading
- apakah ada metric yang redundant
- apakah ada metric penting yang belum tersedia

Contoh pertanyaan:

```text
Apakah "Offline Camera" menunjukkan:
- kondisi sekarang?
- jumlah incident?
- unique camera?
- total downtime?

Apakah "Availability" dihitung berdasarkan:
- current state?
- uptime duration?
- event count?
- monitoring samples?
```

Jika definisinya ambigu, tandai sebagai risiko.

---

## 4. Analyze Data Accuracy

Trace query dan aggregation logic.

Periksa potensi masalah seperti:

- duplicate records
- incorrect joins
- wrong grouping
- timezone issue
- incorrect date boundaries
- NULL handling
- incorrect averaging
- percentage denominator salah
- event yang dihitung berulang
- camera yang sudah deleted masih masuk analytics
- camera group filtering tidak konsisten
- offline event belum ditutup tetapi dihitung salah

Jika memungkinkan, jelaskan formula aktual yang digunakan.

Contoh:

```text
Availability =
(total monitored time - downtime)
----------------------------------
total monitored time
× 100
```

Bandingkan dengan implementasi aktual.

---

## 5. Analyze Visualization

Evaluasi chart dan visualisasi berdasarkan:

### Clarity
Apakah user langsung memahami data yang ditampilkan?

### Relevance
Apakah jenis chart cocok dengan data?

Contoh:

```text
Trend → line chart
Comparison → bar chart
Distribution → bar / donut
Current status → KPI / status card
Time duration → timeline
```

### Information Density
Apakah dashboard terlalu penuh atau terlalu kosong?

### Visual Hierarchy
Apakah informasi paling penting terlihat terlebih dahulu?

### Consistency
Periksa:

- warna
- typography
- spacing
- card style
- chart legend
- label
- icon
- status indicator

---

## 6. Analyze User Experience

Evaluasi workflow user.

Contoh:

```text
User membuka Analytics Dashboard
        ↓
Apakah langsung mengetahui kondisi utama?
        ↓
Apakah bisa mengetahui penyebab?
        ↓
Apakah bisa filter data?
        ↓
Apakah bisa drill-down ke camera tertentu?
```

Identifikasi:

- jumlah klik yang diperlukan
- filter yang sulit digunakan
- informasi yang tidak actionable
- informasi yang terlalu tersembunyi
- informasi yang terlalu redundant

---

## 7. Analyze Filters

Periksa filter yang tersedia.

Contoh:

- date range
- camera
- camera group
- status
- location

Evaluasi:

```text
Apakah filter saling sinkron?

Apakah mengganti Camera Group otomatis membatasi Camera?

Apakah date range mempengaruhi seluruh widget?

Apakah filter state tetap tersimpan saat refresh?

Apakah filter URL-shareable?

Apakah clear/reset filter tersedia?
```

Cari juga potensi bug akibat perubahan multi Camera Group.

Jika satu camera sekarang dapat memiliki lebih dari satu Camera Group, pastikan Analytics Dashboard tidak masih mengasumsikan:

```text
camera.group_id
```

sebagai hubungan tunggal.

---

## 8. Analyze Performance

Periksa bagaimana dashboard mengambil data.

Identifikasi:

- jumlah API request ketika halaman dibuka
- query database yang mahal
- query berulang
- N+1 query
- aggregation di frontend yang seharusnya di backend
- response payload terlalu besar
- chart mengambil raw event terlalu banyak
- missing index
- polling terlalu sering
- expensive COUNT / GROUP BY
- redundant API calls

Jika ditemukan query yang berpotensi lambat, jelaskan alasannya.

---

## 9. Analyze Scalability

Evaluasi jika jumlah data meningkat, misalnya:

```text
100 cameras
1,000 cameras
10,000 cameras

100,000 monitoring events
1,000,000 monitoring events
```

Apakah dashboard tetap efisien?

Periksa apakah analytics menggunakan:

- pagination
- aggregation
- indexed queries
- caching
- pre-computed statistics
- materialized summary
- background processing

Jangan menyarankan kompleksitas tambahan jika belum diperlukan.

---

## 10. Analyze Real-Time Behavior

Jika dashboard mempunyai data near real-time / monitoring:

Periksa:

- refresh interval
- polling
- WebSocket / SSE
- manual refresh
- cache
- stale data

Jelaskan apakah behavior sekarang sudah sesuai kebutuhan operational dashboard.

Pastikan tidak terjadi kondisi:

```text
Dashboard menunjukkan ONLINE
tetapi monitoring utama sebenarnya sudah OFFLINE
```

karena perbedaan refresh interval atau cache.

---

## 11. Analyze Error / Empty / Loading States

Periksa bagaimana UI merespons jika:

```text
API loading
API error
database error
no data
filter menghasilkan 0 result
chart kosong
permission denied
```

Dashboard tidak boleh menampilkan angka misleading seperti:

```text
0 Cameras
0% Availability
```

jika sebenarnya API gagal.

---

## 12. Analyze Maintainability

Periksa kualitas implementasi kode.

Evaluasi:

- separation of concerns
- reusable components
- naming
- duplicated query
- duplicated transformation
- hardcoded value
- magic number
- chart config duplication
- overly large component
- overly complex API endpoint
- tightly coupled frontend/backend

Jangan melakukan refactor pada tahap ini.

Identifikasi saja.

---

## 13. Identify Strengths

Buat daftar hal yang sudah bagus.

Gunakan bukti dari implementasi.

Contoh:

```text
Strength:
Dashboard menggunakan satu aggregation endpoint sehingga frontend
tidak perlu menghitung metric secara lokal.

Benefit:
Logic analytics lebih konsisten dan lebih mudah divalidasi.
```

Jangan menulis kelebihan yang generik tanpa evidence.

---

## 14. Identify Weaknesses

Untuk setiap kekurangan gunakan format:

```text
Issue
Impact
Evidence
Recommended Direction
Priority
```

Contoh:

```text
Issue:
Offline distribution menggunakan raw event count.

Impact:
Camera yang sering flapping terlihat lebih buruk dibanding camera
yang offline dalam waktu lama.

Evidence:
Aggregation menggunakan COUNT(event_id).

Recommended Direction:
Gunakan downtime duration atau unique camera depending on intended KPI.

Priority:
Medium
```

---

## 15. Prioritize Findings

Kelompokkan findings menjadi:

```text
Critical
High
Medium
Low
```

Gunakan kriteria:

### Critical
Data salah atau dashboard memberikan informasi misleading.

### High
Menghambat keputusan operasional atau berpotensi menyebabkan masalah performa besar.

### Medium
UX atau analytics dapat diperbaiki namun tidak menyebabkan kesalahan signifikan.

### Low
Cosmetic / maintainability improvement.

---

## 16. Identify Missing Analytics

Setelah memahami dashboard existing, identifikasi metric atau visualisasi yang secara logis berguna tetapi belum tersedia.

Contoh hanya jika relevan dengan data yang tersedia:

```text
Camera availability trend
Top cameras by downtime
Top camera groups by downtime
MTTR
Incident count
Repeated offline / flapping camera
Downtime duration
Most unstable cameras
Camera availability ranking
Group availability comparison
Offline timeline
```

Jangan menambahkan metric hanya karena umum digunakan.

Pastikan metric:

- bisa dihitung dari data existing
- mempunyai nilai operasional
- mempunyai definisi yang jelas

---

## 17. Multi Camera Group Compatibility

Karena Camera dapat memiliki lebih dari satu Camera Group, secara khusus audit Analytics Dashboard terhadap perubahan tersebut.

Cari semua asumsi seperti:

```text
camera.group
camera.group_id
camera.camera_group
camera.camera_group_id
```

Pastikan analytics dapat memahami relasi:

```text
Camera
├── Group A
└── Group B
```

Analisis bagaimana camera tersebut akan dihitung pada:

- group filter
- group statistics
- distribution
- availability
- event statistics

Tandai kemungkinan double counting.

Contoh:

```text
Camera A berada di Group A dan Group B.

Camera A harus muncul ketika filter:
Group A → yes
Group B → yes

Tetapi pada Total Cameras global:
Camera A tetap dihitung 1 camera.
```

Ini sangat penting.

---

## 18. Expected Output

Setelah selesai menganalisis repository, berikan laporan dengan struktur:

### 1. Executive Summary
Ringkasan kondisi Analytics Dashboard saat ini.

### 2. Existing Architecture
Jelaskan:

```text
Data Source
    ↓
Backend Analytics
    ↓
API
    ↓
Frontend Dashboard
```

### 3. Current Dashboard Components
Daftar seluruh KPI, chart, filter, dan table.

### 4. Strengths
Kelebihan dashboard berdasarkan implementasi aktual.

### 5. Weaknesses
Kekurangan dan potensi masalah.

Gunakan:

```text
Issue
Impact
Evidence
Priority
```

### 6. Data Accuracy Risks
Potensi masalah perhitungan analytics.

### 7. UX / Visualization Findings
Evaluasi tampilan dan usability.

### 8. Performance Findings
Query/API/frontend performance.

### 9. Multi Camera Group Impact
Analisis dampak perubahan many-to-many Camera Group.

### 10. Missing Analytics
Metric atau informasi yang layak ditambahkan.

### 11. Recommended Improvements

Kelompokkan:

```text
Quick Wins
Medium Improvements
Long-Term Improvements
```

### 12. Recommended Dashboard Structure

Jika ada desain informasi yang lebih baik, berikan struktur konseptual seperti:

```text
Analytics Dashboard

[ Total Cameras ] [ Online ] [ Offline ] [ Availability ]

Availability Trend
────────────────────────────────────────

Downtime by Camera Group
────────────────────────────────────────

Top Problem Cameras
────────────────────────────────────────

Camera Event / Downtime History
────────────────────────────────────────
```

Tidak perlu mengubah source code pada tahap ini.

---

## Important Constraints

- Do not modify code.
- Do not modify database schema.
- Do not create migrations.
- Do not refactor.
- Do not redesign blindly.
- Base all findings on the actual repository.
- Clearly distinguish confirmed issues from recommendations.
- Mention file names/functions/components as evidence where relevant.
- Do not claim a problem exists unless supported by the implementation.
- Do not claim something works unless verified from the code.
- Consider backend, frontend, database, and user workflow together.

The objective is to produce a technically grounded assessment that can be used as the basis for the next Analytics Dashboard improvement task.