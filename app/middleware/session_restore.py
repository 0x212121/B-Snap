from starlette.middleware.base import BaseHTTPMiddleware
from fastapi import Request
from sqlalchemy.orm import Session
from sqlalchemy import text
from app.models_sql import User
from app.db.database import SessionLocal
import json
from app.utils.auth_token import is_valid_web_token

class RestoreSessionMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        session_token = request.cookies.get("session_token")

        if session_token:
            try:
                # Hanya injeksi ke session kalau belum ada user_id atau user_role
                if "user_role" not in request.session or "user_id" not in request.session:
                    db: Session = SessionLocal()
                    try:
                        raw_query = text("""
                            SELECT * FROM users
                            WHERE EXISTS (
                                SELECT 1 FROM jsonb_array_elements(web_tokens) AS elem
                                WHERE elem->>'token' = :token
                            )
                            LIMIT 1
                        """)
                        result = db.execute(raw_query, {"token": session_token}).first()

                        if result:
                            user = db.query(User).get(result.id)
                            tokens = json.loads(user.web_tokens) if isinstance(user.web_tokens, str) else user.web_tokens

                            if is_valid_web_token(session_token, tokens):
                                request.session["user_id"] = user.id
                                request.session["user_role"] = user.role
                    finally:
                        db.close()
            except Exception as e:
                # Hindari error keras di middleware
                print(f"[RestoreSessionMiddleware] Failed: {e}")

        response = await call_next(request)
        return response
