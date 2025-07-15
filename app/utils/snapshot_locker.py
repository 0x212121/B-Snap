from threading import Lock

_camera_locks = {}


def get_camera_lock(camera_id: str) -> Lock:
    if camera_id not in _camera_locks:
        _camera_locks[camera_id] = Lock()
    return _camera_locks[camera_id]