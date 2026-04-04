"""Create 100 dummy video records for testing."""
import os
import sys
sys.path.insert(0, '.')

from datetime import datetime, timezone, timedelta
import random
import uuid

from app.db.database import SessionLocal
from app.models.video import Video
from app.models.camera import Camera

def main():
    db = SessionLocal()
    
    # Check if videos already exist
    existing_count = db.query(Video).count()
    if existing_count > 0:
        print(f"Videos table already has {existing_count} records.")
        response = input("Continue adding 100 more? (y/n): ")
        if response.lower() != 'y':
            print("Aborted.")
            db.close()
            return
    
    # Get existing cameras
    cameras = db.query(Camera).all()
    if cameras:
        camera_ids = [c.id for c in cameras]
        # Camera uses 'hostname' not 'name'
        camera_names = {c.id: c.hostname for c in cameras}
        print(f"Found {len(cameras)} cameras to associate with videos.")
    else:
        camera_ids = [f'cam-{i:03d}' for i in range(1, 11)]
        camera_names = {cid: f'Camera-{cid}' for cid in camera_ids}
        print("No cameras found. Using dummy camera names.")
    
    # Generate 100 dummy videos
    base_time = datetime.now(timezone.utc) - timedelta(days=30)
    resolutions = ['1920x1080', '1280x720', '640x480', '2560x1440', '3840x2160']
    groups = ['Building A', 'Building B', 'Parking', 'Warehouse', 'Entrance', None]
    
    dummy_videos = []
    for i in range(100):
        camera_id = random.choice(camera_ids) if cameras else None
        cam_name = camera_names.get(camera_id, f'Camera {random.randint(1, 20)}')
        
        # Random timestamp within last 30 days
        timestamp = base_time + timedelta(
            days=random.randint(0, 30),
            hours=random.randint(0, 23),
            minutes=random.randint(0, 59)
        )
        
        # Random file size between 10MB and 500MB
        file_size = random.randint(10 * 1024 * 1024, 500 * 1024 * 1024)
        
        # Random duration between 10s and 300s
        duration = random.randint(10, 300)
        
        # Build file path
        date_str = timestamp.strftime("%Y%m%d")
        time_str = timestamp.strftime("%H%M%S")
        safe_cam_name = cam_name.replace(" ", "_")
        file_path = f"videos/{date_str}/{safe_cam_name}_{time_str}_{i:04d}.mp4"
        
        video = Video(
            id=str(uuid.uuid4()),
            camera_id=camera_id,
            camera_name=cam_name,
            camera_ip=f"192.168.1.{random.randint(10, 250)}",
            camera_group=random.choice(groups),
            timestamp=timestamp,
            file_path=file_path,
            file_size=file_size,
            duration=duration,
            resolution=random.choice(resolutions),
            file_hash=None,  # Skip hash for dummy data
            deleted_at=None,
            retention_hold=random.choice([True, False, False, False]),  # 25% chance
            retention_hold_reason=None,
            retention_hold_by=None,
            retention_hold_at=None
        )
        
        if video.retention_hold:
            video.retention_hold_reason = random.choice([
                'Investigation Case #2024-001',
                'Incident Review',
                'Legal Hold',
                'Audit Required'
            ])
            video.retention_hold_by = 'admin'
            video.retention_hold_at = timestamp
        
        dummy_videos.append(video)
    
    db.add_all(dummy_videos)
    db.commit()
    
    print(f"\n[OK] Created {len(dummy_videos)} dummy videos:")
    print(f"   - Associated with cameras: {sum(1 for v in dummy_videos if v.camera_id)}")
    print(f"   - With retention hold: {sum(1 for v in dummy_videos if v.retention_hold)}")
    print(f"   - Date range: {min(v.timestamp for v in dummy_videos).date()} to {max(v.timestamp for v in dummy_videos).date()}")
    print(f"   - Total size: {sum(v.file_size for v in dummy_videos) / (1024**3):.2f} GB")
    
    db.close()

if __name__ == "__main__":
    main()
