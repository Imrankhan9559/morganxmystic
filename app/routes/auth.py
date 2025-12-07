from fastapi import APIRouter, Request, Form
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from pyrogram import Client, errors
from app.db.models import User
from app.core.config import settings

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

# Temporary storage for login steps
temp_login_cache = {} 

@router.get("/login")
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "step": "phone"})

@router.post("/auth/send_code")
async def send_code(request: Request, phone: str = Form(...)):
    """Step 1: Send OTP"""
    client = Client(f"login_{phone}", api_id=settings.API_ID, api_hash=settings.API_HASH, in_memory=True)
    await client.connect()
    
    try:
        sent_code = await client.send_code(phone)
        temp_login_cache[phone] = {"client": client, "phone_code_hash": sent_code.phone_code_hash}
        return templates.TemplateResponse("login.html", {"request": request, "step": "code", "phone": phone})
    except Exception as e:
        await client.disconnect()
        return templates.TemplateResponse("login.html", {"request": request, "step": "phone", "error": str(e)})

@router.post("/auth/verify_code")
async def verify_code(request: Request, phone: str = Form(...), code: str = Form(...)):
    """Step 2: Verify OTP. If 2FA is on, it will fail here and ask for password."""
    if phone not in temp_login_cache: return RedirectResponse("/login")
    
    cache = temp_login_cache[phone]
    client: Client = cache["client"]
    
    try:
        await client.sign_in(phone, cache["phone_code_hash"], code)
        return await finalize_login(client, phone)

    except errors.SessionPasswordNeeded:
        # 2FA IS ENABLED! Show Password Input
        return templates.TemplateResponse("login.html", {
            "request": request, 
            "step": "password", 
            "phone": phone
        })
        
    except Exception as e:
        return templates.TemplateResponse("login.html", {"request": request, "step": "code", "phone": phone, "error": str(e)})

@router.post("/auth/verify_password")
async def verify_password(request: Request, phone: str = Form(...), password: str = Form(...)):
    """Step 3: Verify 2FA Password"""
    if phone not in temp_login_cache: return RedirectResponse("/login")
    
    client: Client = temp_login_cache[phone]["client"]
    
    try:
        await client.check_password(password)
        return await finalize_login(client, phone)
    except Exception as e:
         return templates.TemplateResponse("login.html", {
            "request": request, 
            "step": "password", 
            "phone": phone,
            "error": "Wrong Password"
        })

async def finalize_login(client, phone):
    """Helper to save user and redirect"""
    session_string = await client.export_session_string()
    user_info = await client.get_me()
    
    # Save to DB
    existing_user = await User.find_one(User.phone_number == phone)
    if not existing_user:
        new_user = User(phone_number=phone, session_string=session_string, first_name=user_info.first_name)
        await new_user.insert()
    else:
        existing_user.session_string = session_string
        await existing_user.save()
        
    await client.disconnect()
    del temp_login_cache[phone]
    
    response = RedirectResponse("/dashboard", status_code=303)
    response.set_cookie(key="user_phone", value=phone)
    return response

@router.get("/logout")
async def logout():
    response = RedirectResponse("/login")
    response.delete_cookie("user_phone")
    return response