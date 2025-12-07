import traceback
from fastapi import APIRouter, Request, Form, Response
from fastapi.templating import Jinja2Templates
from fastapi.responses import JSONResponse, RedirectResponse
from pyrogram import Client, errors
from app.core.config import settings
from app.db.models import User

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

# In-memory storage for temporary login steps (Production apps should use Redis)
temp_auth_data = {} 

@router.get("/login")
async def login_page(request: Request):
    """Renders the login page."""
    return templates.TemplateResponse("login.html", {"request": request, "step": "phone"})

@router.get("/logout")
async def logout(response: Response):
    """Logs the user out by clearing the cookie."""
    response = RedirectResponse(url="/login")
    response.delete_cookie("user_phone")
    return response

@router.post("/auth/send_code")
async def send_code(phone: str = Form(...)):
    """Step 1: Connect to Telegram and send OTP."""
    try:
        # Create a temporary client just for this auth flow
        client = Client(
            name=f"auth_{phone}",
            api_id=settings.API_ID,
            api_hash=settings.API_HASH,
            in_memory=True
        )
        await client.connect()
        
        # Send Code
        sent_code = await client.send_code(phone)
        
        # Store phone_code_hash temporarily
        temp_auth_data[phone] = {
            "phone_code_hash": sent_code.phone_code_hash,
            "client": client # Keep connection open
        }
        
        return JSONResponse({"status": "success", "message": "Code sent"})
        
    except Exception as e:
        traceback.print_exc()
        return JSONResponse({"error": str(e)}, status_code=400)

@router.post("/auth/verify_code")
async def verify_code(response: Response, phone: str = Form(...), code: str = Form(...)):
    """Step 2: Verify OTP and Login."""
    if phone not in temp_auth_data:
        return JSONResponse({"error": "Session expired. Try again."}, status_code=400)
    
    data = temp_auth_data[phone]
    client = data["client"]
    phone_code_hash = data["phone_code_hash"]

    try:
        # Attempt Sign In
        user_info = await client.sign_in(phone, phone_code_hash, code)
        
        # If successful, export session string
        session_string = await client.export_session_string()
        await client.disconnect()
        del temp_auth_data[phone] # Cleanup

        # Save/Update User in DB
        await save_user_to_db(phone, session_string, user_info)

        # --- SET COOKIE (IFRAME COMPATIBLE) ---
        response = JSONResponse({"status": "success"})
        response.set_cookie(
            key="user_phone", 
            value=phone, 
            httponly=True, 
            samesite='none', # Crucial for Iframes
            secure=True      # Required for samesite=none
        )
        return response

    except errors.SessionPasswordNeeded:
        # 2FA Required
        return JSONResponse({"status": "2fa_required"})
        
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)

@router.post("/auth/verify_password")
async def verify_password(response: Response, phone: str = Form(...), password: str = Form(...)):
    """Step 3 (Optional): Verify 2FA Password."""
    if phone not in temp_auth_data:
        return JSONResponse({"error": "Session expired."}, status_code=400)

    data = temp_auth_data[phone]
    client = data["client"]

    try:
        user_info = await client.check_password(password)
        
        session_string = await client.export_session_string()
        await client.disconnect()
        del temp_auth_data[phone]

        # Save/Update User
        await save_user_to_db(phone, session_string, user_info)

        # --- SET COOKIE (IFRAME COMPATIBLE) ---
        response = JSONResponse({"status": "success"})
        response.set_cookie(
            key="user_phone", 
            value=phone, 
            httponly=True, 
            samesite='none', # Crucial for Iframes
            secure=True      # Required for samesite=none
        )
        return response

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)

async def save_user_to_db(phone, session_string, user_info):
    """Helper to save user data to MongoDB."""
    existing_user = await User.find_one(User.phone_number == phone)
    
    first_name = user_info.first_name if hasattr(user_info, 'first_name') else "User"
    
    if existing_user:
        existing_user.session_string = session_string
        existing_user.first_name = first_name
        await existing_user.save()
    else:
        new_user = User(
            phone_number=phone,
            session_string=session_string,
            first_name=first_name
        )
        await new_user.insert()
