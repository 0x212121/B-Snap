"""Online user tracking utility for B-SNAP.

This module provides functionality to track users who are currently online
based on their session activity.
"""

import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, TypedDict
from threading import Lock


class OnlineUserInfo(TypedDict):
    """Type definition for online user information."""
    user_id: int
    username: str
    role: str
    last_activity: str
    ip_address: Optional[str]
    user_agent: Optional[str]
    session_count: int  # NEW: Track berapa session aktif untuk user ini


class OnlineUserTracker:
    """Thread-safe tracker for online users.
    
    Tracks users based on user_id (bukan session_token) untuk menghindari
    duplicate counting saat session rotation (Remember Me).
    """
    
    def __init__(self, timeout_seconds: int = 300):
        """Initialize the tracker.
        
        Args:
            timeout_seconds: Time in seconds after which a user is considered offline
                           (default: 300 = 5 minutes)
        """
        # KEY CHANGE: Gunakan user_id sebagai key, bukan session_token
        self._users: Dict[int, dict] = {}  # user_id -> user info
        self._session_map: Dict[str, int] = {}  # session_token -> user_id (untuk lookup reverse)
        self._timeout = timeout_seconds
        self._lock = Lock()
    
    def update_activity(
        self,
        session_token: str,
        user_id: int,
        username: str,
        role: str,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None
    ) -> None:
        """Update user activity timestamp.
        
        Called on each request to keep track of active users.
        Menggunakan user_id sebagai primary key untuk menghindari duplicate
        saat session token berubah (Remember Me rotation).
        
        Args:
            session_token: Unique session identifier (secondary key)
            user_id: User's database ID (primary key)
            username: User's username
            role: User's role (admin, operator, viewer)
            ip_address: Client IP address
            user_agent: Client user agent string
        """
        with self._lock:
            current_time = time.time()
            
            # Cek apakah user sudah ada
            if user_id in self._users:
                # Update existing user
                user_data = self._users[user_id]
                user_data["last_activity"] = current_time
                user_data["ip_address"] = ip_address
                user_data["user_agent"] = user_agent
                
                # Track session token baru (untuk keperluan revoke)
                old_session = user_data.get("session_token")
                if old_session != session_token:
                    # Hapus mapping lama, tambah mapping baru
                    self._session_map.pop(old_session, None)
                    self._session_map[session_token] = user_id
                    user_data["session_token"] = session_token
                    # Increment session count jika token berbeda (rotation)
                    if old_session:
                        user_data["session_count"] = user_data.get("session_count", 1) + 1
                        logger.debug(f"Session rotated for user {username}: {old_session[:8]}... -> {session_token[:8]}...")
            else:
                # User baru - create entry
                self._users[user_id] = {
                    "user_id": user_id,
                    "username": username,
                    "role": role,
                    "last_activity": current_time,
                    "ip_address": ip_address,
                    "user_agent": user_agent,
                    "session_token": session_token,
                    "session_count": 1,
                }
                self._session_map[session_token] = user_id
    
    def remove_user(self, session_token: str) -> Optional[int]:
        """Remove a user session dari tracking.
        
        Args:
            session_token: Unique session identifier
            
        Returns:
            user_id yang di-remove, atau None jika tidak ditemukan
        """
        with self._lock:
            user_id = self._session_map.pop(session_token, None)
            if user_id and user_id in self._users:
                user_data = self._users[user_id]
                current_count = user_data.get("session_count", 1)
                
                if current_count <= 1:
                    # Last session - remove user entirely
                    del self._users[user_id]
                    logger.info(f"User {user_data['username']} (ID: {user_id}) removed from online users (last session)")
                else:
                    # Decrement session count tapi keep user (masih ada session lain)
                    user_data["session_count"] = current_count - 1
                    logger.info(f"User {user_data['username']} session reduced to {current_count - 1}")
                
                return user_id
            return None
    
    def remove_user_by_id(self, user_id: int) -> bool:
        """Remove semua session untuk user tertentu (force logout).
        
        Args:
            user_id: User ID yang mau di-remove
            
        Returns:
            True jika berhasil di-remove, False jika tidak ditemukan
        """
        with self._lock:
            if user_id in self._users:
                user_data = self._users.pop(user_id)
                # Bersihkan semua session mapping untuk user ini
                sessions_to_remove = [
                    token for token, uid in self._session_map.items() 
                    if uid == user_id
                ]
                for token in sessions_to_remove:
                    self._session_map.pop(token, None)
                logger.info(f"Force removed user {user_data['username']} (ID: {user_id}) and {len(sessions_to_remove)} session(s)")
                return True
            return False
    
    def cleanup_expired(self) -> List[int]:
        """Remove expired users (tidak ada aktivitas dalam timeout period).
        
        Returns:
            List of user_ids yang di-remove karena expired
        """
        current_time = time.time()
        expired_users = []
        
        with self._lock:
            expired_user_ids = [
                user_id for user_id, info in self._users.items()
                if current_time - info["last_activity"] > self._timeout
            ]
            
            for user_id in expired_user_ids:
                user_data = self._users.pop(user_id)
                # Bersihkan session mapping
                sessions_to_remove = [
                    token for token, uid in self._session_map.items()
                    if uid == user_id
                ]
                for token in sessions_to_remove:
                    self._session_map.pop(token, None)
                expired_users.append(user_id)
                logger.debug(f"Expired user removed: {user_data['username']} (ID: {user_id})")
        
        return expired_users
    
    def get_online_users(self) -> List[OnlineUserInfo]:
        """Get list of currently online users.
        
        Returns:
            List of online user information, sorted by username
        """
        current_time = time.time()
        online_users = []
        
        with self._lock:
            # Cleanup inline (opsional, bisa juga rely pada scheduler)
            expired_ids = [
                uid for uid, info in self._users.items()
                if current_time - info["last_activity"] > self._timeout
            ]
            for uid in expired_ids:
                self._users.pop(uid, None)
            
            # Build response
            for info in self._users.values():
                online_users.append({
                    "user_id": info["user_id"],
                    "username": info["username"],
                    "role": info["role"],
                    "last_activity": datetime.fromtimestamp(
                        info["last_activity"], tz=timezone.utc
                    ).isoformat(),
                    "ip_address": info.get("ip_address"),
                    "user_agent": info.get("user_agent"),
                    "session_count": info.get("session_count", 1),
                })
        
        # Sort by username
        online_users.sort(key=lambda x: x["username"].lower())
        return online_users
    
    def get_online_count(self) -> int:
        """Get count of unique users currently online."""
        return len(self.get_online_users())
    
    def get_stats(self) -> dict:
        """Get statistics about online users."""
        users = self.get_online_users()
        
        role_counts = {}
        total_sessions = 0
        for user in users:
            role = user["role"]
            role_counts[role] = role_counts.get(role, 0) + 1
            total_sessions += user.get("session_count", 1)
        
        return {
            "total_online": len(users),  # UNIQUE users, bukan total sessions
            "total_sessions": total_sessions,  # NEW: Total session count
            "by_role": role_counts,
            "users": users,
        }


# Global instance
_online_tracker: Optional[OnlineUserTracker] = None
import logging
logger = logging.getLogger("online_users")


def get_online_tracker() -> OnlineUserTracker:
    """Get or create the global online tracker instance."""
    global _online_tracker
    if _online_tracker is None:
        _online_tracker = OnlineUserTracker()
    return _online_tracker


def reset_tracker() -> None:
    """Reset the global tracker (useful for testing)."""
    global _online_tracker
    _online_tracker = None