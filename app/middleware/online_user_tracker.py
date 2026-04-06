"""Middleware to track online users.

This middleware updates the online user tracker on each request
to maintain an accurate list of currently active users.
"""

from starlette.middleware.base import BaseHTTPMiddleware
from fastapi import Request

from app.utils.online_users import get_online_tracker


class OnlineUserTrackerMiddleware(BaseHTTPMiddleware):
    """Middleware that tracks user activity for online status.
    
    This middleware checks if the request has an authenticated user
    and updates their last activity timestamp in the online tracker.
    """
    
    async def dispatch(self, request: Request, call_next):
        """Process request and update online user tracking."""
        # Get session info
        session_token = request.cookies.get("session_token")
        user_id = request.session.get("user_id")
        user_name = request.session.get("user_name")
        user_role = request.session.get("user_role")
        
        # Track user if authenticated
        if session_token and user_id and user_name:
            tracker = get_online_tracker()
            tracker.update_activity(
                session_token=session_token,
                user_id=user_id,
                username=user_name,
                role=user_role or "viewer",
                ip_address=request.client.host if request.client else None,
                user_agent=request.headers.get("user-agent"),
            )
        
        # Continue with the request
        response = await call_next(request)
        return response
