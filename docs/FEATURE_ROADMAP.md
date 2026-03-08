# B-Snap Feature Roadmap

> Roadmap pengembangan fitur B-Snap dengan prioritas berdasarkan effort dan impact.

---

## Legend

| Status | Icon | Keterangan |
|--------|------|------------|
| Completed | ✅ | Sudah diimplementasikan |
| In Progress | 🔄 | Sedang dikerjakan |
| Pending | ⏳ | Dalam daftar todo |
| Planned | 📋 | Direncanakan untuk versi berikutnya |

---

## Prioritas Matrix

```
                    HIGH IMPACT
                         │
    ┌────────────────────┼────────────────────┐
    │                    │                    │
    │   LOW EFFORT       │   HIGH EFFORT      │
    │   (Quick Wins)     │   (Major Projects) │
    │                    │                    │
    │   ✅ Toast Notif   │   📋 Mobile App    │
    │   ✅ Data Export   │   📋 ML Analytics  │
    │   ⏳ Bulk Ops      │   📋 Cloud Storage │
    │   ⏳ Dark Mode     │                    │
    │                    │                    │
────┼────────────────────┼────────────────────┼────
    │                    │                    │
    │   LOW EFFORT       │   HIGH EFFORT      │
    │   (Fill-ins)       │   (Avoid/Defer)    │
    │                    │                    │
    │   ⏳ Keyboard      │   ⏳ Rewite Core   │
    │   ⏳ Tooltips      │   ⏳ DB Migration  │
    │                    │                    │
    └────────────────────┼────────────────────┘
                         │
                    LOW IMPACT
```

---

## 1. HIGH IMPACT, LOW EFFORT (Quick Wins) 🎯

Fitur dengan return value tertinggi - mudah diimplementasikan tapi memberikan impact besar.

### 1.1 Toast Notification System ✅

| Detail | Status |
|--------|--------|
| **Status** | ✅ **COMPLETED** |
| **Effort** | 2 hari |
| **Impact** | ⭐⭐⭐⭐⭐ |
| **Files Modified** | 12+ files |
| **Tech** | WebSocket, JavaScript, SQLAlchemy |

**Deskripsi:**
Sistem notifikasi real-time dengan WebSocket untuk memberikan feedback langsung ke user.

**Fitur yang diimplementasikan:**
- ✅ 4 tipe notifikasi: success, error, warning, info
- ✅ 6 posisi: top/bottom + left/center/right
- ✅ Action buttons dalam notifikasi
- ✅ Dark mode support
- ✅ Auto-dismiss dengan progress bar
- ✅ Persistent notifications
- ✅ Demo page: `/demo/toast/`

**Files Created:**
- `app/utils/notification_service.py`
- `app/routes/notifications.py`
- `app/models/notification.py`
- `assets/js/toast.js`
- `templates/demo/toast_demo.html`
- `docs/TOAST_NOTIFICATIONS.md`

---

### 1.2 Data Export (CSV/Excel/PDF) ⏳

| Detail | Status |
|--------|--------|
| **Status** | ⏳ **PENDING** |
| **Effort** | 1-2 hari |
| **Impact** | ⭐⭐⭐⭐⭐ |
| **Priority** | HIGH |

**Deskripsi:**
Export data Cameras, Snapshots, Logs ke format CSV/Excel/PDF untuk reporting dan audit.

**Kenapa Penting:**
- User sering butuh laporan untuk management
- B-Snap sudah punya upload CSV (cameras), tapi belum ada export
- Bisa gunakan `reportlab` (sudah diinstall di Dockerfile) dan `openpyxl`

**Implementasi:**
```python
# app/routes/exports.py
@router.get("/export/cameras")
async def export_cameras(format: str = "csv"):  # csv, xlsx, pdf
    cameras = db.query(Camera).all()
    if format == "csv":
        return generate_csv(cameras)
    elif format == "pdf":
        return generate_pdf_report(cameras)
```

**Tech Stack:**
- `pandas` - Data manipulation
- `openpyxl` - Excel export
- `reportlab` - PDF export (sudah tersedia)

---

### 1.3 Bulk Operations ⏳

| Detail | Status |
|--------|--------|
| **Status** | ⏳ **PENDING** |
| **Effort** | 2-3 hari |
| **Impact** | ⭐⭐⭐⭐⭐ |
| **Priority** | HIGH |

**Deskripsi:**
Checkbox multi-select untuk operasi massal di Gallery & Camera List.

**Fitur:**
- 🗑️ Bulk Delete Snapshots
- 🔄 Bulk Retake Snapshot
- ✅ Bulk Enable/Disable Cameras
- 📥 Bulk Export

**UI Changes:**
- Tambahkan checkbox column
- Action bar di atas tabel
- Confirmation dialog untuk operasi destructive

---

### 1.4 Dark Mode Persistence ⏳

| Detail | Status |
|--------|--------|
| **Status** | ⏳ **PENDING** |
| **Effort** | 1 hari |
| **Impact** | ⭐⭐⭐⭐ |
| **Priority** | MEDIUM |

**Deskripsi:**
Simpan preference dark mode ke localStorage + deteksi system preference.

**Implementasi:**
```javascript
// assets/js/theme.js
const theme = localStorage.getItem('theme') || 
              (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
document.documentElement.classList.toggle('dark', theme === 'dark');
```

**Bonus:** Toggle switch di navbar dengan ikon 🌙/☀️

---

## 2. HIGH IMPACT, HIGH EFFORT (Major Projects) 🚀

Fitur besar yang memerlukan effort signifikan tapi memberikan value tinggi.

### 2.1 Mobile App (PWA/Flutter) 📋

| Detail | Status |
|--------|--------|
| **Status** | 📋 **PLANNED** |
| **Effort** | 2-4 minggu |
| **Impact** | ⭐⭐⭐⭐⭐ |
| **Target Version** | v2.0 |

**Opsi:**
1. **PWA (Progressive Web App)** - Convert existing web app
2. **Flutter** - Cross-platform native app

**Fitur Mobile:**
- Push notifications
- Offline mode
- QR scanner untuk camera setup
- Quick snapshot button

---

### 2.2 ML-Powered Analytics 📋

| Detail | Status |
|--------|--------|
| **Status** | 📋 **PLANNED** |
| **Effort** | 3-4 minggu |
| **Impact** | ⭐⭐⭐⭐⭐ |
| **Target Version** | v2.5 |

**Fitur:**
- Object detection (people, vehicle, animal)
- Motion pattern analysis
- Anomaly detection
- Auto-tagging snapshots

**Tech Stack:**
- OpenCV + YOLO/TensorFlow
- Redis untuk caching
- Background job processing

---

### 2.3 Cloud Storage Integration 📋

| Detail | Status |
|--------|--------|
| **Status** | 📋 **PLANNED** |
| **Effort** | 1-2 minggu |
| **Impact** | ⭐⭐⭐⭐ |
| **Target Version** | v2.0 |

**Provider:**
- AWS S3
- Google Cloud Storage
- Azure Blob
- MinIO (self-hosted)

**Fitur:**
- Automatic cloud backup
- Tiered storage (local → cloud)
- CDN integration untuk fast access

---

## 3. LOW IMPACT, LOW EFFORT (Fill-ins) 📝

Fitur kecil yang bisa dikerjakan saat ada waktu luang.

### 3.1 Keyboard Shortcuts ⏳

| Detail | Status |
|--------|--------|
| **Status** | ⏳ **PENDING** |
| **Effort** | 1 hari |
| **Impact** | ⭐⭐⭐⭐ |
| **Priority** | LOW |

**Shortcut Mapping:**

| Shortcut | Action |
|----------|--------|
| `Ctrl/Cmd + K` | Focus search bar |
| `Ctrl/Cmd + S` | Take snapshot (global) |
| `Ctrl/Cmd + D` | Toggle dark mode |
| `Esc` | Close modal/dropdown |
| `?` | Show shortcut help modal |
| `G` then `M` | Go to Maps |
| `G` then `C` | Go to Cameras |
| `G` then `S` | Go to Snapshots |

**Library:** `hotkeys-js` (kecil, 2KB) atau vanilla JS

---

### 3.2 UI Tooltips & Hints ⏳

| Detail | Status |
|--------|--------|
| **Status** | ⏳ **PENDING** |
| **Effort** | 0.5 hari |
| **Impact** | ⭐⭐⭐ |
| **Priority** | LOW |

**Deskripsi:**
Tambahkan tooltips untuk menu dan buttons agar lebih user-friendly.

---

## 4. LOW IMPACT, HIGH EFFORT (Avoid/Defer) ⛔

Fitur yang effort-nya tinggi tapi impact-nya rendah - hindari untuk sekarang.

### 4.1 Core System Rewrite ⛔

| Detail | Status |
|--------|--------|
| **Status** | ⛔ **DEFERRED** |
| **Effort** | 1-2 bulan |
| **Impact** | ⭐⭐ |
| **Priority** | LOW |

**Kenapa Dihindari:**
- Current system sudah stable
- Risk of introducing bugs
- Better to incrementally improve

---

### 4.2 Database Migration (Switch to MongoDB) ⛔

| Detail | Status |
|--------|--------|
| **Status** | ⛔ **DEFERRED** |
| **Effort** | 2-3 minggu |
| **Impact** | ⭐⭐ |
| **Priority** | LOW |

**Kenapa Dihindari:**
- PostgreSQL sudah cukup untuk use case saat ini
- Migration risk tinggi
- No clear benefit untuk scale saat ini

---

## Implementation Checklist

### Phase 1: Quick Wins (Q1 2026)

- [x] Toast Notification System
  - [x] Core service implementation
  - [x] WebSocket integration
  - [x] Snapshot Gallery integration
  - [x] Camera Management integration
  - [x] Settings integration
- [ ] Data Export (CSV/Excel/PDF)
- [ ] Bulk Operations
- [ ] Dark Mode Persistence

### Phase 2: Enhancement (Q2 2026)

- [ ] Keyboard Shortcuts
- [ ] UI Tooltips
- [ ] Advanced Search & Filter
- [ ] Dashboard Widgets

### Phase 3: Major Features (Q3-Q4 2026)

- [ ] Mobile App (PWA)
- [ ] Cloud Storage Integration
- [ ] ML-Powered Analytics
- [ ] API Rate Limiting

---

## Summary

| Category | Completed | Pending | Total |
|----------|-----------|---------|-------|
| Quick Wins | 1 (with sub-tasks) | 3 | 4 |
| Major Projects | 0 | 3 | 3 |
| Fill-ins | 0 | 2 | 2 |
| Avoid/Defer | 0 | 2 | 2 |
| **Total** | **4** | **7** | **11** |

**Progress: 36% Complete** 🎯

---

## Notes

- Selalu prioritaskan **Quick Wins** untuk maximum user satisfaction
- Major Projects harus direncanakan dengan matang sebelum implementasi
- Fill-ins bisa dikerjakan saat ada waktu luang antara tasks
- Avoid/Defer items bisa direvisit jika requirement berubah

---

*Last Updated: March 9, 2026 (Phase 1 integrations complete)*
*Maintainer: B-Snap Development Team*
