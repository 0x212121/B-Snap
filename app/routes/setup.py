from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from passlib.hash import bcrypt
from app.models_sql import CameraGroup, User
from app.db.database import SessionLocal
from fastapi.templating import Jinja2Templates

router = APIRouter()

# Setup templates
templates = Jinja2Templates(directory="templates")

# @router.get("/setup", response_class=HTMLResponse)
# def setup_form(request: Request):
#     db = SessionLocal()
#     user_exists = db.query(User).first()
#     db.close()
#     if user_exists:
#         return RedirectResponse(url="/login", status_code=302)
#     return HTMLResponse("""
#         <form method="post">
#             <input name="username" placeholder="Username" required><br>
#             <input name="password" type="password" placeholder="Password" required><br>
#             <button type="submit">Create Admin</button>
#         </form>
#     """)

@router.post("/setup")
def setup_create(username: str = Form(...), password: str = Form(...)):
    db = SessionLocal()
    try:
        group = db.query(CameraGroup).filter(CameraGroup.name == "ALL").first()

        if not group:
            raise HTTPException(status_code=404, detail="Default group 'ALL' not found")

        print(f"Group ID: {group.id}")

        if db.query(User).first():
            return RedirectResponse(url="/login", status_code=302)

        hashed = bcrypt.hash(password)
        user = User(username=username, password=hashed, role="admin", group_id=group.id)

        db.add(user)
        db.commit()
        db.refresh(user)
        print("User group_id after commit:", user.group_id)

        return RedirectResponse(url="/login", status_code=302)

    finally:
        db.close()


