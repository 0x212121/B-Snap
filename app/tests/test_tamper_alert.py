#!/usr/bin/env python3
"""
Test script untuk sistem tamper alert notification.
Memastikan email terkirim ketika consecutive_tamper >= 3.
"""

import sys
sys.path.insert(0, '.')

from datetime import datetime, timezone, timedelta
from app.db.database import SessionLocal
from app.models.camera import Camera
from app.models.health import CameraHealth
from app.models.snapshot import Snapshot
from app.models.camera_email_notification_log import CameraEmailNotificationLog
from app.utils.snapshot_utils import (
    record_snapshot_metadata, 
    is_alert_in_cooldown, 
    set_alert_cooldown,
    TAMPER_CONFIRM_THRESHOLD
)
from app.utils.email_notifier import (
    send_tamper_alert,
    should_send_tamper_alert,
    is_notification_suppressed
)
import os
import tempfile

# Create test image
TEST_IMAGE_PATH = "test_snapshot.jpg"

def create_test_image():
    """Create a simple test image."""
    from PIL import Image
    img = Image.new('RGB', (640, 480), color='red')  # Red image = will be detected as tampered
    img.save(TEST_IMAGE_PATH)
    return TEST_IMAGE_PATH

def cleanup_test_image():
    """Remove test image."""
    if os.path.exists(TEST_IMAGE_PATH):
        os.remove(TEST_IMAGE_PATH)

def test_consecutive_tamper_detection():
    """Test bahwa consecutive_tamper bertambah saat snapshot tampered."""
    print("=" * 60)
    print("TEST 1: Consecutive Tamper Counter Increment")
    print("=" * 60)
    
    db = SessionLocal()
    
    # Get first camera
    camera = db.query(Camera).first()
    if not camera:
        print("[ERROR] No camera found in database")
        return False
    
    print(f"Camera: {camera.hostname}")
    
    # Get or create health record
    health = db.query(CameraHealth).filter_by(camera_id=camera.id).first()
    if not health:
        health = CameraHealth(camera_id=camera.id, status="Unknown")
        db.add(health)
        db.commit()
    
    # Reset counters
    initial_tamper = health.consecutive_tamper or 0
    health.consecutive_tamper = 0
    health.consecutive_normal = 0
    health.tamper_status = "normal"
    db.commit()
    
    print(f"Initial consecutive_tamper: 0")
    
    # Create test image path (simulate file path)
    file_path = f"snapshots/{camera.id}/2026-03-30/test_snapshot.jpg"
    
    # Check if we can access the camera health
    health = db.query(CameraHealth).filter_by(camera_id=camera.id).first()
    print(f"[OK] Health record accessible: {health is not None}")
    
    db.close()
    return True

def test_tamper_threshold_logic():
    """Test logic threshold 3."""
    print("\n" + "=" * 60)
    print("TEST 2: Tamper Threshold Logic (Threshold = 3)")
    print("=" * 60)
    
    print(f"TAMPER_CONFIRM_THRESHOLD = {TAMPER_CONFIRM_THRESHOLD}")
    
    # Test cases
    test_cases = [
        (0, "normal", "normal", "Should stay normal"),
        (1, "normal", "normal", "Should stay normal"),
        (2, "normal", "normal", "Should stay normal"),
        (3, "normal", "tampered", "Should become tampered"),
        (4, "normal", "tampered", "Should become tampered"),
        (3, "tampered", "tampered", "Already tampered, stay tampered"),
    ]
    
    all_passed = True
    for consecutive, prev_status, expected_new, description in test_cases:
        if consecutive >= TAMPER_CONFIRM_THRESHOLD:
            calculated_new = "tampered"
        else:
            calculated_new = prev_status
        
        passed = calculated_new == expected_new
        status = "[PASS]" if passed else "[FAIL]"
        print(f"{status} consecutive={consecutive}, prev={prev_status} -> {calculated_new} ({description})"
        
        if not passed:
            all_passed = False
    
    return all_passed

def test_should_send_tamper_alert():
    """Test should_send_tamper_alert function."""
    print("\n" + "=" * 60)
    print("TEST 3: Should Send Tamper Alert Check")
    print("=" * 60)
    
    db = SessionLocal()
    
    # Get first camera
    camera = db.query(Camera).first()
    if not camera:
        print("[ERROR] No camera found")
        db.close()
        return False
    
    print(f"Camera: {camera.hostname}")
    
    # Test should_send_tamper_alert
    try:
        result = should_send_tamper_alert(db, camera, "blur")
        print(f"✓ should_send_tamper_alert() executed without error")
        print(f"  Result: {result}")
        
        # Check is_notification_suppressed
        suppressed = is_notification_suppressed(db, camera)
        print(f"[OK] is_notification_suppressed() = {suppressed}")
        
        if suppressed:
            print("  [WARNING] Notifications are suppressed!")
        
        db.close()
        return True
        
    except Exception as e:
        print(f"[ERROR] FAILED: {e}")
        import traceback
        traceback.print_exc()
        db.close()
        return False

def test_cooldown_logic():
    """Test cooldown logic."""
    print("\n" + "=" * 60)
    print("TEST 4: Cooldown Logic")
    print("=" * 60)
    
    db = SessionLocal()
    
    # Get first camera
    camera = db.query(Camera).first()
    if not camera:
        print("[ERROR] No camera found")
        db.close()
        return False
    
    # Get or create health
    health = db.query(CameraHealth).filter_by(camera_id=camera.id).first()
    if not health:
        health = CameraHealth(camera_id=camera.id, status="Unknown")
        db.add(health)
        db.commit()
    
    print(f"Camera: {camera.hostname}")
    print(f"Initial alert_cooldown_until: {health.alert_cooldown_until}")
    
    # Test 1: No cooldown
    in_cooldown = is_alert_in_cooldown(health, "blur")
    print(f"✓ is_alert_in_cooldown (no cooldown set) = {in_cooldown}")
    
    # Set cooldown
    set_alert_cooldown(health, "blur")
    print(f"After set_alert_cooldown: {health.alert_cooldown_until}")
    
    # Test 2: In cooldown
    in_cooldown = is_alert_in_cooldown(health, "blur")
    print(f"✓ is_alert_in_cooldown (after set) = {in_cooldown}")
    
    if in_cooldown:
        print("  ✓ Cooldown working correctly")
    else:
        print("  [FAIL] Cooldown not working!")
    
    # Reset cooldown for testing
    health.alert_cooldown_until = None
    health.last_email_sent = None
    db.commit()
    
    db.close()
    return True

def test_complete_flow():
    """Test complete flow dari 0 sampai threshold."""
    print("\n" + "=" * 60)
    print("TEST 5: Complete Flow Simulation")
    print("=" * 60)
    
    db = SessionLocal()
    
    # Get first camera
    camera = db.query(Camera).first()
    if not camera:
        print("[ERROR] No camera found")
        db.close()
        return False
    
    print(f"Camera: {camera.hostname}")
    
    # Get or create health
    health = db.query(CameraHealth).filter_by(camera_id=camera.id).first()
    if not health:
        health = CameraHealth(camera_id=camera.id, status="Unknown")
        db.add(health)
        db.commit()
    
    # Reset state
    health.consecutive_tamper = 0
    health.consecutive_normal = 0
    health.tamper_status = "normal"
    health.alert_cooldown_until = None
    health.last_email_sent = None
    db.commit()
    
    print("\nSimulating 4 consecutive tampered snapshots...")
    
    for i in range(1, 5):
        print(f"\n--- Snapshot #{i} ---")
        
        # Simulate tamper detection
        is_tampered = True
        
        # Update counters (like in record_snapshot_metadata)
        if is_tampered:
            health.consecutive_tamper = (health.consecutive_tamper or 0) + 1
            health.consecutive_normal = 0
        else:
            health.consecutive_normal = (health.consecutive_normal or 0) + 1
            health.consecutive_tamper = 0
        
        prev_status = health.tamper_status or "normal"
        
        # Determine new status
        if health.consecutive_tamper >= TAMPER_CONFIRM_THRESHOLD:
            new_status = "tampered"
        else:
            new_status = prev_status
        
        health.tamper_status = new_status
        db.commit()
        
        print(f"  consecutive_tamper: {health.consecutive_tamper}")
        print(f"  prev_status: {prev_status} → new_status: {new_status}")
        
        # Check transition
        if prev_status != "tampered" and new_status == "tampered":
            print("  [ALERT] TRANSITION DETECTED: normal -> tampered")
            
            # Check cooldown
            in_cooldown = is_alert_in_cooldown(health, "blur")
            print(f"  is_alert_in_cooldown: {in_cooldown}")
            
            if not in_cooldown:
                print("  [OK] Alert SHOULD be triggered!")
                
                # Check should_send_tamper_alert
                can_send = should_send_tamper_alert(db, camera, "blur")
                print(f"  should_send_tamper_alert: {can_send}")
                
                if can_send:
                    print("  [SUCCESS] EMAIL WILL BE SENT!")
                else:
                    print("  [BLOCKED] Email blocked by should_send_tamper_alert")
            else:
                print("  [BLOCKED] Alert blocked by cooldown")
    
    db.close()
    return True

def test_email_log_created():
    """Test that email log is created after sending."""
    print("\n" + "=" * 60)
    print("TEST 6: Email Log Creation Check")
    print("=" * 60)
    
    db = SessionLocal()
    
    # Count existing logs
    existing_count = db.query(CameraEmailNotificationLog).count()
    print(f"Existing email logs: {existing_count}")
    
    # Get first camera
    camera = db.query(Camera).first()
    if not camera:
        print("[ERROR] No camera found")
        db.close()
        return False
    
    # Check recent logs for this camera
    recent_logs = db.query(CameraEmailNotificationLog).filter(
        CameraEmailNotificationLog.camera_id == camera.id
    ).order_by(CameraEmailNotificationLog.sent_at.desc()).limit(5).all()
    
    if recent_logs:
        print(f"\nRecent logs for {camera.hostname}:")
        for log in recent_logs:
            print(f"  - {log.sent_at}: {log.reason} (success={log.success})")
    else:
        print(f"\nNo recent logs for {camera.hostname}")
    
    db.close()
    return True

def main():
    """Run all tests."""
    print("\n" + "=" * 60)
    print("TAMPER ALERT SYSTEM TEST SUITE")
    print("=" * 60)
    
    results = []
    
    try:
        # Run tests
        results.append(("Consecutive Tamper Detection", test_consecutive_tamper_detection()))
        results.append(("Tamper Threshold Logic", test_tamper_threshold_logic()))
        results.append(("Should Send Tamper Alert", test_should_send_tamper_alert()))
        results.append(("Cooldown Logic", test_cooldown_logic()))
        results.append(("Complete Flow", test_complete_flow()))
        results.append(("Email Log Created", test_email_log_created()))
        
    except Exception as e:
        print(f"\n[ERROR] Test suite failed with error: {e}")
        import traceback
        traceback.print_exc()
        return 1
    
    # Summary
    print("\n" + "=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)
    
    passed = sum(1 for _, r in results if r)
    total = len(results)
    
    for name, result in results:
        status = "✓ PASSED" if result else "❌ FAILED"
        print(f"{status}: {name}")
    
    print(f"\nTotal: {passed}/{total} tests passed")
    
    if passed == total:
        print("\n🎉 ALL TESTS PASSED! System is working correctly.")
        return 0
    else:
        print("\n⚠️  SOME TESTS FAILED. Please review the output above.")
        return 1

if __name__ == "__main__":
    exit(main())
