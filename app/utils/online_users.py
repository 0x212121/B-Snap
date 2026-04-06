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


class OnlineUserTracker:
    """Thread-safe tracker for online users.
    
    Tracks users based on their session activity. A user is considered "online"
    if they have made a request within the timeout period (default 5 minutes).
    """
    
    def __init__(self, timeout_seconds: int = 300):
        """Initialize the tracker.
        
        Args:
            timeout_seconds: Time in seconds after which a user is considered offline
                           (default: 300 = 5 minutes)
        """
        self._users: Dict[str, dict] = {}  # session_token -> user info
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
        
        Args:
            session_token: Unique session identifier
            user_id: User's database ID
            username: User's username
            role: User's role (admin, operator, viewer)
            ip_address: Client IP address
            user_agent: Client user agent string
        """
        with self._lock:
            self._users[session_token] = {
                "user_id": user_id,
                "username": username,
                "role": role,
                "last_activity": time.time(),
                "ip_address": ip_address,
                "user_agent": user_agent,
            }
    
    def remove_user(self, session_token: str) -> None:
        """Remove a user from tracking (e.g., on logout).
        
        Args:
            session_token: Unique session identifier
        """
        with self._lock:
            self._users.pop(session_token, None)
    
    def get_online_users(self) -> List[OnlineUserInfo]:
        """Get list of currently online users.
        
        Returns:
            List of online user information, sorted by username
        """
        current_time = time.time()
        online_users = []
        
        with self._lock:
            # Clean up expired sessions
            expired_tokens = [
                token for token, info in self._users.items()
                if current_time - info["last_activity"] > self._timeout
            ]
            for token in expired_tokens:
                del self._users[token]
            
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
                })
        
        # Sort by username
        online_users.sort(key=lambda x: x["username"].lower())
        return online_users
    
    def get_online_count(self) -> int:
        """Get count of currently online users.
        
        Returns:
            Number of unique users currently online
        """
        return len(self.get_online_users())
    
    def get_stats(self) -> dict:
        """Get statistics about online users.
        
        Returns:
            Dictionary containing online user statistics
        """
        users = self.get_online_users()
        
        role_counts = {}
        for user in users:
            role = user["role"]
            role_counts[role] = role_counts.get(role, 0) + 1
        
        return {
            "total_online": len(users),
            "by_role": role_counts,
            "users": users,
        }


# Global instance
_online_tracker: Optional[OnlineUserTracker] = None


def get_online_tracker() -> OnlineUserTracker:
    """Get or create the global online tracker instance.
    
    Returns:
        OnlineUserTracker singleton instance
    """
    global _online_tracker
    if _online_tracker is None:
        _online_tracker = OnlineUserTracker()
    return _online_tracker


def reset_tracker() -> None:
    """Reset the global tracker (useful for testing)."""
    global _online_tracker
    _online_tracker = None
