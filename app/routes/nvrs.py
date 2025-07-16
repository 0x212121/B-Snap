import csv
import io
import json
import logging
from typing import Optional
from fastapi import APIRouter, File, HTTPException, Path, Query, Request, Depends, Form, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, StreamingResponse
from sqlalchemy import asc, or_
from sqlalchemy.orm import Session, joinedload
from app.core.config import get_config
from app.core.logging_config import setup_logging
from app.models_sql import NVR, CameraGroup, User
from app.db.database import get_db
from starlette.templating import Jinja2Templates
from app.routes.auth import admin_access_required
from app.routes.cameras import detect_csv_delimiter
from app.utils.audit_logger import log_audit
from app.utils.health_check import ping_nvr_by_id

router = APIRouter()
from app.utils.template_helper import templates

setup_logging()
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
    current_admin: User = Depends(admin_access_required)
):
    """
    This endpoint is called via AJAX by the frontend to fetch new pages of data
    without reloading the entire page. It returns only the HTML for the table rows
    and the pagination controls.
    """
    per_page = get_config('items_per_page', 10)
    query = db.query(NVR)

    # Apply the same search logic as the main endpoint
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
    
    # Sorting is handled client-side by the `sortTable` JavaScript function,
    # so we only need a default order here.
    query = query.order_by(asc(NVR.hostname)).options(joinedload(NVR.group))

    # Pagination logic
    total = query.count()
    nvrs = query.offset((page - 1) * per_page).limit(per_page).all()
    total_pages = (total + per_page - 1) // per_page if total > 0 else 1
    
    # --- Generate HTML for table rows ---
    rows_html = ""
    if not nvrs:
        rows_html = '<tr><td colspan="8" class="p-4 text-center text-gray-500">No NVRs found.</td></tr>'
    else:
        for nvr in nvrs:
            # Note: The number of columns here must match your table header in nvrs.html
            rows_html += f"""
            <tr class="border-b dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-700 text-sm text-gray-700 dark:text-gray-300">
                <td class="p-3 truncate">{ nvr.hostname or '' }</td>
                <td class="p-3 truncate">{ nvr.ip or '' }</td>
                <td class="p-3 truncate">{ nvr.username or '' }</td>
                <td class="p-3 truncate">{ nvr.location or '' }</td>
                <td class="p-3 truncate">{ nvr.group.name if nvr.group else ''}</td>
                <td class="p-3">
                    <span class="px-2 py-1 text-xs font-semibold rounded-full {'bg-green-100 text-green-800' if nvr.status == 'Active' else 'bg-red-100 text-red-800'}">
                        {'Unknown' if not nvr.status else nvr.status}
                    </span>
                </td>
                <td class="p-3 space-x-3 whitespace-nowrap">
                <button onclick="showEditNVRModal('{nvr.id}')" class="text-blue-600 dark:text-blue-400 hover:underline font-semibold text-xs">✏️ Edit</button>
                <form method="post" class="inline" onsubmit="event.preventDefault(); confirmDelete('{nvr.id}')">
                    <button type="submit" class="text-red-500 dark:text-red-400 hover:underline font-semibold text-xs">🗑️ Delete</button>
                </form>
            </td>
            </tr>
            """# --- Generate HTML for pagination controls ---
    # This now matches the simple loadPage(page, per_page) function in your JS
    pagination_html = ""
    if total_pages > 1:
        links = []
        window = 2  # Number of pages around the current page
        
        # 'First' and 'Prev' links
        if page > 1:
            links.append(f'<a href="#" onclick="event.preventDefault(); loadPage(1)" class="px-3 py-1 bg-gray-200 dark:bg-gray-700 rounded hover:bg-blue-500 dark:hover:bg-blue-600 hover:text-white">First</a>')
            links.append(f'<a href="#" onclick="event.preventDefault(); loadPage({page - 1})" class="px-3 py-1 bg-gray-200 dark:bg-gray-700 rounded hover:bg-blue-500 dark:hover:bg-blue-600 hover:text-white">«</a>')

        # Show ellipsis if needed
        if page > window + 2:
            links.append('<span class="px-3 py-1">...</span>')

        # Show page numbers
        for i in range(max(1, page - window), min(total_pages, page + window) + 1):
            if i == page:
                links.append(f'<span class="px-3 py-1 bg-blue-600 text-white rounded font-bold">{i}</span>')
            else:
                links.append(f'<a href="#" onclick="event.preventDefault(); loadPage({i})" class="px-3 py-1 bg-gray-200 dark:bg-gray-700 rounded hover:bg-blue-500 dark:hover:bg-blue-600 hover:text-white">{i}</a>')
        
        # Show ellipsis if needed
        if page < total_pages - window - 1:
             links.append('<span class="px-3 py-1">...</span>')

        # 'Next' and 'Last' links
        if page < total_pages:
            links.append(f'<a href="#" onclick="event.preventDefault(); loadPage({page + 1})" class="px-3 py-1 bg-gray-200 dark:bg-gray-700 rounded hover:bg-blue-500 dark:hover:bg-blue-600 hover:text-white">»</a>')
            links.append(f'<a href="#" onclick="event.preventDefault(); loadPage({total_pages})" class="px-3 py-1 bg-gray-200 dark:bg-gray-700 rounded hover:bg-blue-500 dark:hover:bg-blue-600 hover:text-white">Last</a>')
        
        pagination_html = " ".join(links)

    return HTMLResponse(content=f"{rows_html}|||{pagination_html}")


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
    group_name: str = Form(None),  # ini dikirim dari form
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required)
):
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
        asset_no=asset_no
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
        "status": nvr.status
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

    if password:
        nvr.password = password  # TODO: hash this in production!

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
        "status": nvr.status
    }

    log_audit(
        db=db,
        user=request.session.get("user_name", "unknown"),
        action="update_nvr",
        target=nvr.hostname,
        ip=request.client.host,
        extra=json.dumps({"before": before, "after": after}, indent=2)
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
async def get_camera_details(request: Request, nvr_id: str, db: Session = Depends(get_db), current_admin: User = Depends(admin_access_required)):
    nvr = db.query(NVR).options(joinedload(NVR.group)).filter(NVR.id == nvr_id).first()
    if not nvr:
        raise HTTPException(status_code=404, detail="NVR not found")
        
    return {
        "id": nvr.id,
        "hostname": nvr.hostname,
        "ip": nvr.ip,
        "username": nvr.username,
        "password": nvr.password,
        "location": nvr.location,
        "group": {"name": nvr.group.name} if nvr.group else None
    }


@router.get("/nvrs/export_csv")
async def export_csv(request: Request, db: Session = Depends(get_db), current_admin: User = Depends(admin_access_required)):
    
    # Eagerly load the group relationship to avoid N+1 query issues in the loop
    nvrs = db.query(NVR).options(joinedload(NVR.group)).all()

    output = io.StringIO()
    writer = csv.writer(output)

    # Header CSV - Changed 'division' to 'group_name' for consistency
    writer.writerow([
        "id", "hostname", "ip", "username", "password",
        "group_name", "asset_no", "location", "latitude", "longitude", "status"
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


@router.post("/nvrs/upload_csv")
async def upload_nvr_csv(request: Request, db: Session = Depends(get_db), file: UploadFile = File(...), current_admin: User = Depends(admin_access_required)):
    contents = await file.read()
    failed_rows = []
    success_ids = []

    try:
        csv_text = contents.decode("utf-8-sig") # Use utf-8-sig to handle potential BOM
        detected_delimiter = detect_csv_delimiter(csv_text)
        csv_reader = csv.DictReader(io.StringIO(csv_text), delimiter=detected_delimiter)

        for row in csv_reader:
            try:
                hostname = row.get("hostname", "").strip()
                if not hostname:
                    continue # Skip rows without a hostname

                # --- Group Name Handling ---
                group_name = row.get("group_name", "").strip()
                group_id = None
                if group_name:
                    group = db.query(CameraGroup).filter(CameraGroup.name == group_name).first()
                    if not group:
                        group = CameraGroup(name=group_name)
                        db.add(group)
                        db.flush()
                    group_id = group.id

                # Check for an existing NVR by hostname
                existing_nvr = db.query(NVR).filter_by(hostname=hostname).first()

                # Prepare NVR data from CSV row
                nvr_data = {
                    "ip": row.get("ip", "").strip(),
                    "username": row.get("username", "").strip(),
                    "password": row.get("password", "").strip(),
                    "asset_no": row.get("asset_no", "").strip(),
                    "location": row.get("location", "").strip(),
                    "status": row.get("status", "Active").strip() or "Active",
                    "latitude": float(row["latitude"]) if row.get("latitude", "").strip() else None,
                    "longitude": float(row["longitude"]) if row.get("longitude", "").strip() else None,
                    "group_id": int(group_id) if group_id is not None else None,
                }

                if existing_nvr:
                    # Update the existing NVR's attributes
                    for key, value in nvr_data.items():
                        setattr(existing_nvr, key, value)
                    db.add(existing_nvr)
                    success_ids.append(existing_nvr.id)
                    log_audit(
                        db=db,
                        user=request.session["user_name"],
                        action="update_nvr",
                        target=hostname,
                        ip=request.client.host,
                        extra="via CSV Upload"
                    )
                else:
                    # Create a new NVR instance
                    new_nvr = NVR(hostname=hostname, **nvr_data)
                    db.add(new_nvr)
                    db.flush()
                    success_ids.append(new_nvr.id)
                    log_audit(
                        db=db,
                        user=request.session["user_name"],
                        action="create_camera",
                        target=hostname,
                        ip=request.client.host,
                        extra="via CSV Upload"
                    )

            except Exception as e:
                db.rollback() # Rollback changes for the failed row
                logger.error("[Row Error] Hostname: %s => %s", row.get('hostname'), e)
                failed_rows.append(row.get("hostname"))

        db.commit()
        logger.info("NVR upload committed")

    except Exception as e:
        db.rollback() # Rollback all changes if a major error occurs
        logger.error("CSV upload error: %s", e)

    finally:
        db.close()

    logger.info("[UPLOAD SUMMARY] Success: %d, Failed: %d", len(success_ids), len(failed_rows))
    if failed_rows:
        logger.warning("[FAILED HOSTNAMES]: %s", failed_rows)

    return RedirectResponse(url="/nvrs", status_code=302)


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
