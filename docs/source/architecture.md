# System Architecture

## Overview
B-SNAP is composed of:
- **FastAPI backend** (REST API, authentication, snapshot processing, scheduler)
- **PostgreSQL database** (cameras, users, snapshots)
- **APScheduler** (scheduling background tasks)
- **Leaflet.js frontend** (map view with CCTV markers & snapshots)
- **n8n** (integration with WhatsApp bot)

## Data Flow
1. Worker (FastAPI worker / scheduler) captures snapshot from camera.
2. Snapshot/image from camera stored in DB.
3. FastAPI exposes snapshot info via API.
4. Frontend renders camera markers on Leaflet map.
5. Snapshots can be sent to WhatsApp via n8n.

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
