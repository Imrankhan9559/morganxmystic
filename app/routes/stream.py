import math
from fastapi import APIRouter, Request, HTTPException, Header
from fastapi.responses import StreamingResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from pyrogram import Client
from app.db.models import FileSystemItem, User
from app.core.config import settings

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

async def get_current_user(request: Request):
    phone = request.cookies.get("user_phone")
    if not phone: return None
    return await User.find_one(User.phone_number == phone)

async def telegram_stream_generator(client: Client, message_id: int, offset: int):
    try:
        # Refresh File Reference
        msg = await client.get_messages("me", message_ids=message_id)
        
        file_id = None
        if msg.document: file_id = msg.document.file_id
        elif msg.video: file_id = msg.video.file_id
        elif msg.audio: file_id = msg.audio.file_id
        elif msg.photo: file_id = msg.photo.file_id
        else: yield b""; return

        async for chunk in client.stream_media(file_id, offset=offset, limit=0):
            yield chunk
    except Exception as e:
        print(f"Stream Error: {e}")
        yield b""

@router.get("/player/{item_id}", response_class=HTMLResponse)
async def player_page(request: Request, item_id: str):
    user = await get_current_user(request)
    
    # If not logged in, redirect to login page
    if not user: 
        return templates.TemplateResponse("login.html", {"request": request, "step": "phone"})

    item = await FileSystemItem.get(item_id)
    if not item: raise HTTPException(404, "File not found")

    return templates.TemplateResponse("player.html", {
        "request": request,
        "item": item,
        "stream_url": f"/stream/data/{item_id}",
        "user": user  # <--- FIX: This was missing! Now the navbar will show 'Profile'
    })

@router.get("/stream/data/{item_id}")
async def stream_data(request: Request, item_id: str, range: str = Header(None)):
    user = await get_current_user(request)
    if not user: raise HTTPException(401)

    item = await FileSystemItem.get(item_id)
    if not item: raise HTTPException(404)

    client = Client("streamer", api_id=settings.API_ID, api_hash=settings.API_HASH, session_string=user.session_string, in_memory=True)
    await client.connect()

    file_size = item.size
    start = 0
    end = file_size - 1

    if range:
        try:
            start_str = range.replace("bytes=", "").split("-")[0]
            start = int(start_str) if start_str else 0
        except ValueError: pass

    async def cleanup_generator():
        try:
            msg_id = item.parts[0].message_id
            async for chunk in telegram_stream_generator(client, msg_id, start):
                yield chunk
        finally:
            await client.disconnect()

    headers = {
        'Content-Range': f'bytes {start}-{end}/{file_size}',
        'Accept-Ranges': 'bytes',
        'Content-Length': str(file_size - start),
        'Content-Type': item.mime_type or "application/octet-stream",
        'Content-Disposition': f'inline; filename="{item.name}"'
    }

    return StreamingResponse(cleanup_generator(), status_code=206 if range else 200, headers=headers, media_type=item.mime_type)