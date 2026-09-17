# System Architecture

## Overview
B-SNAP is composed of:
- **FastAPI backend** (REST API, authentication, snapshot processing, scheduler)
- **PostgreSQL database** (cameras, users, snapshots)
- **APScheduler** (scheduling background tasks)
- **Leaflet.js frontend** (map view with CCTV markers & snapshots)
- **GoWA** (direct WhatsApp gateway for alerts, reports, and group receivers)
- **n8n** (optional integration layer for external workflows)

## Data Flow
1. Worker (FastAPI worker / scheduler) captures snapshot from camera.
2. Snapshot/image from camera stored in DB.
3. FastAPI exposes snapshot info via API.
4. Frontend renders camera markers on Leaflet map.
5. Notifications can be sent through GoWA directly. n8n can still be used for external workflow orchestration when required.

## Background Jobs

APScheduler runs operational jobs such as scheduled snapshots, health checks, storage checks, record-folder checks, cleanup jobs, WhatsApp reports, and retention policy enforcement. Job Management lists all configured scheduler jobs, including jobs waiting for scheduler reload.

Record-folder monitoring is split into two jobs:

- `record_folder_check`: scans mounted SMB/NVR recording folders and updates current folder status.
- `cleanup_record_checks`: deletes old record-check run/check/event history according to `retention_record_check_days`.

## Flowchart
```{image} _static/B-SNAP_Flowchart.png
:alt: System Architecture
:align: center
```

## Sequence Diagram
```{image} _static/B-SNAP_Sequence.png
:alt: Sequence Diagram
:align: center
```
