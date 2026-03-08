# Toast Notification Integration Status

> Dokumentasi integrasi Toast Notification System di B-Snap

---

## 📊 Status Integrasi

### ✅ Sudah Diintegrasikan

| Halaman/Modul | Status | Detail |
|--------------|--------|--------|
| **Demo Page** | ✅ Aktif | `/demo/toast/` - Halaman demonstrasi lengkap |
| **Base Template** | ✅ Siap | `toast.js` sudah di-load di `base.html` |
| **Snapshot Gallery** | ✅ Aktif | Capture & delete snapshot dengan notifikasi |
| **Camera Management** | ✅ Aktif | Create, update, delete camera notifications |
| **Settings/Config** | ✅ Aktif | Save configuration notifications |

### ⏳ Belum Diintegrasikan (Ready to Use)

| Halaman/Modul | Status | Prioritas |
|--------------|--------|-----------|
| **User Management** | ⏳ Belum | Add/edit/delete user |
| **Login/Logout** | ⏳ Belum | Auth notifications |
| **Health Check** | ⏳ Belum | Camera online/offline alerts |
| **Bulk Operations** | ⏳ Belum | Progress notifications |

---

## ✅ Integrasi Aktif

### 1. Demo Page (`/demo/toast/`)

**URL:** `http://localhost:8080/demo/toast/`

**Fitur Demo:**
- ✅ Basic types (success, error, warning, info)
- ✅ System notifications (camera offline/online, storage warning)
- ✅ Position testing (6 positions)
- ✅ Action buttons
- ✅ Persistent notifications
- ✅ Client-side API (tanpa server)

**Screenshot:**
```
┌─────────────────────────────────────────────┐
│  🍞 Toast Notification Demo                 │
├─────────────────────────────────────────────┤
│  [Success] [Error] [Warning] [Info]        │
│                                             │
│  System Notifications:                      │
│  [Camera Offline] [Camera Online]          │
│  [Storage Warning] [With Actions]          │
│                                             │
│  Positions:                                 │
│  [Top Left] [Top Center] [Top Right]       │
│  [Bottom Left] [Bottom Center] [Bottom R]  │
└─────────────────────────────────────────────┘
```

---

## 🔧 Service Layer (Siap Pakai)

### SnapshotService dengan Notifikasi

**File:** `app/utils/snapshot_service.py`

**Notifikasi yang sudah tersedia:**

| Event | Notifikasi | Tipe |
|-------|-----------|------|
| Snapshot berhasil | "Snapshot saved from '{camera_name}'" | ✅ Success |
| Snapshot gagal | "Failed to capture snapshot: {error}" | ❌ Error |
| Camera tidak ditemukan | "Camera with ID {id} not found" | ❌ Error |
| Camera disabled | "Camera '{name}' is currently disabled" | ⚠️ Warning |
| Bulk progress | "Capturing from {camera}... ({current}/{total})" | ℹ️ Info |
| Bulk complete (success) | "Successfully captured {count} cameras" | ✅ Success |
| Bulk complete (partial) | "{success} succeeded, {failed} failed" | ⚠️ Warning |
| Delete snapshot | "Snapshot from '{camera}' deleted" | ✅ Success |

**Cara Menggunakan:**

```python
from app.utils.snapshot_service import SnapshotService

# Di route handler
snapshot = await SnapshotService.capture_snapshot(
    camera_id=1,
    db=db,
    triggered_by="manual"
)

# Bulk capture
results = await SnapshotService.bulk_capture(
    camera_ids=[1, 2, 3],
    db=db
)
```

**⚠️ Catatan:** Saat ini `snap_gallery.py` masih menggunakan `take_snapshot` (sync), belum `SnapshotService` (async dengan notifikasi).

---

## 📝 Cara Integrasi ke Routes

### Pattern Dasar

```python
from app.utils.notification_service import NotificationService

@router.post("/cameras/{camera_id}/action")
async def some_action(
    camera_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(user_access_required)
):
    try:
        # ... logic ...
        
        await NotificationService.success(
            message="Action completed successfully!",
            title="Success",
            actions=[
                {"label": "View", "url": f"/cameras/{camera_id}"}
            ]
        )
        
        return {"success": True}
        
    except Exception as e:
        await NotificationService.error(
            message=f"Action failed: {str(e)}",
            title="Error"
        )
        raise HTTPException(status_code=500, detail=str(e))
```

### Pre-built System Notifications

```python
# Camera events
await NotificationService.camera_offline(camera_name="Front Gate", camera_id=1)
await NotificationService.camera_online(camera_name="Front Gate", camera_id=1)

# Snapshot events
await NotificationService.snapshot_saved(
    camera_name="Front Gate",
    camera_id=1,
    snapshot_id=123
)

# Storage alerts
await NotificationService.storage_warning(used_percent=87.5)
await NotificationService.storage_critical(used_percent=95.0)
```

---

## 🎯 Rekomendasi Integrasi Berikutnya

### 1. Snapshot Gallery (HIGH PRIORITY)

**File:** `app/routes/snap_gallery.py`

**Ganti:**
```python
# Dari:
from app.utils.snapshot_service import take_snapshot
result = take_snapshot(camera, db)

# Ke:
from app.utils.snapshot_service import SnapshotService
result = await SnapshotService.capture_snapshot(camera.id, db)
```

### 2. Camera CRUD Operations (HIGH PRIORITY)

**File:** `app/routes/cameras.py`

**Events:**
- Camera created → "Camera '{name}' added successfully"
- Camera updated → "Camera '{name}' updated"
- Camera deleted → "Camera '{name}' deleted"
- Camera enabled/disabled → "Camera '{name}' is now {status}"

### 3. Settings/Configuration (MEDIUM PRIORITY)

**File:** `app/routes/config.py`

**Events:**
- Settings saved → "Settings saved successfully"
- Configuration error → "Failed to save settings: {error}"

### 4. User Management (MEDIUM PRIORITY)

**File:** `app/routes/user_management.py`

**Events:**
- User created → "User '{username}' created"
- User updated → "User '{username}' updated"
- User deleted → "User '{username}' deleted"
- Password changed → "Password updated successfully"

### 5. Health Check (LOW PRIORITY)

**File:** `app/routes/health.py` atau `app/jobs/scheduler.py`

**Events:**
- Camera goes offline → Auto-trigger `camera_offline()`
- Camera comes back online → Auto-trigger `camera_online()`
- Storage > 85% → Auto-trigger `storage_warning()`
- Storage > 95% → Auto-trigger `storage_critical()`

---

## 🛠️ Client-Side Usage (JavaScript)

Toast JS sudah tersedia di semua halaman (di-load di `base.html`).

### Basic Usage

```javascript
// Simple notifications
Toast.success('Operation completed!');
Toast.error('Something went wrong');
Toast.warning('Please check your input');
Toast.info('New update available');

// With options
Toast.success('Snapshot saved', {
    duration: 5000,
    position: 'top-center',
    actions: [
        { label: 'View', url: '/snapshots/123' }
    ]
});

// Clear all
Toast.dismissAll();
```

---

## 📋 Checklist Integrasi

### Phase 1: Core Features
- [x] Demo page
- [x] Snapshot Gallery (ganti ke SnapshotService)
  - [x] Capture snapshot notifications
  - [x] Delete snapshot notifications
- [x] Camera CRUD
  - [x] Create camera notification
  - [x] Update camera notification
  - [x] Delete camera notification
- [x] Settings/Config
  - [x] Save settings notification
- [ ] User Management

### Phase 2: System Events
- [ ] Health Check alerts
- [ ] Storage monitoring
- [ ] Background job status
- [ ] Login/logout events

### Phase 3: Advanced
- [ ] Bulk operation progress
- [ ] Import/CSV upload status
- [ ] Email notification status
- [ ] WebSocket connection status

---

## 🔍 Testing

### Manual Testing
1. Buka `http://localhost:8080/demo/toast/`
2. Test semua tombol notifikasi
3. Verifikasi WebSocket connection di browser console

### Automated Testing
```bash
# Run tests
pytest app/tests/ -v -k "notification"
```

---

## 🐛 Troubleshooting

### Toast tidak muncul
1. Cek browser console untuk error
2. Verifikasi WebSocket terhubung (lihat status di demo page)
3. Pastikan `toast.js` di-load (cek Network tab)

### Notifikasi tidak real-time
1. Cek WebSocket connection status
2. Pastikan worker berjalan: `docker-compose logs notifier`
3. Verifikasi database connection

---

## 📚 Related Documentation

- [Toast Notifications Guide](./TOAST_NOTIFICATIONS.md) - Dokumentasi lengkap API
- [Feature Roadmap](./FEATURE_ROADMAP.md) - Prioritas fitur berikutnya

---

*Last Updated: March 9, 2026 (Updated with Phase 1 integrations)*
*Maintainer: B-Snap Development Team*
