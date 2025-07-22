import asyncio
import csv
from datetime import datetime
import json
import logging
import traceback
from typing import Optional, Union
import uuid
from fastapi import Depends, File, HTTPException, Path, UploadFile, APIRouter
from io import StringIO
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.responses import RedirectResponse
from fastapi import Request, Query, Form
from fastapi.templating import Jinja2Templates
from sqlalchemy import asc, or_
from app.core.logging_config import setup_logging
from app.models_sql import Camera as DBCamera, CameraGroup, CameraHealth, User
from app.db.database import SessionLocal, get_db
from app.core.config import get_config
from app.routes.auth import admin_access_required
from app.utils.audit_logger import log_audit
from app.utils.health_check import ping_camera_by_id
from app.utils.video import record_video_and_save_db
from app.utils.decorators import admin_required
from sqlalchemy.orm import Session, joinedload
from fastapi.responses import StreamingResponse
import io

router = APIRouter(tags=["Cameras"])

from app.utils.template_helper import templates

setup_logging()
logger = logging.getLogger("management")

@router.get("/cameras", response_class=HTMLResponse)
async def manage(request: Request, db: Session = Depends(get_db), current_admin: User = Depends(admin_access_required)):
    return templates.TemplateResponse("cameras.html", {"request": request})


@router.get("/cameras/data", response_class=HTMLResponse)
async def manage_data(
    request: Request, 
    page: int = Query(1, ge=1),
    search: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required)
):
    per_page = get_config('items_per_page', 10)
    
    query = db.query(DBCamera).options(joinedload(DBCamera.group))

    if search:
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
    cameras = query.offset((page - 1) * per_page).limit(per_page).all()
    total_pages = (total + per_page - 1) // per_page if total > 0 else 1
    
    rows_html = ""
    for cam in cameras:
        if cam.latitude is not None or cam.longitude is not None:
            gps_loc = f"{cam.latitude}, {cam.longitude}"
        else:
            gps_loc = ""
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
    
    # --- LOGIKA PAGINASI YANG DIPERBAIKI ---
    pagination_html = ""
    if total_pages > 1:
        links = []
        window = 2  # Jumlah halaman di sekitar halaman saat ini
        
        # Link 'First' dan 'Prev'
        if page > 1:
            links.append(f'<a href="#" onclick="event.preventDefault(); loadPage(1)" class="px-3 py-1 bg-gray-200 dark:bg-gray-700 rounded hover:bg-blue-500 dark:hover:bg-blue-600 hover:text-white">First</a>')
            links.append(f'<a href="#" onclick="event.preventDefault(); loadPage({page - 1})" class="px-3 py-1 bg-gray-200 dark:bg-gray-700 rounded hover:bg-blue-500 dark:hover:bg-blue-600 hover:text-white">«</a>')

        # Tampilkan ellipsis jika perlu
        if page > window + 2:
            links.append('<span class="px-3 py-1">...</span>')

        # Tampilkan nomor halaman
        for i in range(max(1, page - window), min(total_pages, page + window) + 1):
            if i == page:
                links.append(f'<span class="px-3 py-1 bg-blue-600 text-white rounded font-bold">{i}</span>')
            else:
                links.append(f'<a href="#" onclick="event.preventDefault(); loadPage({i})" class="px-3 py-1 bg-gray-200 dark:bg-gray-700 rounded hover:bg-blue-500 dark:hover:bg-blue-600 hover:text-white">{i}</a>')
        
        # Tampilkan ellipsis jika perlu
        if page < total_pages - window - 1:
             links.append('<span class="px-3 py-1">...</span>')

        # Link 'Next' dan 'Last'
        if page < total_pages:
            links.append(f'<a href="#" onclick="event.preventDefault(); loadPage({page + 1})" class="px-3 py-1 bg-gray-200 dark:bg-gray-700 rounded hover:bg-blue-500 dark:hover:bg-blue-600 hover:text-white">»</a>')
            links.append(f'<a href="#" onclick="event.preventDefault(); loadPage({total_pages})" class="px-3 py-1 bg-gray-200 dark:bg-gray-700 rounded hover:bg-blue-500 dark:hover:bg-blue-600 hover:text-white">Last</a>')
        
        pagination_html = " ".join(links)

    return HTMLResponse(content=f"{rows_html}|||{pagination_html}")


@router.post("/cameras/add_camera", response_class=JSONResponse)
async def add_camera_submit(
    request: Request,
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
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required)
):
    
    try:
        existing_cam = db.query(DBCamera).filter(DBCamera.hostname == name).first()
        if existing_cam:
            return JSONResponse(status_code=409, content={"status": "error", "message": f"Camera '{name}' already exists."})

        lat = float(latitude) if latitude else None
        lon = float(longitude) if longitude else None

        group_id = None
        if group_name:
            group = db.query(CameraGroup).filter(CameraGroup.name == group_name).first()
            if not group:
                group = CameraGroup(name=group_name)
                db.add(group)
                db.flush()
            group_id = group.id
        
        port_val = int(port) if port not in [None, ""] else None
        port = port_val
        new_cam = DBCamera(
            hostname=name, ip=ip, port=port, username=username, password=password,
            latitude=lat, longitude=lon, asset_no=asset_no, location=location,
            group_id=group_id, status=status
        )
        db.add(new_cam)

        log_audit(
            db=db,
            user=request.session["user_name"],
            action="create_camera",
            target=name,
            ip=request.client.host,
            extra="via Camera Management"
        )
        # Ping camera and add to healthcheck after created
        if status != "Deactivated" and status != "Standalone":
            ping_camera_by_id(new_cam.id)
        
        return JSONResponse(status_code=201, content={"status": "success", "message": "Camera added successfully!"})

    except Exception as e:
        db.rollback()
        logger.warning("Error adding camera: %s", e)
        return JSONResponse(status_code=500, content={"status": "error", "message": "An internal server error occurred."})


@router.post("/capture_video/{camera_id}")
async def capture_video(request: Request, camera_id: str, current_admin: User = Depends(admin_access_required)):
    # Dibiarkan async karena memanggil asyncio.create_task
    db = SessionLocal()
    try:
        camera = db.query(DBCamera).filter(DBCamera.id == camera_id).first()
        if not camera:
            return {"status": "error", "message": "Camera not found"}
        
        asyncio.create_task(record_video_and_save_db(request, camera_id, 5))
        return {"status": "success", "message": f"🎥 Recording 5s video from {camera.hostname}..."}
    finally:
        db.close()


@router.post("/cameras/edit_camera/{camera_id}", status_code=200)
async def edit_camera_submit(
    request: Request,
    camera_id: str = Path(...),
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
    status: str = Form(...),
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required)
):
    cam = db.query(DBCamera).filter(DBCamera.id == camera_id).first()
    if not cam:
        raise HTTPException(status_code=404, detail="Camera not found")

    # ⏮️ Simpan data sebelum diubah
    before = {
        "hostname": cam.hostname,
        "ip": cam.ip,
        "port": cam.port,
        "username": cam.username,
        "latitude": cam.latitude,
        "longitude": cam.longitude,
        "asset_no": cam.asset_no,
        "location": cam.location,
        "group_id": cam.group_id,
        "status": cam.status
    }

    try:
        # Manual conversion and validation
        lat = float(latitude) if latitude and latitude.strip() else None
        lon = float(longitude) if longitude and longitude.strip() else None

        if cam.hostname != name:
            existing = db.query(DBCamera).filter(DBCamera.hostname == name, DBCamera.id != camera_id).first()
            if existing:
                raise HTTPException(status_code=409, detail=f"Another camera with name '{name}' already exists.")
        if cam.longitude != longitude or cam.latitude != latitude:
            cam.previous_latitude, cam.previous_longitude = cam.latitude, cam.longitude
        if name != cam.hostname:
            cam.previous_name = cam.hostname

        port_val = int(port) if port not in [None, ""] else None
        cam.port = port_val
        cam.hostname, cam.ip, cam.port, cam.username = name, ip, port, username
        cam.latitude, cam.longitude, cam.asset_no = lat, lon, asset_no
        cam.location, cam.status = location, status

        if password:
            cam.password = password

        if group_name:
            group = db.query(CameraGroup).filter(CameraGroup.name == group_name).first()
            if not group:
                group = CameraGroup(name=group_name)
                db.add(group)
                db.flush()
            cam.group_id = group.id
        else:
            cam.group_id = None

        db.commit()

        # ✅ Simpan data setelah diubah
        after = {
            "hostname": cam.hostname,
            "ip": cam.ip,
            "port": cam.port,
            "username": cam.username,
            "latitude": cam.latitude,
            "longitude": cam.longitude,
            "asset_no": cam.asset_no,
            "location": cam.location,
            "group_id": cam.group_id,
            "status": cam.status
        }

        # 📝 Audit log
        log_audit(
            db=db,
            user=request.session.get("user_name", "unknown"),
            action="update_camera",
            target=cam.hostname,
            ip=request.client.host,
            extra=json.dumps({
                "before": before,
                "after": after
            }, indent=2)
        )

        if cam.status == "Deactivated":
            print(f"Masuk if val_status")
            health = db.query(CameraHealth).filter(CameraHealth.id == cam.id).first()
    
            if health:
                new_health = CameraHealth(
                    id=cam.id,
                    status="Offline",
                    checked=datetime.now(),
                )
                db.merge(new_health)
                db.commit()
                logger.info("✅ Health record created for %s", cam.hostname)
            else:
                logger.info("ℹ️ Health record already exists for %s", cam.hostname)

        return {"status": "success", "message": "Camera updated successfully"}

    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid format for latitude or longitude.")
    except Exception as e:
        db.rollback()
        logger.warning("Error updating camera: %s", e)
        raise HTTPException(status_code=500, detail="An internal server error occurred.")


@router.get("/api/camera/{camera_id}", response_class=JSONResponse)
async def get_camera_details(request: Request, camera_id: str, db: Session = Depends(get_db), current_admin: User = Depends(admin_access_required)):
    camera = db.query(DBCamera).options(joinedload(DBCamera.group)).filter(DBCamera.id == camera_id).first()
    if not camera:
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
        "group": {"name": camera.group.name} if camera.group else None
    }


@router.get("/api/camera_groups")
async def get_camera_groups(request: Request, db: Session = Depends(get_db), current_admin: User = Depends(admin_access_required)):
    groups = db.query(CameraGroup).all()
    return [{"id": g.id, "name": g.name} for g in groups]


@router.post("/cameras/{camera_id}/delete", response_class=JSONResponse)
async def delete_camera(request: Request, camera_id: str, db: Session = Depends(get_db), current_admin: User = Depends(admin_access_required)):
    cam = db.query(DBCamera).filter(DBCamera.id == camera_id).first()
    if not cam:
        return JSONResponse(status_code=404, content={"status": "error", "message": "Camera not found"})

    try:
        db.delete(cam)
        db.commit()

        log_audit(
            db=db,
            user=request.session.get("user_name", "unknown"),
            action="delete_camera",
            target=cam.hostname,
            ip=request.client.host,
            extra=f"via Camera Management\nIP: {cam.ip}"
        )
        return JSONResponse(status_code=200, content={"status": "success", "message": f"Camera '{cam.hostname}' deleted."})
    except Exception as e:
        db.rollback()
        print(f"❌ Delete error: {e}")
        traceback.print_exc()
        return JSONResponse(status_code=500, content={"status": "error", "message": "Failed to delete camera."})


@router.post("/cameras/upload_csv")
async def upload_csv(request: Request, file: UploadFile = File(...), current_admin: User = Depends(admin_access_required)):
    contents = await file.read()
    db: Session = SessionLocal()
    failed_rows = []
    success_ids = []
    try:
        csv_text = contents.decode("utf-8-sig")
        detected_delimiter = detect_csv_delimiter(csv_text)
        csv_reader = csv.DictReader(StringIO(csv_text), delimiter=detected_delimiter)

        for row in csv_reader:
            try:
                hostname = row.get("hostname", "").strip()
                if not hostname:
                    continue

                # Process group name from CSV
                group_name = row.get("group_name", "").strip()
                group_id = None
                if group_name:
                    group = db.query(CameraGroup).filter(CameraGroup.name == group_name).first()
                    if not group:
                        # If the group does not exist, create it
                        group = CameraGroup(name=group_name)
                        db.add(group)
                        db.flush() # Use flush to get the new group's ID
                    group_id = group.id

                # Check if camera exists
                existing_cam = db.query(DBCamera).filter_by(hostname=hostname).first()

                # Prepare camera data from the CSV row
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
                    "group_id": group_id
                }

                if existing_cam:
                    # Update existing camera
                    for key, value in camera_data.items():
                        setattr(existing_cam, key, value)
                    db.add(existing_cam)
                    success_ids.append(existing_cam.id)
                    log_audit(
                        db=db,
                        user=request.session["user_name"],
                        action="update_camera",
                        target=hostname,
                        ip=request.client.host,
                        extra="via CSV Upload"
                    )
                else:
                    # Create a new camera
                    camera_data['hostname'] = hostname
                    camera_data['id'] = row.get("id", "").strip() or str(uuid.uuid4())
                    new_cam = DBCamera(**camera_data)
                    db.add(new_cam)
                    db.flush() # Flush to get the new camera's ID
                    success_ids.append(new_cam.id)
                    log_audit(
                        db=db,
                        user=request.session["user_name"],
                        action="create_camera",
                        target=hostname,
                        ip=request.client.host,
                        extra="via CSV Upload"
                    )

            except Exception as e:
                failed_rows.append(row.get("hostname", "N/A"))
                db.rollback() # Rollback the specific row's transaction

        db.commit()

    except Exception as e:
        db.rollback()
        logger.error("Error during CSV upload process: %s", e)
    finally:
        db.close()

    # You can add logic here to inform the user about successes and failures
    # For example, by using flash messages if your templating engine supports it.
    
    return RedirectResponse(url="/cameras", status_code=303) # Use 303 for POST-redirect-GET pattern


@router.get("/cameras/export_csv")
async def export_csv(request: Request, current_admin: User = Depends(admin_access_required)):
    db = SessionLocal()
    
    # Eagerly load the group relationship to prevent lazy loading issues.
    cameras = db.query(DBCamera).options(joinedload(DBCamera.group)).all()
    db.close()

    output = io.StringIO()
    writer = csv.writer(output)

    # Update the CSV header to use 'group_name' instead of 'group_id'.
    writer.writerow([
        "id", "hostname", "ip", "port", "username", "password",
        "latitude", "longitude", "group_name", "asset_no", "location", "status",
        "previous_name", "previous_latitude", "previous_longitude"
    ])

    # Write the data rows with the group name.
    for cam in cameras:
        writer.writerow([
            cam.id,
            cam.hostname,
            cam.ip,
            cam.port,
            cam.username,
            cam.password,
            cam.latitude,
            cam.longitude,
            cam.group.name if cam.group else "", # Use group name, provide empty string if no group
            cam.asset_no,
            cam.location,
            cam.status,
            cam.previous_name,
            cam.previous_latitude,
            cam.previous_longitude,
        ])

    output.seek(0)
    log_audit(
        db=db,
        user=request.session["user_name"],
        action="export_camera_csv",
        target="Cameras Export",
        ip=request.client.host,
        extra="via Camera Management"
    )
    return StreamingResponse(
        output,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=cameras.csv"}
    )


def detect_csv_delimiter(csv_content: str):
    """Detects the delimiter (comma or semicolon) in a CSV string."""
    if not csv_content:
        return ',' # Default if empty

    # Take the first line (header) for analysis
    first_line = csv_content.splitlines()[0]

    # Count the occurrences of comma and semicolon
    comma_count = first_line.count(',')
    semicolon_count = first_line.count(';')

    if comma_count > 0 and semicolon_count == 0:
        return ','
    elif semicolon_count > 0 and comma_count == 0:
        return ';'
    elif comma_count > 0 and semicolon_count > 0:
        # If both exist, try to guess which is more dominant
        # Or you can set a priority, e.g. comma
        if comma_count > semicolon_count:
            return ','
        else:
            return ';'
    return ',' # Default if no clear delimiter or both are 0


@router.post("/api/n8n/update-camera", response_class=JSONResponse)
async def update_camera_n8n(
    payload: dict,
    request: Request,
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required)
):
    """
    Update camera info via n8n by hostname. Admin only.
    Required fields: hostname, username, password
    Optional: ip, port, status, group_name
    """
    required_fields = ["hostname", "username", "password"]
    for field in required_fields:
        if field not in payload or not payload[field].strip():
            raise HTTPException(status_code=400, detail=f"{field} is required")

    cam = db.query(DBCamera).filter(DBCamera.hostname == payload["hostname"]).first()
    if not cam:
        raise HTTPException(status_code=404, detail="Camera not found")

    # Save before state
    before = {
        "hostname": cam.hostname,
        "ip": cam.ip,
        "port": cam.port,
        "username": cam.username,
        "status": cam.status,
        "group_id": cam.group_id
    }

    try:
        cam.username = payload["username"]
        cam.password = payload["password"]

        if "ip" in payload:
            cam.ip = payload["ip"]

        if "port" in payload:
            cam.port = int(payload["port"]) if str(payload["port"]).strip() else None

        if "status" in payload:
            cam.status = payload["status"]

        if "group_name" in payload:
            group_name = payload["group_name"]
            group = db.query(CameraGroup).filter(CameraGroup.name == group_name).first()
            if not group:
                group = CameraGroup(name=group_name)
                db.add(group)
                db.flush()
            cam.group_id = group.id

        db.commit()

        after = {
            "hostname": cam.hostname,
            "ip": cam.ip,
            "port": cam.port,
            "username": cam.username,
            "status": cam.status,
            "group_id": cam.group_id
        }

        # Audit
        log_audit(
            db=db,
            user=request.session.get("user_name", "Whatsapp-bot"),
            action="update_camera",
            target=cam.hostname,
            ip=request.client.host,
            extra=json.dumps({
                "before": before,
                "after": after
            }, indent=2)
        )

        return {"status": "success", "message": f"Camera '{cam.hostname}' updated successfully"}

    except Exception as e:
        db.rollback()
        logger.error("Error updating camera from n8n: %s", e)
        raise HTTPException(status_code=500, detail="Internal server error")
