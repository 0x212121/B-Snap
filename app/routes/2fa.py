import io
from fastapi import APIRouter, Depends, HTTPException, Response
import pyotp
import qrcode

from app.db.database import update_user_secret
from app.models.user import User
from app.routes.auth import get_current_user

router = APIRouter()
# templates = Jinja2Templates(directory="templates")

@router.post("/2fa/enable")
async def enable_2fa_setup(current_user: User = Depends(get_current_user)):
    """
    Generates a new 2FA secret and a QR code for the user to scan.
    """
    if current_user.is_2fa_enabled:
        raise HTTPException(status_code=400, detail="2FA is already enabled.")

    # Generate a new secret
    secret = pyotp.random_base32()
    
    # Update the user's record in the database with the new secret
    # IMPORTANT: Store this secret securely! Encrypt it at rest.
    update_user_secret(username=current_user.username, secret=secret)

    # Generate the provisioning URI for authenticator apps
    # The 'issuer_name' should be your app's name, e.g., "b-snap"
    uri = pyotp.totp.TOTP(secret).provisioning_uri(
        name=current_user.username, 
        issuer_name="b-snap"
    )

    # Generate the QR code image from the URI
    img = qrcode.make(uri)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    buf.seek(0)
    
    # Return the QR code as an image response
    return Response(content=buf.getvalue(), media_type="image/png")