# 🚀 B-Snap Product Backlog

## 📋 Feature & Enhancement
- [ ] **Reboot camera button** – *Priority: Medium*
  - [ ] ACti z317, z911, z97, z38, z41, z41
- [x] **Audit log** - *Priority: High*
- [x] **NVR menu** – *Priority: High*  
  - [x] Display recordings per camera, filtered by date.
- [x] **B‑Snap Assistant: CCTV coordinates** – *Priority: High*  
  - [x] Show camera coordinates similar to Google Maps.
- [x] **Snapshot grouping** – *Priority: Medium*  
  - [x] Group snapshots by division or group.
- [x] **View Last Snapshot and View Realtime Snapshot Button** - *Priority: Medium*
  - [x] Show 2 button in Camera Status menu
- [x] **Store username and or phone number who run command** - *Priority: High*
  - Create table for store username and by phone number.
  - Refactor user management.
  - Logs for command in chatbot.
- [x] **Maps legend display total camera by division only** - *Priority: Medium*
- [x] **Camera uptime** – *Priority: High*
- [x] **Migrate SQLite to PostgreSQL** - *Priority: High*
  - Create migration script
  - Test in dev environment
- [x] **Dockerize B-Snap** - *Priority: Medium*
- [x] **B-Snap Assistant: Error handling core feature** - *Priority: High*
- [x] **Enhanced Security Login** [MFA]
- [x] **Endpoint API protect**
  - API request using token

## 🐞 Bug
- [x] **False online status** – *Priority: High*  
  Camera shows “online” status even when ping is 0 ms.

## 💡 Idea (For Later)
- [ ] **Master camera/switch IP feature**
- [ ] **Engineer photo on check‑in/out**
- [ ] **WA notification on snapshot failure**
- [ ] **Downtime Accumulation**
- [ ] **Check if snapshot is blur**


---
## 📅 Review Log

✅ Completed:
- [x] Logging for apps, snapshot and healthcheck
- [x] No. Asset column for camera
- [x] Log viewer menu
- [x] **NVR menu** – *Priority: High*
- [x] **Snapshot grouping** – *Priority: Medium* 
- [x] Bug: Ping single NVR failed
- [x] Device Status View Snapshots
- [x] **B‑Snap Assistant Bot: CCTV coordinates** – *Priority: High*
- [x] **Integrate NVR to Status** - *Priority: High*
- [x] Change primary key for camera and NVR table to use UUID
- [x] Snapshot deleted sync to DB
- [x] **View Last Snapshot and View Realtime Snapshot Button** - *Priority: Medium*
  - [x] Show 2 button in Camera Status menu
- [x] **Audit log** - *Priority: High*
- [x] **Store username and or phone number who run command** - *Priority: High* -> Done with Audit log
- [x] **Maps legend display total camera by division only** - *Priority: Medium*
- [x] Config not created during setup
- [x] **Add another camera to DB** - *Priority: Medium*
  - [x] Add standalone camera
- [x] **Camera uptime** – *Priority: High*
  - [x] Show uptime for each camera.
- [x] Changed threshold for High Latency from 200ms to 50ms at healthcheck function.
- [x] Refactor Access Control Decorator
- [x] Refactor Video Endpoint
- [x] **Endpoint API protect**
  - [x] API request using token
- [x] **Enhanced Security Login**
  - [x] Implement 2FA (completed 30/06/2025)
- [x] Protect snapshot image URL with token (signed URL)
- [x] **B-Snap Assistant: Error handling core feature** - *Priority: High*
- [x] Limit session login (1 user per session)
- [x] Downtime history
- [x] **Dockerize B-Snap** - *Priority: Medium*
- [x] HTTPS using nginx proxy
  - [x] don't expose B-Snap port to outside

⏳ Ongoing:
- [x] Bug fixing
  - [x] Timestamp di maps
- [x] Timestamp delete snapshot, snapshot in maps converted
- [ ] Double snapshot 
  - [x] Pisah scheduler dari web server
- [x] Change font in docker

🆕 Added:
- [x] Lazy load backend
- [ ] Tamper detection (snapshot blur / dark)