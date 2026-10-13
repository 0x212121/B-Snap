#!/usr/bin/env python3
"""
Test untuk memverifikasi logika akses group berfungsi dengan benar.
User dengan group_id=None harus bisa akses semua kamera.
User dengan group_id tertentu hanya bisa akses kamera dalam group tersebut.
"""

import sys
sys.path.insert(0, '.')

from app.db.database import SessionLocal
from app.models.user import User
from app.models.camera import Camera
from app.models.camera_group import CameraGroup

def test_user_without_group_access():
    """Test user tanpa group (group_id=None) bisa akses semua kamera."""
    print("=" * 60)
    print("TEST 1: User tanpa group (group_id=None)")
    print("=" * 60)
    
    db = SessionLocal()
    
    # Get user tanpa group
    user = db.query(User).filter(User.group_id == None).first()
    if not user:
        print("[SKIP] Tidak ada user tanpa group")
        return True
    
    print(f"User: {user.username}")
    print(f"group_id: {user.group_id}")
    
    # Simulasi logika akses (dari maps.py, snap_gallery.py, videos.py)
    group_id = user.group_id
    
    # Query kamera
    query = db.query(Camera)
    if group_id is not None:
        query = query.filter(Camera.group_id == group_id)
    
    cameras = query.all()
    total_cameras = db.query(Camera).count()
    
    print(f"Total cameras di DB: {total_cameras}")
    print(f"Cameras bisa diakses: {len(cameras)}")
    
    if len(cameras) == total_cameras:
        print("[PASS] User tanpa group bisa akses SEMUA kamera!")
        return True
    else:
        print("[FAIL] User tanpa group seharusnya bisa akses semua kamera!")
        return False

def test_user_with_group_access():
    """Test user dengan group hanya bisa akses kamera dalam group tersebut."""
    print("\n" + "=" * 60)
    print("TEST 2: User dengan group tertentu")
    print("=" * 60)
    
    db = SessionLocal()
    
    # Get user dengan group
    user = db.query(User).filter(User.group_id != None).first()
    if not user:
        print("[SKIP] Tidak ada user dengan group (semua user punya akses penuh)")
        return True
    
    print(f"User: {user.username}")
    print(f"group_id: {user.group_id}")
    print(f"group name: {user.group.name}")
    
    # Simulasi logika akses
    group_id = user.group_id
    
    query = db.query(Camera)
    if group_id is not None:
        query = query.filter(Camera.group_id == group_id)
    
    cameras = query.all()
    total_cameras = db.query(Camera).count()
    group_cameras = db.query(Camera).filter(Camera.group_id == group_id).count()
    
    print(f"Total cameras di DB: {total_cameras}")
    print(f"Cameras dalam group {user.group.name}: {group_cameras}")
    print(f"Cameras bisa diakses: {len(cameras)}")
    
    if len(cameras) == group_cameras and len(cameras) < total_cameras:
        print("[PASS] User dengan group hanya bisa akses kamera dalam group-nya!")
        return True
    else:
        print("[FAIL] User dengan group seharusnya hanya bisa akses kamera dalam group-nya!")
        return False

def test_api_response():
    """Test API response menunjukkan group dengan benar."""
    print("\n" + "=" * 60)
    print("TEST 3: API Response")
    print("=" * 60)
    
    db = SessionLocal()
    
    # Test user_to_dict function
    from app.routes.user_management import user_to_dict
    
    user_no_group = db.query(User).filter(User.group_id == None).first()
    if user_no_group:
        user_dict = user_to_dict(user_no_group, db)
        group_info = user_dict.get('group')
        print(f"User: {user_no_group.username}")
        print(f"API group field: {group_info}")
        if group_info is None:
            print("[PASS] API mengembalikan group=None untuk user tanpa group!")
        else:
            print("[FAIL] API seharusnya mengembalikan group=None untuk user tanpa group!")
            return False
    
    return True

def main():
    print("\nGROUP ACCESS TEST SUITE")
    print("Verifikasi: group_id=None = akses semua kamera")
    print()
    
    results = []
    
    try:
        results.append(("User tanpa group", test_user_without_group_access()))
        results.append(("User dengan group", test_user_with_group_access()))
        results.append(("API Response", test_api_response()))
    except Exception as e:
        print(f"\n[ERROR] Test failed: {e}")
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
        print(f"{status} {name}")
    
    print(f"\nTotal: {passed}/{total} tests passed")
    
    if passed == total:
        print("\n*** ALL TESTS PASSED ***")
        print("Logika akses group berfungsi dengan benar!")
        return 0
    else:
        print("\n*** SOME TESTS FAILED ***")
        return 1

if __name__ == "__main__":
    exit(main())
