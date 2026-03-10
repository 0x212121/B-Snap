"""Routes for managing orphaned snapshot files.

Provides endpoints for:
1. Scanning for orphaned files
2. Viewing orphaned file list
3. Reviewing and deleting orphaned files
4. Storage summary and cleanup
"""
import logging
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Request, Query
from fastapi.responses import JSONResponse, HTMLResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.db.database import get_db
from app.models.user import User
from app.models.orphaned_file import OrphanedFile
from app.routes.auth import admin_access_required, operator_access_required
from app.utils.orphaned_scanner import (
    FolderScanner, 
    OrphanedFileManager, 
    run_orphaned_scan,
    cleanup_orphaned_files,
    cleanup_orphaned_files_from_disk
)
from app.utils.template_helper import templates

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Orphaned Files"])


# ============================================================================
# SCHEMAS
# ============================================================================

class OrphanedFileResponse(BaseModel):
    id: int
    file_path: str
    file_size: Optional[int]
    camera_id: Optional[str]
    detected_at: str
    status: str
    notes: Optional[str]
    
    class Config:
        from_attributes = True


class ScanResponse(BaseModel):
    disk_files: int
    db_records: int
    orphaned_found: int
    new_records: int
    total_orphaned: int


class CleanupResponse(BaseModel):
    deleted: int
    failed: int
    total_processed: int


# ============================================================================
# API ENDPOINTS
# ============================================================================

@router.get("/api/orphaned-files", response_model=List[OrphanedFileResponse])
def get_orphaned_files(
    request: Request,
    status: Optional[str] = Query(None, description="Filter by status: pending, reviewed, deleted"),
    camera_id: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(operator_access_required)
):
    """Get list of orphaned files."""
    query = db.query(OrphanedFile)
    
    if status:
        query = query.filter(OrphanedFile.status == status)
    else:
        # Default: show pending and reviewed
        query = query.filter(OrphanedFile.status.in_(['pending', 'reviewed']))
    
    if camera_id:
        query = query.filter(OrphanedFile.camera_id == camera_id)
    
    total = query.count()
    files = query.order_by(OrphanedFile.detected_at.desc()).offset(offset).limit(limit).all()
    
    result = []
    for f in files:
        result.append({
            'id': f.id,
            'file_path': f.file_path,
            'file_size': f.file_size,
            'camera_id': f.camera_id,
            'detected_at': f.detected_at.isoformat() if f.detected_at else None,
            'status': f.status,
            'notes': f.notes
        })
    
    return JSONResponse({
        'files': result,
        'total': total,
        'limit': limit,
        'offset': offset
    })


@router.post("/api/orphaned-files/scan")
def scan_orphaned_files(
    request: Request,
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required)
):
    """Run orphaned file scan and sync to database."""
    try:
        logger.info("[OrphanedScan] Starting scan...")
        
        # Run scan step by step with error handling
        try:
            scanner = FolderScanner()
            manager = OrphanedFileManager()
            
            # Step 1: Scan folder
            logger.info("[OrphanedScan] Step 1: Scanning folder...")
            orphaned_list, disk_count, db_count = scanner.scan_for_orphaned(db)
            logger.info("[OrphanedScan] Found %d orphaned files on disk", len(orphaned_list))
            
            # Step 2: Sync to table
            logger.info("[OrphanedScan] Step 2: Syncing to table...")
            new_count, total_orphaned = scanner.sync_to_orphaned_table(db)
            logger.info("[OrphanedScan] Sync complete: %d new, %d total", new_count, total_orphaned)
            
            # Step 3: Get storage summary
            logger.info("[OrphanedScan] Step 3: Getting storage summary...")
            summary = manager.get_storage_summary(db)
            logger.info("[OrphanedScan] Summary: %s", summary)
            
            response_data = {
                'disk_files': int(disk_count),
                'db_records': int(db_count),
                'orphaned_found': int(len(orphaned_list)),
                'new_records': int(new_count),
                'total_orphaned': int(total_orphaned),
                'storage_summary': summary
            }
            
            logger.info("[OrphanedScan] Returning response: %s", response_data)
            return JSONResponse(content=response_data)
            
        except Exception as inner_e:
            logger.error("[OrphanedScan] Error during scan steps: %s", inner_e, exc_info=True)
            # Even if there's an error, try to return a valid response
            return JSONResponse(
                status_code=500,
                content={
                    'status': 'error',
                    'message': str(inner_e),
                    'disk_files': 0,
                    'db_records': 0,
                    'orphaned_found': 0,
                    'new_records': 0,
                    'total_orphaned': 0,
                    'storage_summary': {
                        'total_count': 0,
                        'total_size_bytes': 0,
                        'total_size_mb': 0,
                        'by_camera': []
                    }
                }
            )
    except Exception as e:
        logger.error("[OrphanedScan] Critical error: %s", e, exc_info=True)
        import traceback
        logger.error(traceback.format_exc())
        return JSONResponse(
            status_code=500,
            content={'status': 'error', 'message': str(e)}
        )


@router.get("/api/orphaned-files/summary")
def get_orphaned_summary(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(operator_access_required)
):
    """Get storage summary of orphaned files."""
    try:
        manager = OrphanedFileManager()
        summary = manager.get_storage_summary(db)
        return JSONResponse(summary)
    except Exception as e:
        logger.error("[OrphanedSummary] Error: %s", e, exc_info=True)
        return JSONResponse({
            'total_count': 0,
            'total_size_bytes': 0,
            'total_size_mb': 0,
            'by_camera': [],
            'error': str(e)
        })


@router.post("/api/orphaned-files/{file_id}/delete")
def delete_orphaned_file(
    request: Request,
    file_id: int,
    delete_from_disk: bool = Query(True),
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required)
):
    """Delete a specific orphaned file."""
    manager = OrphanedFileManager()
    
    success = manager.delete_file(db, file_id, delete_from_disk)
    
    if success:
        return JSONResponse({
            'status': 'success',
            'message': f'File {file_id} deleted successfully'
        })
    else:
        raise HTTPException(status_code=404, detail="File not found or could not be deleted")


@router.post("/api/orphaned-files/cleanup")
def cleanup_all_orphaned(
    request: Request,
    max_age_days: Optional[int] = Query(None, description="Only delete files older than N days"),
    dry_run: bool = Query(False, description="Show what would be deleted without deleting"),
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required)
):
    """Bulk cleanup orphaned files."""
    try:
        result = cleanup_orphaned_files(db, max_age_days=max_age_days, dry_run=dry_run)
        if dry_run:
            return JSONResponse({
                'dry_run': True,
                'would_delete': result.get('would_delete', 0),
                'would_free_bytes': result.get('would_free_bytes', 0),
                'would_free_mb': result.get('would_free_mb', 0)
            })
        else:
            return JSONResponse({
                'dry_run': False,
                'deleted': result.get('deleted', 0),
                'failed': result.get('failed', 0),
                'total_processed': result.get('total_processed', 0)
            })
    except Exception as e:
        logger.error("[OrphanedCleanup] Error: %s", e, exc_info=True)
        import traceback
        logger.error(traceback.format_exc())
        return JSONResponse(
            status_code=500,
            content={'status': 'error', 'message': str(e)}
        )


@router.get("/api/orphaned-files/test-scan")
def test_scan(
    request: Request,
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required)
):
    """Simple test endpoint for scan."""
    try:
        scanner = FolderScanner()
        disk_files = scanner.get_all_files_on_disk()
        db_paths = scanner.get_all_db_file_paths(db)
        
        return JSONResponse({
            'disk_file_count': len(disk_files),
            'db_record_count': len(db_paths),
            'status': 'ok'
        })
    except Exception as e:
        logger.error("[TestScan] Error: %s", e)
        return JSONResponse(
            status_code=500,
            content={'status': 'error', 'message': str(e)}
        )


@router.post("/api/orphaned-files/cleanup-disk")
def cleanup_all_orphaned_from_disk(
    request: Request,
    dry_run: bool = Query(False, description="Show what would be deleted without deleting"),
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required)
):
    """Bulk cleanup orphaned files directly from disk (bypass table check).
    
    This endpoint scans disk for files not in DB and deletes them directly.
    Use this when orphaned files exist on disk but are not in the orphaned_files table.
    """
    try:
        result = cleanup_orphaned_files_from_disk(db, dry_run=dry_run)
        
        if dry_run:
            return JSONResponse({
                'method': 'disk_scan',
                'dry_run': True,
                'would_delete': result.get('would_delete', 0),
                'would_free_mb': result.get('would_free_mb', 0)
            })
        else:
            return JSONResponse({
                'method': 'disk_scan',
                'dry_run': False,
                'deleted': result.get('deleted', 0),
                'failed': result.get('failed', 0),
                'mb_freed': result.get('mb_freed', 0)
            })
    except Exception as e:
        logger.error("[OrphanedCleanupDisk] Error: %s", e, exc_info=True)
        import traceback
        logger.error(traceback.format_exc())
        return JSONResponse(
            status_code=500,
            content={'status': 'error', 'message': str(e)}
        )


@router.post("/api/orphaned-files/{file_id}/review")
def review_orphaned_file(
    request: Request,
    file_id: int,
    notes: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required)
):
    """Mark orphaned file as reviewed."""
    manager = OrphanedFileManager()
    
    success = manager.review_file(db, file_id, notes)
    
    if success:
        return JSONResponse({
            'status': 'success',
            'message': f'File {file_id} marked as reviewed'
        })
    else:
        raise HTTPException(status_code=404, detail="File not found")


# ============================================================================
# DEBUG ENDPOINTS
# ============================================================================

@router.post("/api/orphaned-files/cleanup-debug")
def cleanup_debug(
    request: Request,
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required)
):
    """Debug cleanup process step by step."""
    from app.utils.orphaned_scanner import OrphanedFileManager
    from sqlalchemy import text
    import os
    
    manager = OrphanedFileManager()
    
    # Step 1: Check pending files in table
    pending = db.query(OrphanedFile).filter(OrphanedFile.status == 'pending').all()
    
    results = []
    for p in pending:
        full_path = manager.base_dir / p.file_path
        results.append({
            'id': p.id,
            'file_path': p.file_path,
            'full_path': str(full_path),
            'file_exists': full_path.exists(),
            'file_size': p.file_size
        })
    
    # Step 2: Try to delete first file as test
    test_result = None
    if pending:
        first = pending[0]
        full_path = manager.base_dir / first.file_path
        try:
            if full_path.exists():
                full_path.unlink()
                first.status = 'deleted'
                db.commit()
                test_result = {
                    'success': True,
                    'file': first.file_path,
                    'message': 'File deleted and DB updated'
                }
            else:
                test_result = {
                    'success': False,
                    'file': first.file_path,
                    'message': 'File not found on disk'
                }
        except Exception as e:
            test_result = {
                'success': False,
                'file': first.file_path,
                'message': str(e)
            }
    
    return JSONResponse({
        'pending_count': len(pending),
        'pending_files': results,
        'base_dir': str(manager.base_dir),
        'base_dir_exists': os.path.exists(manager.base_dir),
        'test_delete': test_result
    })


@router.get("/api/orphaned-files/debug")
def debug_orphaned_files(
    request: Request,
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required)
):
    """Debug endpoint to check orphaned files detection."""
    from app.utils.orphaned_scanner import FolderScanner
    from app.models.snapshot import Snapshot
    import os
    
    scanner = FolderScanner()
    
    # Get disk files
    disk_files = scanner.get_all_files_on_disk()
    
    # Get DB files
    db_paths = scanner.get_all_db_file_paths(db)
    
    # Check orphaned
    orphaned_files = []
    for file_path in disk_files:
        try:
            rel_path = file_path.relative_to(scanner.base_dir).as_posix()
            if rel_path not in db_paths:
                orphaned_files.append({
                    'absolute': str(file_path),
                    'relative': rel_path,
                    'size': file_path.stat().st_size if file_path.exists() else 0
                })
        except ValueError:
            continue
    
    # Check table orphaned_files
    table_count = db.query(OrphanedFile).filter(
        OrphanedFile.status.in_(['pending', 'reviewed'])
    ).count()
    
    pending_files = db.query(OrphanedFile).filter(
        OrphanedFile.status == 'pending'
    ).all()
    
    return JSONResponse({
        'disk_file_count': len(disk_files),
        'db_record_count': len(db_paths),
        'orphaned_detected': len(orphaned_files),
        'table_pending_count': len(pending_files),
        'table_total_active': table_count,
        'orphaned_sample': orphaned_files[:5],
        'pending_in_table': [
            {'id': f.id, 'path': f.file_path, 'status': f.status} 
            for f in pending_files[:5]
        ],
        'base_dir_exists': os.path.exists(scanner.base_dir),
        'base_dir': str(scanner.base_dir)
    })


# ============================================================================
# WEB UI ENDPOINTS
# ============================================================================

@router.get("/orphaned-files")
def orphaned_files_page(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(operator_access_required)
):
    """Render orphaned files management page."""
    manager = OrphanedFileManager()
    summary = manager.get_storage_summary(db)
    
    return templates.TemplateResponse("orphaned_files.html", {
        "request": request,
        "summary": summary
    })


@router.get("/orphaned-files/data")
def get_orphaned_files_data(
    request: Request,
    status: Optional[str] = Query(None),
    camera_id: Optional[str] = Query(None),
    limit: int = Query(50),
    offset: int = Query(0),
    db: Session = Depends(get_db),
    current_user: User = Depends(operator_access_required)
):
    """Get orphaned files data for AJAX refresh."""
    query = db.query(OrphanedFile)
    
    if status:
        query = query.filter(OrphanedFile.status == status)
    else:
        query = query.filter(OrphanedFile.status.in_(['pending', 'reviewed']))
    
    if camera_id:
        query = query.filter(OrphanedFile.camera_id == camera_id)
    
    total = query.count()
    files = query.order_by(OrphanedFile.detected_at.desc()).offset(offset).limit(limit).all()
    
    # Render partial template
    html = templates.get_template("_orphaned_files_list.html").render({
        "files": files
    })
    
    return JSONResponse({
        'html': html,
        'total': total,
        'limit': limit,
        'offset': offset
    })
