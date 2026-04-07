import csv
import io
import logging
import json
from typing import Optional
from fastapi import APIRouter, File, HTTPException, Path, Query, Request, Depends, Form, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, StreamingResponse
from sqlalchemy import asc, or_
from sqlalchemy.orm import Session, joinedload
from app.core.config import get_config
from app.core.logging_config import setup_logging
from app.models.nvr import NVR
from app.models.camera_group import CameraGroup
from app.models.user import User
from app.db.database import get_db
from app.routes.auth import admin_access_required
from app.routes.cameras import detect_csv_delimiter
from app.utils.audit_logger import log_audit
from app.utils.healthcheck import ping_nvr_by_id

router = APIRouter(tags=["NVRs"])
from app.utils.template_helper import templates

logger = logging.getLogger("management")


@router.get("/nvrs")
async def list_nvrs(request: Request, db: Session = Depends(get_db), page: int = Query(1, ge=1), search: str = Query(None), current_admin: User = Depends(admin_access_required)):
    per_page = get_config('items_per_page', 10)

    query = db.query(NVR)  # Include group relation

    if search:
        query = query.filter(NVR.hostname.ilike(f"%{search}%"))

    query = query.order_by(asc(NVR.hostname))  # Default sort by name

    total = query.count()
    nvrs = query.offset((page - 1) * per_page).limit(per_page).all()
    db.close()

    total_pages = (total + per_page - 1) // per_page
    return templates.TemplateResponse("nvrs.html", {
        "request": request,
        "nvrs": nvrs,
        "page": page,
        "total_pages": total_pages,
        "per_page": per_page,
        "search": search,
        "total": total
    })


@router.get("/nvrs/data", response_class=HTMLResponse)
async def get_nvrs_data(
    request: Request, 
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    search: str = Query(None),
    per_page: int = Query(20, ge=1),
    current_admin: User = Depends(admin_access_required)
):
    query = db.query(NVR)
    
    if search:
        search_term = f"%{search}%"
        query = query.join(CameraGroup, NVR.group_id == CameraGroup.id, isouter=True).filter(
            or_(
                NVR.hostname.ilike(search_term),
                NVR.ip.ilike(search_term),
                NVR.location.ilike(search_term),
                NVR.asset_no.ilike(search_term),
                CameraGroup.name.ilike(search_term),
                NVR.status.ilike(search_term)
            )
        )
    
    query = query.order_by(asc(NVR.hostname)).options(joinedload(NVR.group))
    total = query.count()
    nvrs = query.offset((page - 1) * per_page).limit(per_page).all()
    
    # Calculate stats
    base_query = db.query(NVR)
    if search:
        base_query = base_query.join(CameraGroup, NVR.group_id == CameraGroup.id, isouter=True).filter(
            or_(
                NVR.hostname.ilike(search_term),
                NVR.ip.ilike(search_term),
                NVR.location.ilike(search_term),
                CameraGroup.name.ilike(search_term),
            )
        )
    
    stats = {
        "total": total,
        "active": base_query.filter(NVR.status == "Active").count(),
        "deactivated": base_query.filter(NVR.status == "Deactivated").count(),
        "restricted": base_query.filter(NVR.status == "Restricted").count(),
        "located": base_query.filter(NVR.latitude.isnot(None), NVR.longitude.isnot(None)).count()
    }
    
    total_pages = (total + per_page - 1) // per_page if total > 0 else 1
    
    # Generate rows HTML with new styling
    rows_html = ""
    if not nvrs:
        rows_html = '<tr><td colspan="7" class="p-4 text-center text-gray-500 dark:text-gray-400">No NVRs found.</td></tr>'
    else:
        for nvr in nvrs:
            # Status badge class
            status_class = "status-active" if nvr.status == "Active" else "status-deactivated" if nvr.status == "Deactivated" else "status-restricted"
            
            # Initials for avatar
            initials = (nvr.hostname or "NVR")[:2].upper()
            
            rows_html += f"""
            <tr class="group">
              <td class="px-4 py-3">
                <div class="flex items-center gap-3">
                  <div class="w-10 h-10 rounded-full bg-gradient-to-br from-blue-400 to-blue-600 flex items-center justify-center text-white text-sm font-bold">
                    {initials}
                  </div>
                  <div>
                    <p class="font-semibold text-gray-900 dark:text-white">{nvr.hostname or ''}</p>
                    <p class="text-xs text-gray-500 dark:text-gray-400">{nvr.ip or ''}</p>
                  </div>
                </div>
              </td>
              <td class="px-4 py-3 text-gray-700 dark:text-gray-300 font-mono text-xs">{nvr.ip or ''}</td>
              <td class="px-4 py-3 text-gray-700 dark:text-gray-300">{nvr.username or ''}</td>
              <td class="px-4 py-3 text-gray-700 dark:text-gray-300 max-w-xs truncate" title="{nvr.location or ''}">{nvr.location or '-'}</td>
              <td class="px-4 py-3">
                <span class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-gray-100 text-gray-800 dark:bg-gray-700 dark:text-gray-300">
                  {nvr.group.name if nvr.group else 'No Group'}
                </span>
              </td>
              <td class="px-4 py-3 text-center">
                <span class="status-badge {status_class}">
                  {nvr.status or 'Unknown'}
                </span>
              </td>
              <td class="px-4 py-3 text-center">
                <div class="flex items-center justify-center gap-1">
                  <button onclick="showEditNVRModal('{nvr.id}')"
                          title="Edit"
                          class="p-2 text-gray-400 hover:text-blue-600 dark:hover:text-blue-400 rounded-lg hover:bg-blue-50 dark:hover:bg-blue-900/20 transition">
                    <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z"></path>
                    </svg>
                  </button>
                  <button onclick="confirmDelete('{nvr.id}', '{nvr.hostname}')"
                          title="Delete"
                          class="p-2 text-gray-400 hover:text-red-600 dark:hover:text-red-400 rounded-lg hover:bg-red-50 dark:hover:bg-red-900/20 transition">
                    <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"></path>
                    </svg>
                  </button>
                </div>
              </td>
            </tr>
            """
    
    # Return with stats as third part
    return HTMLResponse(content=f"{rows_html}|||{''}|||{json.dumps(stats)}")


# This code running perfectly
@router.post("/nvrs/create")
async def create_nvr(
    request: Request,
    name: str = Form(...),
    ip: str = Form(...),
    username: str = Form(...),
    password: str = Form(...),
    latitude: Optional[str] = Form(None),
    longitude: Optional[str] = Form(None),
    asset_no: Optional[str] = Form(None),
    location: Optional[str] = Form(None),
    group_name: str = Form(...),  # Changed: now required
    status: str = Form(None),
    note: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required)
):
    # Validate group_name is provided
    if not group_name or not group_name.strip():
        logger.warning("Attempted to add NVR without group: %s", name)
        return JSONResponse(status_code=400, content={"status": "error", "message": "Group is required. Please select a group."})
    
    # Convert lat/lon jika ada
    lat = float(latitude) if latitude and latitude.strip() else None
    lon = float(longitude) if longitude and longitude.strip() else None

    # Resolve group_name → group_id
    group_id = None
    if group_name and group_name.strip():
        group = db.query(CameraGroup).filter(CameraGroup.name == group_name.strip()).first()
        if not group:
            group = CameraGroup(name=group_name.strip())
            db.add(group)
            db.flush()  # Supaya dapat group.id tanpa commit
        group_id = group.id

    # Cek duplikat
    existing_nvr = db.query(NVR).filter(NVR.hostname == name).first()
    if existing_nvr:
        return JSONResponse(status_code=409, content={"status": "error", "message": f"NVR '{name}' already exists."})

    # Buat objek NVR
    nvr = NVR(
        hostname=name,
        ip=ip,
        username=username,
        password=password,
        location=location,
        group_id=group_id,
        latitude=lat,
        longitude=lon,
        asset_no=asset_no,
        status=status or "Deactivated",
        note=note if note and note.strip() else None
    )

    db.add(nvr)
    db.flush()  # flush dulu supaya dapat ID-nya buat ping

    # Logging audit
    log_audit(
        db=db,
        user=request.session.get("user_name", "unknown"),
        action="create_nvr",
        target=nvr.hostname,
        ip=request.client.host,
        extra="via NVR Management"
    )

    # Ping NVR setelah dibuat (pakai ID)
    ping_nvr_by_id(nvr.id)

    db.commit()  # Commit terakhir setelah semua oke
    return JSONResponse(status_code=201, content={"status": "success", "message": "NVR created successfully!"})


@router.post("/nvrs/edit/{nvr_id}")
async def edit_nvr(
    request: Request,
    nvr_id: str = Path(...),  # ID dari URL
    db: Session = Depends(get_db),
    name: str = Form(...),
    ip: str = Form(...),
    username: str = Form(...),
    password: Optional[str] = Form(None),
    latitude: Optional[str] = Form(None),
    longitude: Optional[str] = Form(None),
    asset_no: Optional[str] = Form(None),
    location: Optional[str] = Form(None),
    group_name: str = Form(None),
    status: str = Form(...),
    note: Optional[str] = Form(None),
    current_admin: User = Depends(admin_access_required)
):
    nvr = db.query(NVR).filter(NVR.id == nvr_id).first()
    if not nvr:
        return JSONResponse(status_code=404, content={"detail": "NVR not found"})

    before = {
        "hostname": nvr.hostname,
        "ip": nvr.ip,
        "username": nvr.username,
        "latitude": nvr.latitude,
        "longitude": nvr.longitude,
        "asset_no": nvr.asset_no,
        "location": nvr.location,
        "group_id": nvr.group_id,
        "status": nvr.status,
        "note": nvr.note
    }

    # Parse float fields safely
    lat = float(latitude) if latitude and latitude.strip() else None
    lon = float(longitude) if longitude and longitude.strip() else None

    # Resolve group_name to group_id
    group_id = None
    if group_name and group_name.strip():
        group = db.query(CameraGroup).filter(CameraGroup.name == group_name.strip()).first()
        if not group:
            group = CameraGroup(name=group_name.strip())
            db.add(group)
            db.flush()
        group_id = group.id

    # Update fields
    nvr.hostname = name
    nvr.ip = ip
    nvr.username = username
    nvr.location = location
    nvr.asset_no = asset_no
    nvr.latitude = lat
    nvr.longitude = lon
    nvr.status = status
    nvr.group_id = group_id  # safe: will be None or int/UUID
    nvr.note = note if note and note.strip() else None


    if password:
        nvr.password = password

    db.commit()

    after = {
        "hostname": nvr.hostname,
        "ip": nvr.ip,
        "username": nvr.username,
        "latitude": nvr.latitude,
        "longitude": nvr.longitude,
        "asset_no": nvr.asset_no,
        "location": nvr.location,
        "group_id": nvr.group_id,
        "status": nvr.status,
        "note": nvr.note,
    }

    # Logika untuk menampilkan data yang berubah saja
    changes = []
    for key in before:
        if before[key] != after[key]:
            changes.append(f"- {key}: '{before[key]}' -> '{after[key]}'")
    
    if password:
        changes.append("- password: [CHANGED]")

    audit_extra = "\n".join(changes) if changes else "No changes detected."

    log_audit(
        db=db,
        user=request.session.get("user_name", "unknown"),
        action="update_nvr",
        target=nvr.hostname,
        ip=request.client.host,
        extra=audit_extra
    )

    return JSONResponse(content={"status": "success", "message": "NVR updated successfully"})


@router.get("/nvrs/json")
async def get_nvrs_json(db: Session = Depends(get_db), current_admin: User = Depends(admin_access_required)):
    nvrs = db.query(NVR).all()
    return JSONResponse(content=[{
        "id": nvr.id,
        "hostname": nvr.hostname,
        "ip": nvr.ip,
        "username": nvr.username,
        "password": nvr.password,
        "location": nvr.location,
        "group_name": nvr.group_id
    } for nvr in nvrs])


@router.get("/api/nvr/{nvr_id}", response_class=JSONResponse)
async def get_nvr_details(request: Request, nvr_id: str, db: Session = Depends(get_db), current_admin: User = Depends(admin_access_required)):
    """Get NVR details (without password for security)."""
    nvr = db.query(NVR).options(joinedload(NVR.group)).filter(NVR.id == nvr_id).first()
    if not nvr:
        raise HTTPException(status_code=404, detail="NVR not found")
    
    # Password excluded from standard response for security
    return {
        "id": nvr.id,
        "hostname": nvr.hostname,
        "ip": nvr.ip,
        "username": nvr.username,
        # "password": nvr.password,  # REMOVED: Password not exposed in API
        "location": nvr.location,
        "note": nvr.note,
        "group": {"name": nvr.group.name} if nvr.group else None
    }


@router.get("/api/nvr/{nvr_id}/password", response_class=JSONResponse)
async def get_nvr_password(
    request: Request, 
    nvr_id: str, 
    db: Session = Depends(get_db), 
    current_admin: User = Depends(admin_access_required)
):
    """
    Admin only: Fetches the password for an NVR.
    This endpoint is logged and should only be used when necessary.
    """
    logger.debug("Admin fetching password for NVR ID: %s", nvr_id)
    nvr = db.query(NVR).filter(NVR.id == nvr_id).first()
    if not nvr:
        logger.warning("NVR '%s' not found for password retrieval.", nvr_id)
        raise HTTPException(status_code=404, detail="NVR not found")
    
    # Log this sensitive access
    log_audit(
        db=db,
        user=request.session.get("user_name", "unknown"),
        action="view_nvr_password",
        target=nvr.hostname,
        ip=request.client.host,
        extra=f"NVR ID: {nvr_id}"
    )
    logger.info("Admin %s retrieved password for NVR %s", 
                request.session.get("user_name"), nvr.hostname)
    
    return {
        "id": nvr.id,
        "hostname": nvr.hostname,
        "username": nvr.username,
        "password": nvr.password  # Decrypted via property
    }


@router.post("/nvrs/delete/{nvr_id}", response_class=JSONResponse)
async def delete_nvr(
    request: Request,
    nvr_id: str = Path(...),
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required)
):
    """
    Deletes an NVR from the database.
    This endpoint is called via AJAX from the frontend after user confirmation.
    """
    nvr = db.query(NVR).filter(NVR.id == nvr_id).first()
    if not nvr:
        return JSONResponse(
            status_code=404,
            content={"status": "error", "message": "NVR not found."}
        )

    try:
        nvr_hostname = nvr.hostname  # Store hostname for logging before deletion
        
        # Log the audit trail before committing the deletion
        log_audit(
            db=db,
            user=request.session.get("user_name", "unknown"),
            action="delete_nvr",
            target=str(nvr_hostname),
            ip=request.client.host,
            extra=f"NVR '{nvr_hostname}' (ID: {nvr_id}) deleted."
        )

        db.delete(nvr)
        db.commit()

        return JSONResponse(
            status_code=200,
            content={"status": "success", "message": f"NVR '{nvr_hostname}' has been deleted successfully."}
        )
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to delete NVR {nvr_id}: {e}")
        return JSONResponse(
            status_code=500,
            content={"status": "error", "message": "An internal server error occurred while deleting the NVR."}
        )


@router.get("/nvrs/export_csv")
async def export_csv(request: Request, db: Session = Depends(get_db), current_admin: User = Depends(admin_access_required)):
    
    # Eagerly load the group relationship to avoid N+1 query issues in the loop
    nvrs = db.query(NVR).options(joinedload(NVR.group)).all()

    output = io.StringIO()
    writer = csv.writer(output)

    # Header CSV - Changed 'division' to 'group_name' for consistency
    writer.writerow([
        "id", "hostname", "ip", "username", "password",
        "group_name", "asset_no", "location", "latitude", "longitude", "status", "note"
    ])

    # Write data rows
    for nvr in nvrs:
        writer.writerow([
            nvr.id,
            nvr.hostname,
            nvr.ip,
            nvr.username,
            nvr.password,
            nvr.group.name if nvr.group else "", # Write the group name if it exists
            nvr.asset_no,
            nvr.location,
            nvr.latitude,
            nvr.longitude,
            nvr.status,
            nvr.note
        ])

    output.seek(0)
    log_audit(
        db=db,
        user=request.session["user_name"],
        action="export_nvr_csv",
        target="NVRs Export",
        ip=request.client.host,
        extra="via NVR Management"
    )
    return StreamingResponse(
        output,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=nvrs.csv"}
    )

MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MB
ALLOWED_FILE_TYPES = ["text/csv"]

def detect_csv_delimiter(csv_content: str):
    """Detects the delimiter (comma or semicolon) in a CSV string."""
    if not csv_content:
        return ',' # Default if content is empty

    # Get the first line of the CSV content
    first_line = csv_content.splitlines()[0]

    # Count the occurrences of commas and semicolons
    comma_count = first_line.count(',')
    semicolon_count = first_line.count(';')

    if comma_count > 0 and semicolon_count == 0:
        return ','
    elif semicolon_count > 0 and comma_count == 0:
        return ';'
    elif comma_count > 0 and semicolon_count > 0:
        if comma_count > semicolon_count:
            return ','
        else:
            return ';'
    return ',' # Default if no delimiter found


@router.post("/nvrs/upload_csv")
async def upload_nvr_csv(
    request: Request, 
    db: Session = Depends(get_db), 
    file: UploadFile = File(...), 
    current_admin: User = Depends(admin_access_required)
):
    logger.info("Starting NVR CSV upload process.")

    # 1. Validasi Tipe File (MIME Type)
    if file.content_type not in ALLOWED_FILE_TYPES:
        logger.warning("Upload failed: Invalid file type '%s'", file.content_type)
        return RedirectResponse(url="/nvrs?upload_error=File+type+invalid.+Only+CSV+is+allowed.", status_code=303)

    contents = await file.read()

    # 2. Validasi Ukuran File
    if len(contents) > MAX_FILE_SIZE:
        logger.warning("Upload failed: File size %d exceeds limit of %d", len(contents), MAX_FILE_SIZE)
        return RedirectResponse(url=f"/nvrs?upload_error=File+size+exceeded+maximum+limit+(5MB).", status_code=303)

    # 3. Validasi Konten File
    try:
        if not contents:
            logger.warning("Upload failed: Empty file uploaded.")
            return RedirectResponse(url="/nvrs?upload_error=Uploaded+CSV+is+empty.", status_code=303)
            
        csv_text = contents.decode("utf-8-sig")
        detected_delimiter = detect_csv_delimiter(csv_text)
        csv_reader = csv.DictReader(io.StringIO(csv_text), delimiter=detected_delimiter)
        
        headers = csv_reader.fieldnames
        required_headers = {"hostname"}

        if not headers or not required_headers.issubset(set(h.strip() for h in headers)):
            logger.warning("Upload failed: Missing required headers. Found: %s", headers)
            return RedirectResponse(url="/nvrs?upload_error=CSV+header+do+not+have+required+headers+'hostname'.", status_code=303)

        rows = list(csv_reader)
        if not rows:
            logger.warning("Upload failed: CSV file has headers but no data rows.")
            return RedirectResponse(url="/nvrs?upload_error=CSV+file+don't+have+rows.", status_code=303)

        failed_rows_info = []
        success_count = 0

        for i, row in enumerate(rows):
            row_num = i + 2
            hostname = row.get("hostname", "").strip()
            try:
                if not hostname:
                    logger.warning("Skipping row %s due to empty hostname.", row_num)
                    failed_rows_info.append(f"Row {row_num}: Hostname empty")
                    continue

                group_name = row.get("group_name", "").strip()
                group_id = None
                if group_name:
                    group = db.query(CameraGroup).filter(CameraGroup.name == group_name).first()
                    if not group:
                        group = CameraGroup(name=group_name)
                        db.add(group)
                        db.flush()
                    group_id = group.id

                existing_nvr = db.query(NVR).filter_by(hostname=hostname).first()

                nvr_data = {
                    "ip": row.get("ip", "").strip(),
                    "username": row.get("username", "").strip(),
                    "password": row.get("password", "").strip(),
                    "asset_no": row.get("asset_no", "").strip(),
                    "location": row.get("location", "").strip(),
                    "status": row.get("status", "Active").strip() or "Active",
                    "latitude": float(row["latitude"]) if row.get("latitude", "").strip() else None,
                    "longitude": float(row["longitude"]) if row.get("longitude", "").strip() else None,
                    "group_id": group_id,
                    "note": row.get("note", "").strip() or None
                }

                if existing_nvr:
                    for key, value in nvr_data.items():
                        setattr(existing_nvr, key, value)
                    log_action = "update_nvr"
                else:
                    new_nvr = NVR(hostname=hostname, **nvr_data)
                    db.add(new_nvr)
                    log_action = "create_nvr"
                
                log_audit(
                    db=db,
                    user=request.session.get("user_name", "unknown"),
                    action=log_action,
                    target=hostname,
                    ip=request.client.host,
                    extra="via CSV Upload"
                )
                success_count += 1

            except Exception as e:
                db.rollback()
                logger.error("Failed to process row %s (%s). Error: %s", row_num, hostname, e)
                failed_rows_info.append(f"Baris {row_num} ({hostname}): Gagal diproses")

        db.commit()
        logger.info("NVR CSV upload finished. Successes: %s, Failures: %s.", success_count, len(failed_rows_info))
        
        if failed_rows_info:
             return RedirectResponse(url=f"/nvrs?upload_warning={success_count}+sukses,+{len(failed_rows_info)}+gagal.", status_code=303)
        else:
             return RedirectResponse(url=f"/nvrs?upload_success={success_count}+NVR+berhasil+diproses.", status_code=303)

    except Exception as e:
        db.rollback()
        logger.error("Critical error during NVR CSV upload. Error: %s", e, exc_info=True)
        return RedirectResponse(url="/nvrs?upload_error=Terjadi+kesalahan+fatal+saat+memproses+file.", status_code=303)

