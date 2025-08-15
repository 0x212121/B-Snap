import asyncio
import csv
import json
import logging
import os
import uuid
from datetime import datetime
from io import StringIO
from typing import Optional, Union

from fastapi import APIRouter, Depends, File, Form, HTTPException, Path, UploadFile, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload

from app.core.logging_config import setup_logging
from app.db.database import get_db
from app.models.camera import Camera as DBCamera
from app.models.camera_group import CameraGroup
from app.models.health import CameraHealth
from app.routes.auth import admin_access_required
from app.schemas.camera import CameraUpdatePayload
from app.utils.audit_logger import log_audit
from app.utils.healthcheck import ping_camera_by_id
from app.utils.template_helper import templates
from app.utils.video import record_video_and_save_db

# Dependency injection for router
router = APIRouter(tags=["Cameras"], dependencies=[Depends(get_db), Depends(admin_access_required)])

setup_logging()
logger = logging.getLogger("management")

@router.get("/cameras", response_class=HTMLResponse)
async def manage(request: Request):
    """Renders the main cameras management page."""
    return templates.TemplateResponse("cameras.html", {"request": request})


@router.get("/cameras/data", response_class=HTMLResponse)
async def manage_data(
    request: Request,
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    search: Optional[str] = Query(None),
    per_page: int = Query(10, ge=1)
):
    """
    Fetches camera data for the management table, with search and pagination.
    This endpoint returns an HTML fragment, not the full page.
    """
    logger.debug("Received request for camera data. Page: %s, Search: %s, Per_page: %s", page, search, per_page)

    query = db.query(DBCamera).options(joinedload(DBCamera.group))

    if search:
        logger.debug("Applying search filter for term: '%s'", search)
        search_term = f"%{search}%"
        query = query.join(CameraGroup, DBCamera.group_id == CameraGroup.id, isouter=True).filter(
            or_(
                DBCamera.hostname.ilike(search_term),
                DBCamera.ip.ilike(search_term),
                DBCamera.location.ilike(search_term),
                DBCamera.asset_no.ilike(search_term),
                CameraGroup.name.ilike(search_term),
                DBCamera.status.ilike(search_term)
            )
        )

    query = query.order_by(DBCamera.hostname.asc())

    total = query.count()
    logger.debug("Total cameras found before pagination: %s", total)
    
    cameras = query.offset((page - 1) * per_page).limit(per_page).all()
    total_pages = (total + per_page - 1) // per_page if total > 0 else 1
    logger.debug("Fetched %s cameras for page %s of %s", len(cameras), page, total_pages)

    rows_html = ""
    for cam in cameras:
        gps_loc = f"{cam.latitude}, {cam.longitude}" if cam.latitude is not None or cam.longitude is not None else ""
        rows_html += f"""
            <tr class="border-b dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-700 text-sm text-gray-700 dark:text-gray-300">
                <td class="p-3 font-semibold">{cam.hostname}</td>
                <td class="p-3">{cam.ip}</td>
                <td class="p-3">{cam.port}</td>
                <td class="p-3">{cam.username}</td>
                <td class="p-3">{gps_loc}</td>
                <td class="p-3">{cam.asset_no}</td>
                <td class="p-3">{cam.location or ''}</td>
                <td class="p-3">{cam.group.name if cam.group else ''}</td>
                <td class="p-3">
                    <span class="px-2 py-1 text-xs font-semibold rounded-full {'bg-green-100 text-green-800' if cam.status == 'Active' else 'bg-red-100 text-red-800'}">
                        {'Unknown' if not cam.status else cam.status}
                    </span>
                </td>
                <td class="p-3 space-x-3 whitespace-nowrap">
                    <button onclick="showEditCameraModal('{cam.id}')" class="text-blue-600 dark:text-blue-400 hover:underline font-semibold text-xs">✏️ Edit</button>
                    <form method="post" class="inline" onsubmit="event.preventDefault(); confirmDelete('{cam.id}', '{ cam.hostname }')">
                        <button type="submit" class="text-red-500 dark:text-red-400 hover:underline font-semibold text-xs">🗑️ Delete</button>
                    </form>
                    <button onclick="captureVideo('{cam.id}')" class="text-green-600 dark:text-green-400 hover:underline font-semibold text-xs">🎥 Capture</button>
                    <button onclick='viewSnapshot({json.dumps(cam.hostname)})' class="text-green-600 dark:text-green-400 hover:underline font-semibold text-xs">📸 View Snapshot</button>
                </td>
            </tr>
        """
    
    pagination_html = ""
    if total_pages > 1:
        links = []
        window = 2
        
        if page > 1:
            links.append(f'<a href="#" onclick="event.preventDefault(); loadPage(1)" class="px-3 py-1 bg-gray-200 dark:bg-gray-700 rounded hover:bg-blue-500 dark:hover:bg-blue-600 hover:text-white">First</a>')
            links.append(f'<a href="#" onclick="event.preventDefault(); loadPage({page - 1})" class="px-3 py-1 bg-gray-200 dark:bg-gray-700 rounded hover:bg-blue-500 dark:hover:bg-blue-600 hover:text-white">«</a>')

        if page > window + 2:
            links.append('<span class="px-3 py-1">...</span>')

        for i in range(max(1, page - window), min(total_pages, page + window) + 1):
            if i == page:
                links.append(f'<span class="px-3 py-1 bg-blue-600 text-white rounded font-bold">{i}</span>')
            else:
                links.append(f'<a href="#" onclick="event.preventDefault(); loadPage({i})" class="px-3 py-1 bg-gray-200 dark:bg-gray-700 rounded hover:bg-blue-500 dark:hover:bg-blue-600 hover:text-white">{i}</a>')
        
        if page < total_pages - window - 1:
             links.append('<span class="px-3 py-1">...</span>')

        if page < total_pages:
            links.append(f'<a href="#" onclick="event.preventDefault(); loadPage({page + 1})" class="px-3 py-1 bg-gray-200 dark:bg-gray-700 rounded hover:bg-blue-500 dark:hover:bg-blue-600 hover:text-white">»</a>')
            links.append(f'<a href="#" onclick="event.preventDefault(); loadPage({total_pages})" class="px-3 py-1 bg-gray-200 dark:bg-gray-700 rounded hover:bg-blue-500 dark:hover:bg-blue-600 hover:text-white">Last</a>')
        
        pagination_html = " ".join(links)

    return HTMLResponse(content=f"{rows_html}|||{pagination_html}")


@router.post("/cameras/add_camera", response_class=JSONResponse)
async def add_camera_submit(
    request: Request,
    db: Session = Depends(get_db),
    name: str = Form(...),
    ip: str = Form(...),
    port: Union[int, str, None] = Form(None),
    username: str = Form(...),
    password: str = Form(...),
    latitude: str = Form(None),
    longitude: str = Form(None),
    asset_no: Optional[str] = Form(None),
    location: Optional[str] = Form(None),
    group_name: Optional[str] = Form(None),
    status: str = Form(...),
    is_flipped: bool = Form(False),
    note: Optional[str] = Form(None),
):
    """Handles the form submission to add a new camera."""
    logger.info("Attempting to add new camera with hostname: %s", name)
    try:
        existing_cam = db.query(DBCamera).filter(DBCamera.hostname == name).first()
        if existing_cam:
            logger.warning("Attempted to add a camera that already exists: %s", name)
            return JSONResponse(status_code=409, content={"status": "error", "message": f"Camera '{name}' already exists."})

        lat = float(latitude) if latitude else None
        lon = float(longitude) if longitude else None

        group_id = None
        if group_name:
            logger.debug("Processing group_name: %s", group_name)
            group = db.query(CameraGroup).filter(CameraGroup.name == group_name).first()
            if not group:
                logger.info("Creating new camera group: %s", group_name)
                group = CameraGroup(name=group_name)
                db.add(group)
                db.flush()
            group_id = group.id
        
        port_val = int(port) if port not in [None, ""] else None
        
        new_cam = DBCamera(
            hostname=name, ip=ip, port=port_val, username=username, password=password,
            latitude=lat, longitude=lon, asset_no=asset_no, location=location,
            group_id=group_id, status=status, is_flipped=is_flipped, note=note
        )
        db.add(new_cam)
        db.commit()
        db.refresh(new_cam)
        logger.info("New camera '%s' added successfully with ID: %s", name, new_cam.id)

        log_audit(
            db=db,
            user=request.session.get("user_name", "unknown"),
            action="create_camera",
            target=name,
            ip=request.client.host,
            extra="via Camera Management"
        )
        
        if status != "Deactivated" and status != "Standalone":
            logger.debug("Camera status is '%s', pinging camera for healthcheck.", status)
            ping_camera_by_id(new_cam.id)
        
        return JSONResponse(status_code=201, content={"status": "success", "message": "Camera added successfully!"})

    except Exception as e:
        db.rollback()
        logger.error("Error adding camera '%s'. Error: %s", name, e, exc_info=True)
        return JSONResponse(status_code=500, content={"status": "error", "message": "An internal server error occurred."})


@router.post("/capture_video/{camera_id}")
async def capture_video(request: Request, camera_id: str, db: Session = Depends(get_db)):
    """Triggers an async video recording from a specific camera."""
    try:
        camera = db.query(DBCamera).filter(DBCamera.id == camera_id).first()
        if not camera:
            logger.warning("Attempted to capture video from non-existent camera ID: %s", camera_id)
            return {"status": "error", "message": "Camera not found"}
        
        logger.info("Triggering 5s video capture for camera '%s' (%s).", camera.hostname, camera_id)
        asyncio.create_task(record_video_and_save_db(request, camera_id, 5))
        return {"status": "success", "message": f"🎥 Recording 5s video from {camera.hostname}..."}
    finally:
        db.close()


@router.post("/cameras/edit_camera/{camera_id}", status_code=200)
async def edit_camera_submit(
    request: Request,
    camera_id: str = Path(...),
    db: Session = Depends(get_db),
    name: str = Form(...),
    ip: str = Form(...),
    port: Union[int, str, None] = Form(None),
    username: str = Form(...),
    password: Optional[str] = Form(None),
    latitude: Optional[str] = Form(None),
    longitude: Optional[str] = Form(None),
    asset_no: Optional[str] = Form(None),
    location: Optional[str] = Form(None),
    group_name: Optional[str] = Form(None),
    is_flipped: bool = Form(False),
    status: str = Form(...),
    note: Optional[str] = Form(None),
):
    """Handles the form submission to edit an existing camera."""
    logger.info("Attempting to edit camera with ID: %s", camera_id)
    cam = db.query(DBCamera).filter(DBCamera.id == camera_id).first()
    if not cam:
        logger.warning("Attempted to edit non-existent camera with ID: %s", camera_id)
        raise HTTPException(status_code=404, detail="Camera not found")

    # Save 'before' state for audit logging
    old_password = cam.password
    before = {
        "hostname": cam.hostname, "ip": cam.ip, "port": cam.port, "username": cam.username,
        "latitude": cam.latitude, "longitude": cam.longitude, "asset_no": cam.asset_no,
        "location": cam.location, "group_id": cam.group_id, "status": cam.status,
        "is_flipped": cam.is_flipped, "note": cam.note
    }

    try:
        lat = float(latitude) if latitude and latitude.strip() else None
        lon = float(longitude) if longitude and longitude.strip() else None

        if cam.hostname != name:
            logger.info("Hostname change detected for camera '%s' from '%s' to '%s'", cam.id, cam.hostname, name)
            existing = db.query(DBCamera).filter(DBCamera.hostname.ilike(name), DBCamera.id != camera_id).first()
            if existing:
                logger.warning("Hostname conflict: another camera with name '%s' already exists.", name)
                raise HTTPException(status_code=409, detail=f"Another camera with name '{name}' already exists.")

        cam.previous_name = cam.hostname if name != cam.hostname else None
        cam.previous_latitude = cam.latitude if lat != cam.latitude else None
        cam.previous_longitude = cam.longitude if lon != cam.longitude else None

        port_val = int(port) if port not in [None, ""] else None
        cam.hostname, cam.ip, cam.port, cam.username = name, ip, port_val, username
        cam.latitude, cam.longitude, cam.asset_no = lat, lon, asset_no
        cam.location, cam.status = location, status
        cam.is_flipped = is_flipped
        cam.note = note

        if password:
            cam.password = password

        if group_name:
            group = db.query(CameraGroup).filter(CameraGroup.name == group_name).first()
            if not group:
                logger.info("Creating new camera group '%s' during edit.", group_name)
                group = CameraGroup(name=group_name)
                db.add(group)
                db.flush()
            cam.group_id = group.id
        else:
            cam.group_id = None

        db.commit()
        db.refresh(cam)
        logger.info("Camera '%s' updated successfully.", name)

        # Generate 'after' state for audit log
        after = {
            "hostname": cam.hostname, "ip": cam.ip, "port": cam.port, "username": cam.username,
            "latitude": cam.latitude, "longitude": cam.longitude, "asset_no": cam.asset_no,
            "location": cam.location, "group_id": cam.group_id, "status": cam.status,
            "is_flipped": cam.is_flipped, "note": cam.note
        }
        
        changes = []
        for key in before:
            if before[key] != after[key]:
                changes.append(f"- {key}: '{before[key]}' -> '{after[key]}'")
        if password and password != old_password:
            changes.append("- password: [CHANGED]")
            
        audit_extra = "\n".join(changes) if changes else "No changes detected."

        log_audit(
            db=db,
            user=request.session.get("user_name", "unknown"),
            action="update_camera",
            target=cam.hostname,
            ip=request.client.host,
            extra=audit_extra
        )
        
        if cam.status == "Deactivated":
            health = db.query(CameraHealth).filter(CameraHealth.id == cam.id).first()
            if health:
                new_health = CameraHealth(id=cam.id, status="Offline", checked=datetime.now())
                db.merge(new_health)
                db.commit()
                logger.info("Health record for camera '%s' set to Offline due to deactivation.", cam.hostname)
        
        return {"status": "success", "message": "Camera updated successfully"}

    except ValueError:
        logger.warning("Invalid format for latitude or longitude in edit request.")
        raise HTTPException(status_code=400, detail="Invalid format for latitude or longitude.")
    except Exception as e:
        db.rollback()
        logger.error("Error updating camera '%s'. Error: %s", camera_id, e, exc_info=True)
        raise HTTPException(status_code=500, detail="An internal server error occurred.")


@router.get("/api/camera/{camera_id}", response_class=JSONResponse)
async def get_camera_details(request: Request, camera_id: str, db: Session = Depends(get_db)):
    """Fetches a single camera's details by ID."""
    logger.debug("Fetching details for camera ID: %s", camera_id)
    camera = db.query(DBCamera).options(joinedload(DBCamera.group)).filter(DBCamera.id == camera_id).first()
    if not camera:
        logger.warning("Details for camera ID '%s' not found.", camera_id)
        raise HTTPException(status_code=404, detail="Camera not found")
        
    return {
        "id": camera.id,
        "hostname": camera.hostname,
        "previous_name": camera.previous_name if camera else '',
        "ip": camera.ip,
        "port": camera.port,
        "username": camera.username,
        "password": camera.password,
        "latitude": camera.latitude,
        "longitude": camera.longitude,
        "asset_no": camera.asset_no,
        "location": camera.location,
        "status": camera.status,
        "is_flipped": camera.is_flipped,
        "group": {"name": camera.group.name} if camera.group else None,
        "note": camera.note
    }


@router.get("/api/camera_groups")
async def get_camera_groups(request: Request, db: Session = Depends(get_db)):
    """Fetches a list of all camera groups."""
    groups = db.query(CameraGroup).all()
    logger.debug("Fetched %s camera groups.", len(groups))
    return [{"id": g.id, "name": g.name} for g in groups]


@router.post("/cameras/delete/{camera_id}", response_class=JSONResponse)
async def delete_camera(request: Request, camera_id: str, db: Session = Depends(get_db)):
    """
    Deletes a camera by its ID, and also deletes all associated files
    (snapshots and videos) from the file system.
    """
    logger.info("Attempting to delete camera with ID: %s", camera_id)
    
    # Load the camera object along with its related videos and snapshots
    cam = db.query(DBCamera).options(
        joinedload(DBCamera.snapshots),
        joinedload(DBCamera.videos)
    ).filter(DBCamera.id == camera_id).first()
    
    if not cam:
        logger.warning("Attempted to delete non-existent camera ID: %s", camera_id)
        return JSONResponse(status_code=404, content={"status": "error", "message": "Camera not found"})

    try:
        SNAPSHOT_PATH = get_snapshot_directory()
        VIDEO_PATH = get_video_directory()
        
        logger.info("Deleting %s associated snapshot files for camera '%s'...", len(cam.snapshots), cam.hostname)
        for snapshot in cam.snapshots:
            file_path = os.path.join(SNAPSHOT_PATH, snapshot.filename)
            if os.path.exists(file_path):
                os.remove(file_path)
                logger.debug("Deleted snapshot file: %s", file_path)

        # 2. Hapus file videos
        logger.info("Deleting %s associated video files for camera '%s'...", len(cam.videos), cam.hostname)
        for video in cam.videos:
            file_path = os.path.join(VIDEO_PATH, video.filename)
            if os.path.exists(file_path):
                os.remove(file_path)
                logger.debug("Deleted video file: %s", file_path)

        # --- HAPUS DATA DARI DATABASE ---
        # Karena kita sudah menggunakan `cascade="all, delete-orphan"` di model `Camera`,
        # cukup panggil `db.delete(cam)`. SQLAlchemy akan menangani penghapusan
        # semua relasi terkait di database.
        db.delete(cam)
        db.commit()
        logger.info("Camera '%s' and all associated database records deleted successfully.", cam.hostname)

        log_audit(
            db=db,
            user=request.session.get("user_name", "unknown"),
            action="delete_camera",
            target=cam.hostname,
            ip=request.client.host,
            extra=f"via Camera Management\nIP: {cam.ip}"
        )
        return JSONResponse(status_code=200, content={"status": "success", "message": f"Camera '{cam.hostname}' and all associated files deleted."})

    except Exception as e:
        db.rollback()
        logger.error("Failed to delete camera '%s' or its files. Error: %s", camera_id, e, exc_info=True)
        # Penting: Rollback hanya membatalkan operasi database, bukan penghapusan file.
        return JSONResponse(status_code=500, content={"status": "error", "message": "Failed to delete camera and its files."})


@router.post("/cameras/upload_csv")
async def upload_csv(request: Request, db: Session = Depends(get_db), file: UploadFile = File(...)):
    """Handles CSV file upload to add/update cameras in bulk."""
    logger.info("Starting CSV upload process.")
    contents = await file.read()
    failed_rows = []
    success_ids = []
    
    try:
        csv_text = contents.decode("utf-8-sig")
        detected_delimiter = detect_csv_delimiter(csv_text)
        csv_reader = csv.DictReader(StringIO(csv_text), delimiter=detected_delimiter)
        logger.debug("Detected CSV delimiter: '%s'", detected_delimiter)

        for i, row in enumerate(csv_reader):
            row_num = i + 1
            logger.debug("Processing row %s: %s", row_num, row)
            try:
                hostname = row.get("hostname", "").strip()
                if not hostname:
                    logger.warning("Skipping row %s due to empty hostname.", row_num)
                    continue

                group_name = row.get("group_name", "").strip()
                group_id = None
                if group_name:
                    group = db.query(CameraGroup).filter(CameraGroup.name == group_name).first()
                    if not group:
                        logger.info("Creating new group '%s' from CSV import.", group_name)
                        group = CameraGroup(name=group_name)
                        db.add(group)
                        db.flush()
                    group_id = group.id

                existing_cam = db.query(DBCamera).filter_by(hostname=hostname).first()

                camera_data = {
                    "ip": row.get("ip", "").strip(),
                    "port": int(row["port"]) if row.get("port") else None,
                    "username": row.get("username", "").strip(),
                    "password": row.get("password", "").strip(),
                    "latitude": float(row["latitude"]) if row.get("latitude", "").strip() else None,
                    "longitude": float(row["longitude"]) if row.get("longitude", "").strip() else None,
                    "asset_no": row.get("asset_no", "").strip(),
                    "location": row.get("location", "").strip(),
                    "status": row.get("status", "Active").strip() or "Active",
                    "is_flipped": str(row.get("is_flipped", "")).strip().lower() in ["1", "true", "yes"],
                    "group_id": group_id
                }

                if existing_cam:
                    logger.debug("Updating existing camera '%s' via CSV.", hostname)
                    for key, value in camera_data.items():
                        setattr(existing_cam, key, value)
                    db.add(existing_cam)
                    success_ids.append(existing_cam.id)
                    log_audit(db=db, user=request.session["user_name"], action="update_camera",
                              target=hostname, ip=request.client.host, extra="via CSV Upload")
                else:
                    logger.debug("Creating new camera '%s' via CSV.", hostname)
                    camera_data['hostname'] = hostname
                    camera_data['id'] = row.get("id", "").strip() or str(uuid.uuid4())
                    new_cam = DBCamera(**camera_data)
                    db.add(new_cam)
                    db.flush()
                    success_ids.append(new_cam.id)
                    log_audit(db=db, user=request.session["user_name"], action="create_camera",
                              target=hostname, ip=request.client.host, extra="via CSV Upload")

            except Exception as e:
                failed_rows.append(row.get("hostname", "N/A"))
                db.rollback()
                logger.error("Failed to process row %s (hostname: %s). Error: %s", row_num, hostname, e, exc_info=True)

        db.commit()
        logger.info("CSV upload finished. Total successes: %s, Total failures: %s.", len(success_ids), len(failed_rows))

    except Exception as e:
        db.rollback()
        logger.error("Critical error during CSV upload process. Error: %s", e, exc_info=True)
    finally:
        db.close()
    
    return RedirectResponse(url="/cameras", status_code=303)


@router.get("/cameras/export_csv")
async def export_csv(request: Request, db: Session = Depends(get_db)):
    """Exports all camera data to a CSV file."""
    logger.info("Starting CSV export process.")
    cameras = db.query(DBCamera).options(joinedload(DBCamera.group)).all()
    db.close()
    logger.debug("Fetched %s cameras for export.", len(cameras))

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow([
        "id", "hostname", "ip", "port", "username", "password",
        "latitude", "longitude", "group_name", "asset_no", "location", "status",
        "previous_name", "previous_latitude", "previous_longitude", "is_flipped", "note"
    ])

    for cam in cameras:
        writer.writerow([
            cam.id, cam.hostname, cam.ip, cam.port, cam.username, cam.password,
            cam.latitude, cam.longitude, cam.group.name if cam.group else "",
            cam.asset_no, cam.location, cam.status, cam.previous_name,
            cam.previous_latitude, cam.previous_longitude, cam.is_flipped, cam.note
        ])

    output.seek(0)
    log_audit(
        db=db,
        user=request.session.get("user_name", "unknown"),
        action="export_camera_csv",
        target="Cameras Export",
        ip=request.client.host,
        extra="via Camera Management"
    )
    logger.info("CSV export completed successfully.")
    return StreamingResponse(
        output,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=cameras.csv"}
    )


def detect_csv_delimiter(csv_content: str):
    """Detects the delimiter (comma or semicolon) in a CSV string."""
    if not csv_content:
        return ','
    
    first_line = csv_content.splitlines()[0]
    comma_count = first_line.count(',')
    semicolon_count = first_line.count(';')

    if comma_count > 0 and semicolon_count == 0:
        return ','
    elif semicolon_count > 0 and comma_count == 0:
        return ';'
    elif comma_count > 0 and semicolon_count > 0:
        return ',' if comma_count > semicolon_count else ';'
    
    return ','


@router.post("/api/n8n/update-camera", response_class=JSONResponse)
async def update_camera_n8n(
    payload: CameraUpdatePayload, # FastAPI akan otomatis melakukan validasi di sini
    request: Request,
    db: Session = Depends(get_db)
):
    """
    API endpoint to update camera info via n8n by hostname.
    """
    logger.info("Received API request from n8n to update camera. Hostname: %s", payload.hostname)
    
    # Validasi required fields sudah tidak perlu, Pydantic sudah melakukannya.
    # Misalnya, `payload.hostname` pasti ada dan bertipe `str`.

    cam = db.query(DBCamera).filter(DBCamera.hostname == payload.hostname).first()
    if not cam:
        logger.warning("Camera not found for hostname: %s", payload.hostname)
        raise HTTPException(status_code=404, detail="Camera not found")

    # Save 'before' state for audit
    before = {
        "hostname": cam.hostname, "ip": cam.ip, "port": cam.port,
        "username": cam.username, "status": cam.status, "group_id": cam.group_id
    }
    
    try:
        # Langsung gunakan atribut dari objek payload yang sudah divalidasi
        cam.username = payload.username
        cam.password = payload.password

        # Menggunakan `if payload.ip is not None` adalah cara yang lebih eksplisit
        # untuk menangani nilai opsional.
        if payload.ip is not None:
            cam.ip = payload.ip
            logger.debug("Updating IP to: %s", cam.ip)

        if payload.port is not None:
            cam.port = payload.port
            logger.debug("Updating port to: %s", cam.port)

        if payload.status is not None:
            cam.status = payload.status
            logger.debug("Updating status to: %s", cam.status)

        if payload.group_name is not None:
            group_name = payload.group_name
            group = db.query(CameraGroup).filter(CameraGroup.name == group_name).first()
            if not group:
                logger.info("Creating new group '%s' via n8n request.", group_name)
                group = CameraGroup(name=group_name)
                db.add(group)
                db.flush()
            cam.group_id = group.id
            logger.debug("Updating group to: %s", group_name)
        else:
            # Opsional: Jika group_name adalah None, set group_id menjadi None
            cam.group_id = None


        db.commit()
        db.refresh(cam)
        logger.info("Camera '%s' updated successfully via n8n request.", cam.hostname)

        # Generate 'after' state for audit
        after = {
            "hostname": cam.hostname, "ip": cam.ip, "port": cam.port,
            "username": cam.username, "status": cam.status, "group_id": cam.group_id
        }

        log_audit(
            db=db,
            user=request.session.get("user_name", "WhatsApp-bot"),
            action="update_camera",
            target=cam.hostname,
            ip=request.client.host,
            extra=json.dumps({"before": before, "after": after}, indent=2)
        )

        return {"status": "success", "message": f"Camera '{cam.hostname}' updated successfully"}

    except Exception as e:
        db.rollback()
        logger.error("Error updating camera '%s' from n8n. Error: %s", payload.hostname, e, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")

def get_snapshot_directory() -> str:
    """Returns the absolute path to the snapshot directory."""
    current_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(current_dir, "..", "static", "snapshots"))

def get_video_directory() -> str:
    """Returns the absolute path to the video directory."""
    current_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(current_dir, "..", "static", "videos"))