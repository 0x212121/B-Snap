"""Explicit EN/ID formatting for Insights exports, independent of process locale."""

from __future__ import annotations

from datetime import datetime

LABELS = {
    "B-SNAP Executive Report": "Laporan Eksekutif B-SNAP",
    "Executive Summary": "Ringkasan Eksekutif",
    "Executive Summary and Key Metrics Report": "Laporan Ringkasan Eksekutif dan Metrik Utama",
    "B-SNAP Automated Observability System": "Sistem Observabilitas Otomatis B-SNAP",
    "Period": "Periode",
    "All groups": "Semua grup",
    "Camera metrics scope: {group}. System activity metrics are global.": "Cakupan metrik kamera: {group}. Metrik aktivitas sistem dihitung secara global.",
    "Key Highlights": "Sorotan Utama",
    "Top Performers": "Peringkat Teratas",
    "Metric": "Metrik",
    "Current": "Saat Ini",
    "Previous": "Sebelumnya",
    "Change %": "Perubahan %",
    "API Calls": "Panggilan API",
    "Commands": "Perintah",
    "Commands Executed": "Perintah Dieksekusi",
    "Emails": "Email",
    "Email Notifications": "Notifikasi Email",
    "Note: Change % is calculated using daily averages": "Catatan: Perubahan % dihitung berdasarkan rata-rata harian",
    "Activity Trend Index": "Indeks Tren Aktivitas",
    "Increasing": "Meningkat",
    "Decreasing": "Menurun",
    "Stable": "Stabil",
    "increased by {value}%": "meningkat {value}%",
    "decreased by {value}%": "menurun {value}%",
    "remained stable": "stabil",
    "{metric}: {change} compared with the previous period.": "{metric}: {change} dibanding periode sebelumnya.",
    "During {start} to {end}, B-SNAP recorded {api} API calls, {commands} command executions, "
    "and {emails} email notifications. Compared with the previous period, API activity {api_change}, "
    "command executions {command_change}, and email notifications {email_change}. "
    "The activity trend index is {index}/100 ({trend}).": "Selama periode {start} hingga {end}, B-SNAP mencatat {api} panggilan API, "
    "{commands} eksekusi perintah, dan {emails} notifikasi email. Dibandingkan periode "
    "sebelumnya, aktivitas API {api_change}, eksekusi perintah {command_change}, dan "
    "notifikasi email {email_change}. Indeks tren aktivitas adalah {index}/100 ({trend}).",
    "Most executed command: {name}, with {count} executions.": "Perintah yang paling sering dieksekusi: {name}, dengan {count} kali eksekusi.",
    "Camera with the most email notifications: {name}, with {count} deliveries.": "Kamera dengan notifikasi email terbanyak: {name}, dengan {count} pengiriman.",
    "No command data for this period.": "Tidak terdapat data perintah untuk periode ini.",
    "No camera data for this period.": "Tidak terdapat data kamera untuk periode ini.",
    "Camera Operations": "Operasional Kamera",
    "Selected period": "Periode terpilih",
    "Availability": "Ketersediaan",
    "Monitored downtime": "Durasi offline terpantau",
    "Offline transitions": "Transisi offline",
    "No monitoring data": "Tidak ada data pemantauan",
    "No data": "Tidak ada data",
    "hours": "jam",
    "Camera Operations Summary": "Ringkasan Operasional Kamera",
    "API vs Command Logs (Daily Trend)": "Log API vs Perintah (Tren Harian)",
    "Email Notifications (Daily Count)": "Notifikasi Email (Jumlah Harian)",
    "Top 10 Most Executed Commands": "10 Perintah Paling Sering Dieksekusi",
    "Camera Availability and Downtime": "Ketersediaan dan Durasi Offline Kamera",
    "Offline Transitions": "Transisi Offline",
    "Top Cameras by Downtime": "Kamera dengan Durasi Offline Terlama",
    "Camera": "Kamera",
    "Downtime (hours)": "Durasi Offline (jam)",
    "Top 5 Cameras by Email (Global)": "5 Kamera dengan Email Terbanyak (Global)",
    "Top 10 Commands": "10 Perintah Teratas",
    "Command": "Perintah",
    "Count": "Jumlah",
    "Generated automatically by B-SNAP Insights Module - Confidential": "Dibuat otomatis oleh Modul Insights B-SNAP - Rahasia",
}


class InsightsReportLocale:
    """Translate report text and format values for the explicitly selected language."""

    def __init__(self, language: str) -> None:
        self.language = "id" if language == "id" else "en"

    def text(self, message: str, **values: str) -> str:
        """Translate a template before interpolating already escaped values."""
        return (LABELS.get(message, message) if self.language == "id" else message).format(**values)

    def number(self, value: float, decimals: int = 0) -> str:
        """Use EN/ID decimal and thousands separators without changing process locale."""
        result = f"{value:,.{decimals}f}"
        return result.translate(str.maketrans(",.", ".,")) if self.language == "id" else result

    def date(self, value: datetime) -> str:
        """Format a date already converted to the report timezone."""
        months = (
            "Jan Feb Mar Apr Mei Jun Jul Agu Sep Okt Nov Des"
            if self.language == "id"
            else "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec"
        ).split()
        month = months[value.month - 1]
        return (
            f"{value.day:02d} {month} {value.year}"
            if self.language == "id"
            else f"{month} {value.day:02d}, {value.year}"
        )
