# B-SNAP Health/History Endpoint Improvements

> TODO list untuk improvement endpoint `health/history` yang akan menaikkan nilai jual B-SNAP.

---

## 📋 Overview

Dokumen ini berisi rekomendasi improvement komprehensif untuk endpoint `health/history` dan fitur terkait monitoring kesehatan perangkat CCTV. Improvement ini dirancang untuk meningkatkan nilai jual B-SNAP ke segmen enterprise dan compliance-driven markets.

---

## 🎯 Prioritas Implementasi

| Priority | Fitur | Effort | Impact | Status |
|----------|-------|--------|--------|--------|
| P0 | Executive PDF Report Generator | Medium | High | 🔴 Pending |
| P0 | SLA & Compliance Dashboard | Medium | High | 🔴 Pending |
| P1 | Availability Calendar / Heatmap View | Low | Medium | 🔴 Pending |
| P1 | Multi-dimensional Filtering & Views | Low | Medium | 🔴 Pending |
| P2 | Predictive Health Analytics | High | Very High | 🔴 Pending |
| P2 | Real-time Alert History Integration | Medium | Medium | 🔴 Pending |
| P3 | Comparative & Benchmarking Analysis | Medium | Medium | 🔴 Pending |
| P3 | Network Performance Metrics | Medium | Medium | 🔴 Pending |
| P3 | Incident Timeline & Root Cause Analysis | Medium | Medium | 🔴 Pending |
| P4 | Integration API for External Systems | High | High | 🔴 Pending |

---

## 🔴 P0 - Critical (Implementasi Segera)

### 1. Executive PDF Report Generator
**File terkait:** `app/routes/health.py`, `templates/reports/health_report.html`

**Deskripsi:**
Export laporan profesional dalam format PDF untuk kebutuhan presentasi management dan compliance audit.

**Fitur:**
- [ ] Cover page dengan logo & periode laporan
- [ ] Executive summary (total uptime, incident terbesar, SLA compliance)
- [ ] Chart: uptime trend, outage heatmap, top 10 worst performers
- [ ] Detail table per device dengan sorting
- [ ] SLA compliance summary dengan status Pass/Fail
- [ ] Auto-scheduled report (weekly/monthly) via email
- [ ] Format pilihan: PDF, Excel, CSV

**Endpoint:**
```python
@router.post("/health/history/report")
async def generate_health_report(
    start_date: date,
    end_date: date,
    format: Literal["pdf", "excel", "csv"] = "pdf",
    include_charts: bool = True,
    recipients: Optional[List[str]] = None
)
```

**Technical Notes:**
- Gunakan ReportLab (sudah ada di insights.py)
- Reuse chart generation dari existing code
- Tambahkan model `ScheduledReport` untuk auto-schedule

---

### 2. SLA & Compliance Dashboard
**File terkait:** `app/routes/health.py`, `app/models/sla_report.py`

**Deskripsi:**
Report SLA (Service Level Agreement) compliance untuk periode custom dengan metrik enterprise-grade.

**Fitur:**
- [ ] SLA compliance per device/group/location
- [ ] MTTR (Mean Time To Recovery) calculation
- [ ] MTBF (Mean Time Between Failures) calculation
- [ ] Incident severity classification (Critical/Major/Minor)
- [ ] Compliance status dashboard (Pass/Fail/Warning)
- [ ] Perbandingan dengan SLA target (default: 99.5%)
- [ ] Export untuk audit documentation

**Endpoint:**
```python
@router.get("/health/sla-report")
async def sla_report(
    start_date: date,
    end_date: date,
    group_by: Literal["camera", "location", "group"] = "camera",
    sla_threshold: float = 99.5
)
```

**Database Changes:**
```python
# app/models/sla_report.py
class SLAReport(Base):
    __tablename__ = "sla_reports"
    id = Column(String(36), primary_key=True, default=generate_uuid)
    camera_id = Column(String(36), ForeignKey("cameras.id"))
    period_start = Column(Date)
    period_end = Column(Date)
    uptime_percentage = Column(Float)
    mttr_seconds = Column(Integer)  # Mean Time To Recovery
    mtbf_seconds = Column(Integer)  # Mean Time Between Failures
    incident_count = Column(Integer)
    severity_critical = Column(Integer, default=0)
    severity_major = Column(Integer, default=0)
    severity_minor = Column(Integer, default=0)
    compliance_status = Column(String(20))  # Pass/Fail/Warning
```

---

## 🟡 P1 - High Priority

### 3. Availability Calendar / Heatmap View
**File terkait:** `app/routes/health.py`, `templates/health_heatmap.html`

**Deskripsi:**
Visualisasi availability dalam format calendar heatmap untuk identifikasi pattern downtime.

**Fitur:**
- [ ] Yearly heatmap view (365 days)
- [ ] Monthly aggregation view
- [ ] Color coding: Green (>99%), Yellow (95-99%), Red (<95%)
- [ ] Hover detail: uptime %, offline count, total downtime
- [ ] Click to drill down ke daily detail
- [ ] Export heatmap sebagai image

**Endpoint:**
```python
@router.get("/health/availability-heatmap")
async def availability_heatmap(
    camera_id: Optional[str] = None,
    year: int = datetime.now().year,
    month: Optional[int] = None,
)
```

**UI Components:**
- [ ] Calendar grid component
- [ ] Legend dan color scale
- [ ] Tooltip dengan detail metrics
- [ ] Navigation (prev/next month/year)

---

### 4. Multi-dimensional Filtering & Views
**File terkait:** `app/routes/health.py`, `templates/health_history.html`

**Deskripsi:**
Filtering dan grouping yang lebih powerful untuk eksplorasi data.

**Fitur:**
- [ ] Date range selector: 7d, 30d, 90d, 1y, custom
- [ ] Group by: location, group, status
- [ ] Filter uptime minimum (e.g., uptime < 95%)
- [ ] Filter incident count minimum
- [ ] Filter "Only offline now"
- [ ] Tag-based filtering
- [ ] Save/load filter presets

**Endpoint Modification:**
```python
@router.get("/health/history")
async def health_history(
    q: Optional[str] = "",
    sort: str = "name_asc",
    page: int = 1,
    # Filter baru
    date_range: Literal["7d", "30d", "90d", "1y", "custom"] = "30d",
    custom_start: Optional[date] = None,
    custom_end: Optional[date] = None,
    group_by: Optional[Literal["location", "group", "status"]] = None,
    uptime_min: Optional[float] = None,
    incident_min: Optional[int] = None,
    only_offline_now: bool = False,
    tag_filter: Optional[List[str]] = None,
)
```

**UI Components:**
- [ ] Advanced filter panel (collapsible)
- [ ] Date range picker dengan preset buttons
- [ ] Group by selector dengan visual grouping
- [ ] Filter tags/chips yang aktif
- [ ] Clear all filters button

---

## 🟠 P2 - Medium Priority

### 5. Predictive Health Analytics
**File terkait:** `app/routes/health.py`, `app/utils/predictive_analytics.py`

**Deskripsi:**
Prediksi failure berdasarkan pattern latency dan status changes menggunakan trend analysis.

**Fitur:**
- [ ] Degradation Score (0-100): trend kesehatan device
- [ ] Risk Level classification: Low/Medium/High/Critical
- [ ] Predicted failure probability (7-day forecast)
- [ ] Recommended actions: "Schedule maintenance", "Check network"
- [ ] Pattern detection alerts: "Latency meningkat 40% dalam 5 hari"
- [ ] Anomaly detection untuk behavior tidak normal

**Endpoint:**
```python
@router.get("/health/predictive-analysis/{camera_id}")
async def predictive_analysis(
    camera_id: str, 
    days: int = 30
)
```

**Algorithm:**
```python
# Simple trend analysis
- Linear regression untuk latency trend
- Moving average untuk uptime trend
- Standard deviation threshold untuk anomaly detection
- Threshold-based risk scoring
```

**Database Changes:**
```python
# app/models/predictive_analysis.py
class PredictiveAnalysis(Base):
    __tablename__ = "predictive_analysis"
    id = Column(String(36), primary_key=True, default=generate_uuid)
    camera_id = Column(String(36), ForeignKey("cameras.id"))
    analyzed_at = Column(DateTime(timezone=True))
    degradation_score = Column(Integer)  # 0-100
    risk_level = Column(String(20))  # Low/Medium/High/Critical
    failure_probability_7d = Column(Float)  # 0.0-1.0
    recommended_action = Column(String(255))
    trend_direction = Column(String(20))  # Improving/Stable/Degrading
```

---

### 6. Real-time Alert History Integration
**File terkait:** `app/routes/health.py`, `app/models/camera_daily_stats.py`

**Deskripsi:**
Korelasi antara downtime dan alert yang terkirim untuk accountability dan audit trail.

**Fitur:**
- [ ] Tampilkan jumlah notifikasi terkirim per incident
- [ ] Response time: offline → alert terkirim
- [ ] Acknowledged by: siapa yang acknowledge incident
- [ ] Alert channel tracking: Email/WhatsApp/SMS
- [ ] Escalation timeline

**Database Changes:**
```python
# app/models/camera_daily_stats.py - tambahkan:
alert_count = Column(Integer, default=0)
avg_alert_response_time = Column(Integer)  # seconds
last_alert_sent_at = Column(DateTime(timezone=True))
acknowledged_by = Column(String(100))
acknowledged_at = Column(DateTime(timezone=True))
```

**UI Components:**
- [ ] Alert history timeline
- [ ] Response time metrics
- [ ] Acknowledgment status indicator

---

## 🟢 P3 - Future Enhancements

### 7. Comparative & Benchmarking Analysis
**File terkait:** `app/routes/health.py`

**Deskripsi:**
Bandingkan performa antar periode dan identifikasi improvement/decline.

**Fitur:**
- [ ] Perbandingan uptime % period 1 vs period 2
- [ ] Delta improvements/decline dengan persentase
- [ ] Best improvers & worst decliners ranking
- [ ] Statistical significance indicator
- [ ] Period-over-period trend charts

**Endpoint:**
```python
@router.get("/health/history/compare")
async def compare_periods(
    period1_start: date, period1_end: date,
    period2_start: date, period2_end: date,
    camera_ids: Optional[List[str]] = None
)
```

---

### 8. Network Performance Metrics
**File terkait:** `app/models/camera_daily_stats.py`, `app/routes/health.py`

**Deskripsi:**
Analisis latency trend dan network stability untuk diagnose infrastruktur.

**Fitur:**
- [ ] Latency trend chart data
- [ ] Jitter (latency variance) calculation
- [ ] Peak latency incidents tracking
- [ ] Network stability score
- [ ] Packet loss percentage (jika tersedia)

**Database Changes:**
```python
# app/models/camera_daily_stats.py - tambahkan:
avg_latency = Column(Float)
max_latency = Column(Float)
min_latency = Column(Float)
jitter = Column(Float)  # standard deviation of latency
packet_loss_percentage = Column(Float)
network_stability_score = Column(Integer)  # 0-100
```

**Endpoint:**
```python
@router.get("/health/network-metrics/{camera_id}")
async def network_metrics(camera_id: str, days: int = 30)
```

---

### 9. Incident Timeline & Root Cause Analysis
**File terkait:** `app/routes/health.py`, `templates/incident_timeline.html`

**Deskripsi:**
Timeline visual untuk setiap incident dengan detail lengkap untuk post-incident review.

**Fitur:**
- [ ] Chronological event timeline
- [ ] Event types: latency spike, status change, alert sent
- [ ] Duration calculation per phase
- [ ] Root cause analysis suggestions
- [ ] Evidence collection: logs, metrics snapshot
- [ ] Incident report export

**Endpoint:**
```python
@router.get("/health/incident-timeline/{camera_id}")
async def incident_timeline(
    camera_id: str,
    start_date: date,
    end_date: date
)
```

---

## 🔵 P4 - Enterprise Integration

### 10. Integration API for External Systems
**File terkait:** `app/routes/health.py`, `app/models/webhook_config.py`

**Deskripsi:**
Webhook dan API untuk integrasi dengan sistem enterprise lainnya.

**Fitur:**
- [ ] Webhook configuration untuk external systems
- [ ] Event types: downtime, recovery, sla_breach
- [ ] Prometheus metrics endpoint (`/health/metrics/prometheus`)
- [ ] REST API untuk ITSM integration (ServiceNow, Jira)
- [ ] Grafana datasource plugin
- [ ] PowerBI connector

**Endpoints:**
```python
@router.post("/health/webhooks/configure")
async def configure_webhook(
    url: str,
    events: List[Literal["downtime", "recovery", "sla_breach"]],
    camera_filter: Optional[List[str]] = None,
)

@router.get("/health/metrics/prometheus")
async def prometheus_metrics()
```

**Database:**
```python
class WebhookConfig(Base):
    __tablename__ = "webhook_configs"
    id = Column(String(36), primary_key=True, default=generate_uuid)
    url = Column(String(500), nullable=False)
    events = Column(JSON)  # ["downtime", "recovery", "sla_breach"]
    camera_filter = Column(JSON)  # ["camera_id_1", "camera_id_2"]
    is_active = Column(Boolean, default=True)
    secret_key = Column(String(100))  # untuk webhook signature
    created_at = Column(DateTime(timezone=True))
    last_triggered_at = Column(DateTime(timezone=True))
```

---

## 🚀 Quick Wins (Implementasi Cepat)

Fitur-fitur ini bisa diimplementasikan dengan effort minimal untuk immediate value:

### Q1. Export CSV/Excel dari Health History
**Effort:** 2-4 jam
**File:** `app/routes/health.py`
```python
@router.get("/health/history/export")
async def export_health_history(format: Literal["csv", "excel"] = "csv")
```

### Q2. Date Range Filter (non-hardcoded)
**Effort:** 2-3 jam
**File:** `app/routes/health.py`, `templates/health_history.html`
- Ganti `thirty_days_ago = date.today() - timedelta(days=30)` dengan parameter

### Q3. SLA Summary Card
**Effort:** 3-4 jam
**File:** `templates/health_history.html`
- Tambahkan card di atas tabel: Overall SLA, Devices Pass/Fail

### Q4. Camera Group Filter
**Effort:** 2-3 jam
**File:** `app/routes/health.py`
- Filter berdasarkan `camera.group_id`

---

## 📝 Implementation Notes

### Existing Code References
- **PDF Generation:** Lihat `app/routes/insights.py` (ReportLab implementation)
- **Health Check Logic:** `app/utils/healthcheck.py`
- **Daily Stats:** `app/models/camera_daily_stats.py`
- **Status Change Log:** `app/models/camera_status_change_log.py`
- **Current Health History:** `app/routes/health.py` (line 200-315)

### Libraries Required
```
# Sudah terinstall:
- reportlab (PDF generation)
- sqlalchemy (ORM)
- fastapi (API framework)

# Mungkin perlu ditambahkan:
- pandas (untuk data processing & export)
- openpyxl (untuk Excel export)
- prometheus-client (untuk metrics endpoint)
```

### Database Migration Strategy
1. Buat migration dengan Alembic untuk setiap model baru
2. Backfill data untuk SLA metrics (jika diperlukan)
3. Test migration rollback sebelum deploy

---

## 📊 Success Metrics

Setelah implementasi, track metrics berikut:

- [ ] **User Engagement:** Increase page views health/history 50%
- [ ] **Report Downloads:** X reports generated per week
- [ ] **Enterprise Interest:** X demo requests dengan focus pada SLA/compliance
- [ ] **Customer Satisfaction:** NPS score improvement
- [ ] **Sales Conversion:** Close rate untuk enterprise deals

---

**Last Updated:** 2026-03-09  
**Author:** AI Assistant  
**Status:** Draft - Pending Review
