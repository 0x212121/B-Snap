from datetime import datetime
from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from app.models_sql import User
from app.routes.auth import operator_access_required
from app.utils.video import STATIC_VIDEO_DIR
import os

router = APIRouter()

templates = Jinja2Templates(directory="templates")

@router.get("/videos", response_class=HTMLResponse)
async def video_gallery(request: Request, current_operator: User = Depends(operator_access_required)):
    import pathlib
    video_dir = pathlib.Path("app/static/videos")
    if not video_dir.exists():
        video_dir.mkdir(parents=True, exist_ok=True)
        
    video_files = sorted(video_dir.glob("*.mp4"), reverse=True)

    videos = []
    for file in video_files:
        name_parts = file.stem.split("_")
        if len(name_parts) >= 3:
            camera_name = "_".join(name_parts[:-2])
            timestamp = "_".join(name_parts[-2:])
            try:
                dt = datetime.strptime(timestamp, "%Y%m%d_%H%M%S")
                readable = dt.strftime("%d %b %Y, %H:%M:%S")
            except:
                readable = "Unknown"
        else:
            camera_name = "Unknown"
            readable = "Unknown"

        videos.append({
            "url": f"/static/videos/{file.name}",
            "camera": camera_name,
            "time": readable,
            "filename" : file.name
        })

    return templates.TemplateResponse("videos.html", {
        "request": request,
        "videos": videos,
    })

@router.delete("/videos/{filename}", response_class=JSONResponse)
async def delete_video(request: Request, filename: str, current_operator: User = Depends(operator_access_required)):
    # Only allow admin users
    # if request.session.get("user_role") != "admin":
    #     raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
    #                         detail="Not authorized to delete videos")

    # Prevent path traversal
    safe_name = os.path.basename(filename)
    full_path = os.path.join(STATIC_VIDEO_DIR, safe_name)

    if not os.path.isfile(full_path):
        raise HTTPException(status_code=404, detail="Video not found")

    try:
        os.remove(full_path)
        return JSONResponse({"message": f"Video '{safe_name}' deleted"}, status_code=200)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete video: {e}")
