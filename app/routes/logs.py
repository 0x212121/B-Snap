from fastapi import APIRouter, Depends, Request, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from app.models.user import User
from app.routes.auth import operator_access_required
from app.utils.template_helper import templates
from pathlib import Path
import os
import re
from datetime import datetime
from typing import List, Optional

router = APIRouter(tags=["Logs"])

LOG_DIR = Path("logs")
LOG_TYPES = ["main", "snapshot", "healthcheck", "scheduler", "management"]
LOG_COLORS = {
    "ERROR": "red",
    "WARNING": "yellow",
    "WARN": "yellow",
    "INFO": "blue",
    "DEBUG": "gray"
}


class LogEntry:
    """Represents a single parsed log entry."""
    def __init__(self, line: str, line_number: int):
        self.raw = line
        self.line_number = line_number
        self.timestamp = None
        self.level = "INFO"
        self.logger = "unknown"
        self.pid = None
        self.message = line.strip()
        self._parse()
    
    def _parse(self):
        """Parse log line format: [2024-01-01 10:00:00,123] [LEVEL] [logger] [pid=X] message"""
        # Timestamp pattern
        ts_match = re.match(r'\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:,\d+)?)\]', self.raw)
        if ts_match:
            self.timestamp = ts_match.group(1)
        
        # Level pattern
        level_match = re.search(r'\[(ERROR|WARNING|WARN|INFO|DEBUG)\]', self.raw)
        if level_match:
            self.level = level_match.group(1).upper()
        
        # Logger name pattern (after level)
        logger_match = re.search(r'\[(ERROR|WARNING|WARN|INFO|DEBUG)\]\s*\[([^\]]+)\]', self.raw)
        if logger_match:
            self.logger = logger_match.group(2).lower()
        
        # PID pattern
        pid_match = re.search(r'\[pid=(\d+)\]', self.raw)
        if pid_match:
            self.pid = int(pid_match.group(1))
        
        # Extract message (everything after the metadata)
        msg_match = re.search(r'\[pid=\d+\]\s*(.+)$', self.raw)
        if msg_match:
            self.message = msg_match.group(1)
        else:
            # Fallback: try to extract after timestamp
            msg_match = re.search(r'\[\d{4}-[^\]]+\]\s*(.+)$', self.raw)
            if msg_match:
                self.message = msg_match.group(1)
    
    def to_dict(self):
        return {
            "line_number": self.line_number,
            "timestamp": self.timestamp,
            "level": self.level,
            "logger": self.logger,
            "pid": self.pid,
            "message": self.message,
            "raw": self.raw,
            "color": LOG_COLORS.get(self.level, "")
        }


# ─── 1. UI view ─────────────────────────────────────────────────────
@router.get("/logs", response_class=HTMLResponse)
async def view_logs(
    request: Request,
    log_type: str = "main",
    current_operator: User = Depends(operator_access_required)
):
    return templates.TemplateResponse("logs.html", {
        "request": request,
        "log_type": log_type,
    })


# ─── 2. Return list of log files under that log_type ────────────────
@router.get("/logs/files/{log_type}")
async def list_log_files(
    log_type: str,
    current_operator: User = Depends(operator_access_required)
):
    if log_type not in LOG_TYPES:
        raise HTTPException(status_code=400, detail="Invalid log type")

    dir_path = LOG_DIR
    if not dir_path.exists():
        return JSONResponse(content={"files": []})

    # List only .log, .log.1, .log.2 etc.
    files = sorted([
        f.name for f in dir_path.glob(f"{log_type}.log*") if f.is_file()
    ])
    
    # Add metadata for each file
    file_info = []
    for filename in files:
        fpath = dir_path / filename
        stat = fpath.stat()
        file_info.append({
            "name": filename,
            "size": stat.st_size,
            "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            "size_human": _format_bytes(stat.st_size)
        })
    
    return JSONResponse(content={"files": file_info})


# ─── 3. Return parsed log content with filtering ────────────────────
@router.get("/logs/content/{log_type}/{filename}")
async def get_log_content(
    log_type: str,
    filename: str,
    level: Optional[str] = Query(None, description="Filter by level: ERROR, WARNING, INFO, DEBUG"),
    logger: Optional[str] = Query(None, description="Filter by logger name"),
    search: Optional[str] = Query(None, description="Search in message"),
    limit: int = Query(1000, ge=1, le=5000, description="Max lines to return"),
    offset: int = Query(0, ge=0, description="Skip first N lines"),
    current_operator: User = Depends(operator_access_required)
):
    if log_type not in LOG_TYPES:
        raise HTTPException(status_code=400, detail="Invalid log type")

    safe_filename = os.path.basename(filename)
    log_path = LOG_DIR / safe_filename

    if not log_path.exists() or not log_path.is_file():
        return JSONResponse(content={"entries": [], "total": 0, "error": f"{filename} not found."})

    try:
        with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
    except Exception as e:
        return JSONResponse(content={"entries": [], "total": 0, "error": str(e)})

    # Parse all lines
    entries = []
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        entry = LogEntry(line, i + 1)
        entries.append(entry)

    # Apply filters
    filtered = entries
    if level:
        levels = [l.strip().upper() for l in level.split(",")]
        filtered = [e for e in filtered if e.level in levels]
    if logger:
        loggers = [l.strip().lower() for l in logger.split(",")]
        filtered = [e for e in filtered if any(l in e.logger for l in loggers)]
    if search:
        search_lower = search.lower()
        filtered = [e for e in filtered if search_lower in e.message.lower() or search_lower in e.raw.lower()]

    total = len(filtered)
    
    # Apply pagination
    paginated = filtered[offset:offset + limit]
    
    # Get unique loggers for filter dropdown
    unique_loggers = sorted(set(e.logger for e in entries))
    
    # Get level counts
    level_counts = {}
    for e in entries:
        level_counts[e.level] = level_counts.get(e.level, 0) + 1

    return JSONResponse(content={
        "entries": [e.to_dict() for e in paginated],
        "total": total,
        "offset": offset,
        "limit": limit,
        "unique_loggers": unique_loggers,
        "level_counts": level_counts
    })


# ─── 4. Download a specific log file ────────────────────────────────
@router.get("/logs/download/{log_type}/{filename}")
async def download_log(
    log_type: str,
    filename: str,
    current_operator: User = Depends(operator_access_required)
):
    if log_type not in LOG_TYPES:
        raise HTTPException(status_code=400, detail="Invalid log type")

    safe_filename = os.path.basename(filename)
    log_path = LOG_DIR / safe_filename

    if not log_path.exists() or not log_path.is_file():
        raise HTTPException(status_code=404, detail="Log file not found")

    return FileResponse(log_path, filename=safe_filename, media_type="text/plain")


# ─── 5. Get log statistics ─────────────────────────────────────────
@router.get("/logs/stats/{log_type}/{filename}")
async def get_log_stats(
    log_type: str,
    filename: str,
    current_operator: User = Depends(operator_access_required)
):
    """Get statistics about a log file."""
    if log_type not in LOG_TYPES:
        raise HTTPException(status_code=400, detail="Invalid log type")

    safe_filename = os.path.basename(filename)
    log_path = LOG_DIR / safe_filename

    if not log_path.exists():
        return JSONResponse(content={"error": "File not found"})

    try:
        with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
        
        entries = []
        for i, line in enumerate(lines):
            if line.strip():
                entries.append(LogEntry(line, i + 1))
        
        # Calculate stats
        level_counts = {}
        logger_counts = {}
        error_entries = []
        
        for e in entries:
            level_counts[e.level] = level_counts.get(e.level, 0) + 1
            logger_counts[e.logger] = logger_counts.get(e.logger, 0) + 1
            if e.level == "ERROR":
                error_entries.append(e.to_dict())
        
        return JSONResponse(content={
            "total_lines": len(lines),
            "parsed_entries": len(entries),
            "level_counts": level_counts,
            "logger_counts": dict(sorted(logger_counts.items(), key=lambda x: -x[1])[:10]),
            "recent_errors": error_entries[-10:]  # Last 10 errors
        })
    except Exception as e:
        return JSONResponse(content={"error": str(e)})


def _format_bytes(bytes_val: int) -> str:
    """Format bytes to human readable."""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if bytes_val < 1024.0:
            return f"{bytes_val:.1f} {unit}"
        bytes_val /= 1024.0
    return f"{bytes_val:.1f} TB"
