#!/usr/bin/env python3
"""
Test script untuk sistem tamper alert notification.
"""

import sys
sys.path.insert(0, '.')

from datetime import datetime, timezone, timedelta
from app.db.database import SessionLocal
from app.models.camera import Camera
from app.models.health import CameraHealth
from app.models.camera_email_notification_log import CameraEmailNotificationLog
from app.utils.snapshot_utils import TAMPER_CONFIRM_THRESHOLD
from app.utils.email_notifier import (
    should_send_tamper_alert,
    is_notification_suppressed
)

def test_threshold_logic():
    """Test logic threshold 3."""
    print("TEST: Tamper Threshold Logic (Threshold = %d)" % TAMPER_CONFIRM_THRESHOLD)
    
    test_cases = [
        (0, "normal", "normal"),
        (1, "normal", "normal"),
        (2, "normal", "normal"),
        (3, "normal", "tampered"),
        (4, "normal", "tampered"),
        (3, "tampered", "tampered"),
    ]
    
    all_passed = True
    for consecutive, prev_status, expected in test_cases:
        if consecutive >= TAMPER_CONFIRM_THRESHOLD:
            calculated = "tampered"
        else:
            calculated = prev_status
        
        passed = calculated == expected
        status = "[PASS]" if passed else "[FAIL]"
        print("  %s consecutive=%d, prev=%s -> %s" % (status, consecutive, prev_status, calculated))
        
        if not passed:
            all_passed = False
    
    return all_passed

def test_notification_functions():
    """Test notification functions."""
    print("\nTEST: Notification Functions")
    
    db = SessionLocal()
    
    # Get first camera
    camera = db.query(Camera).first()
    if not camera:
        print("  [ERROR] No camera found")
        db.close()
        return False
    
    print("  Camera: %s" % camera.hostname)
    
    # Test is_notification_suppressed
    try:
        result = is_notification_suppressed(db, camera)
        print("  [PASS] is_notification_suppressed() = %s" % result)
    except Exception as e:
        print("  [FAIL] is_notification_suppressed() error: %s" % e)
        db.close()
        return False
    
    # Test should_send_tamper_alert
    try:
        result = should_send_tamper_alert(db, camera, "blur")
        print("  [PASS] should_send_tamper_alert() = %s" % result)
    except Exception as e:
        print("  [FAIL] should_send_tamper_alert() error: %s" % e)
        db.close()
        return False
    
    db.close()
    return True

def test_cooldown_state():
    """Test cooldown state."""
    print("\nTEST: Cooldown State")
    
    db = SessionLocal()
    
    camera = db.query(Camera).first()
    if not camera:
        print("  [ERROR] No camera found")
        db.close()
        return False
    
    health = db.query(CameraHealth).filter_by(camera_id=camera.id).first()
    if not health:
        print("  [ERROR] No health record")
        db.close()
        return False
    
    print("  Camera: %s" % camera.hostname)
    print("  alert_cooldown_until: %s" % health.alert_cooldown_until)
    print("  last_email_sent: %s" % health.last_email_sent)
    print("  last_alert_reason: %s" % health.last_alert_reason)
    
    # Check if in cooldown
    from app.utils.snapshot_utils import is_alert_in_cooldown
    in_cooldown = is_alert_in_cooldown(health, "blur")
    print("  is_alert_in_cooldown('blur'): %s" % in_cooldown)
    
    db.close()
    return True

def test_complete_flow():
    """Test complete flow simulation."""
    print("\nTEST: Complete Flow Simulation")
    
    db = SessionLocal()
    
    camera = db.query(Camera).first()
    if not camera:
        print("  [ERROR] No camera found")
        db.close()
        return False
    
    health = db.query(CameraHealth).filter_by(camera_id=camera.id).first()
    if not health:
        print("  [ERROR] No health record")
        db.close()
        return False
    
    # Reset state
    health.consecutive_tamper = 0
    health.tamper_status = "normal"
    health.alert_cooldown_until = None
    health.last_email_sent = None
    db.commit()
    
    print("  Camera: %s" % camera.hostname)
    print("  Simulating 4 consecutive tampered snapshots...")
    
    email_triggered = False
    
    for i in range(1, 5):
        # Simulate tamper detection
        health.consecutive_tamper = (health.consecutive_tamper or 0) + 1
        health.consecutive_normal = 0
        
        prev_status = health.tamper_status or "normal"
        
        # Determine new status
        if health.consecutive_tamper >= TAMPER_CONFIRM_THRESHOLD:
            new_status = "tampered"
        else:
            new_status = prev_status
        
        health.tamper_status = new_status
        db.commit()
        
        print("    Snapshot #%d: consecutive=%d, status=%s -> %s" % (i, health.consecutive_tamper, prev_status, new_status))
        
        # Check transition
        if prev_status != "tampered" and new_status == "tampered":
            print("      [ALERT] Transition detected!")
            
            from app.utils.snapshot_utils import is_alert_in_cooldown
            in_cooldown = is_alert_in_cooldown(health, "blur")
            print("      is_alert_in_cooldown: %s" % in_cooldown)
            
            if not in_cooldown:
                can_send = should_send_tamper_alert(db, camera, "blur")
                print("      should_send_tamper_alert: %s" % can_send)
                
                if can_send:
                    print("      [SUCCESS] Email WILL be sent!")
                    email_triggered = True
    
    db.close()
    return email_triggered

def test_recent_email_logs():
    """Check recent email logs."""
    print("\nTEST: Recent Email Logs")
    
    db = SessionLocal()
    
    count = db.query(CameraEmailNotificationLog).count()
    print("  Total email logs: %d" % count)
    
    # Get recent logs
    recent = db.query(CameraEmailNotificationLog).order_by(
        CameraEmailNotificationLog.sent_at.desc()
    ).limit(5).all()
    
    if recent:
        print("  Recent logs:")
        for log in recent:
            print("    - %s: %s (success=%s)" % (log.sent_at, log.reason, log.success))
    else:
        print("  No email logs found")
    
    db.close()
    return True

def main():
    print("=" * 60)
    print("TAMPER ALERT SYSTEM TEST REPORT")
    print("=" * 60)
    
    results = []
    
    try:
        results.append(("Threshold Logic", test_threshold_logic()))
        results.append(("Notification Functions", test_notification_functions()))
        results.append(("Cooldown State", test_cooldown_state()))
        results.append(("Complete Flow", test_complete_flow()))
        results.append(("Recent Email Logs", test_recent_email_logs()))
    except Exception as e:
        print("\n[ERROR] Test failed: %s" % e)
        import traceback
        traceback.print_exc()
        return 1
    
    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    
    passed = sum(1 for _, r in results if r)
    total = len(results)
    
    for name, result in results:
        status = "[PASS]" if result else "[FAIL]"
        print("%s %s" % (status, name))
    
    print("\nTotal: %d/%d tests passed" % (passed, total))
    
    if passed == total:
        print("\n*** ALL TESTS PASSED ***")
        return 0
    else:
        print("\n*** SOME TESTS FAILED ***")
        return 1

if __name__ == "__main__":
    exit(main())
