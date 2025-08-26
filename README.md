# 📷 B-Snap

**B-Snap** is a self-hosted web-based application to capture, manage, and monitor snapshots from ONVIF-enabled or RTSP-compatible IP cameras.

## 🚀 Features

- 🔐 Built-in 2FA
- 📸 One-click snapshot from dashboard or gallery
- 🗂️ Organized gallery with camera filter and search
- 🧾 API documentation (OpenAPI-style)
- 🔁 Scheduled snapshot & healthcheck (interval configurable)
- 📊 Health monitoring (online/offline status & latency)
- 🌓 Dark mode support
- 🧑‍💻 RESTful APIs with JSON & HTML support
- ⏱ Configurable concurrent snapshot workers
- 🧹 Auto-cleanup old snapshots
- 📦 Upload camera list via CSV
- 🧠 Intelligent fallback (ONVIF → RTSP)
- 📥 Download snapshot with camera name and timestamp overlay

## 📦 Installation

```bash
git clone https://github.com/0x212121/cctv-snapper.git
cd cctv-snapper
pip install -r requirements.txt
uvicorn app.main:app --reload
```
### Web Access
URL: http://localhost:8000


## 🧰 Tech Stack

- **Backend:** FastAPI (Python)
- **Scheduler:** APScheduler
- **ONVIF SDK:** `onvif-zeep`
- **Frontend UI:** Jinja2 + Tailwind CSS
- **Storage:** File system (local)
- **Database:** PostgreSQL

## Menu Permission Role
| No. | Menu Name         | Allowed Roles              | Description                          |
|-----|-------------------|----------------------------|--------------------------------------|
| 1   | Stats             | admin                      | Main dashboard for system overview   | [x]
| 2   | Camera Map        | admin, operator, user      | Manage camera configuration          | [x]
| 3   | Snapshot Gallery  | admin, operator, user (RO) | View and manage snapshots            | [x]
| 4   | Video Gallery     | admin                      | View and manage videos               | [x]
| 5   | Cameras           | admin                      | Camera Management                    | [x]
| 6   | NVRs              | admin                      | NVR Management                       | [x]
| 7   | Users             | admin                      | User Management                      | [x]
| 8   | Device Status     | admin, operator            | Run diagnostics on camera status     | [x]
| 9   | Settings          | admin                      | Variable configuration               | [x]
| 10  | Logs              | admin, operator            | Application Logs                     | [x]
| 11  | Audit Logs        | admin                      | Auditing Logs                        | [x]
| 12  | API Docs          | admin                      | API Documentation                    | [x]


## Build Tailwind CSS
- Install npm
- Install tailwindcss: `npm install -D tailwindcss@3.4.1 postcss autoprefixer`
- run `npm run dev` when you update Tailwind CSS code.