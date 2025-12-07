from fastapi import APIRouter, Request, HTTPException, Form
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from app.db.models import User, FileSystemItem
from app.routes.dashboard import get_current_user
from app.core.config import settings

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

@router.get("/admin")
async def admin_panel(request: Request):
    user = await get_current_user(request)
    if not user: return RedirectResponse("/login")
    
    admin_phone = getattr(settings, "ADMIN_PHONE", "").replace(" ", "")
    user_phone = user.phone_number.replace(" ", "")

    if user_phone != admin_phone:
        raise HTTPException(status_code=403, detail="Not authorized.")

    total_users = await User.count()
    total_files = await FileSystemItem.find(FileSystemItem.is_folder == False).count()
    all_users = await User.find_all().to_list()

    return templates.TemplateResponse("admin.html", {
        "request": request, "total_users": total_users, "total_files": total_files, 
        "users": all_users, "user_email": user.phone_number
    })

@router.post("/admin/delete_user")
async def delete_user(request: Request, user_phone: str = Form(...)):
    """Deletes a user from the DB"""
    user = await get_current_user(request)
    # Re-verify admin
    if user.phone_number.replace(" ", "") != getattr(settings, "ADMIN_PHONE", "").replace(" ", ""):
        raise HTTPException(403)
    
    target = await User.find_one(User.phone_number == user_phone)
    if target:
        await target.delete()
        # Optional: Delete their files too
        await FileSystemItem.find(FileSystemItem.owner_phone == user_phone).delete()
    
    return RedirectResponse("/admin", status_code=303)