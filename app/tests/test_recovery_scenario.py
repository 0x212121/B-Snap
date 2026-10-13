#!/usr/bin/env python3
"""
Test scenario: Camera gagal terus lalu recover.
Verifikasi suppression behavior saat recovery.
"""

import sys
sys.path.insert(0, '.')

from datetime import datetime, timezone, timedelta
from app.db.database import SessionLocal
from app.models.camera import Camera
from app.models.health import CameraHealth
from app.utils.email_notifier import (
    record_notification_failure,
    record_notification_success,
    reset_notification_suppression,
    is_notification_suppressed
)

def test_recovery_resets_suppression():
    """Test bahwa recovery reset suppression."""
    print("=" * 60)
    print("TEST: Recovery Resets Suppression")
    print("=" * 60)
    
    db = SessionLocal()
    
    # Get first camera
    camera = db.query(Camera).first()
    if not camera:
        print("[ERROR] No camera found")
        return False
    
    print(f"Camera: {camera.hostname}")
    print()
    
    # Step 1: Simulate 3 failures (suppression threshold)
    print("Step 1: Simulate 3 consecutive failures...")
    record_notification_failure(db, camera, "Error 1")
    record_notification_failure(db, camera, "Error 2")
    record_notification_failure(db, camera, "Error 3")
    
    # Refresh from DB
    db.refresh(camera)
    print(f"  Fail count: {camera.notification_fail_count}")
    print(f"  Suppressed until: {camera.notification_suppressed_until}")
    
    suppressed = is_notification_suppressed(db, camera)
    print(f"  Is suppressed: {suppressed}")
    
    if not suppressed:
        print("  [ERROR] Should be suppressed after 3 failures!")
        return False
    print("  [OK] Camera is suppressed")
    print()
    
    # Step 2: Camera recovers
    print("Step 2: Camera recovers (reset suppression)...")
    reset_notification_suppression(db, camera.id)
    
    # Refresh from DB
    db.refresh(camera)
    print(f"  Fail count after reset: {camera.notification_fail_count}")
    print(f"  Suppressed until: {camera.notification_suppressed_until}")
    
    suppressed = is_notification_suppressed(db, camera)
    print(f"  Is suppressed: {suppressed}")
    
    if suppressed:
        print("  [ERROR] Should NOT be suppressed after recovery!")
        return False
    print("  [OK] Suppression reset - camera can send alerts again")
    print()
    
    # Step 3: Camera tampered again, threshold 3 applies again
    print("Step 3: Camera tampered again...")
    print("  consecutive_tamper = 1 -> status = normal")
    print("  consecutive_tamper = 2 -> status = normal")
    print("  consecutive_tamper = 3 -> status = TAMPERED -> ALERT!")
    print()
    
    # Step 4: If email fails again
    print("Step 4: If email fails again...")
    record_notification_failure(db, camera, "Error after recovery")
    
    db.refresh(camera)
    print(f"  Fail count: {camera.notification_fail_count}")
    suppressed = is_notification_suppressed(db, camera)
    print(f"  Is suppressed: {suppressed}")
    
    if suppressed:
        print("  [ERROR] Should not be suppressed after 1 failure!")
        return False
    print("  [OK] Not suppressed (fail_count < 3)")
    print()
    
    # Cleanup
    camera.notification_fail_count = 0
    camera.notification_suppressed_until = None
    db.commit()
    
    db.close()
    return True

def main():
    print("\nRECOVERY SCENARIO TEST")
    print("Scenario: Camera fails 3x (suppressed) -> recovers -> tampered again\n")
    
    try:
        result = test_recovery_resets_suppression()
    except Exception as e:
        print(f"\n[ERROR] Test failed: {e}")
        import traceback
        traceback.print_exc()
        return 1
    
    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)
    
    if result:
        print("[PASS] Recovery correctly resets suppression")
        print("\nKey Points:")
        print("  1. After 3 failures: camera is SUPPRESSED")
        print("  2. After recovery: suppression is RESET (fail_count = 0)")
        print("  3. Camera can trigger alerts again")
        print("  4. Threshold 3 applies again for new tamper events")
        return 0
    else:
        print("[FAIL] Test failed")
        return 1

if __name__ == "__main__":
    exit(main())
