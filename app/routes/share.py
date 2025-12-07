import uuid
from typing import List
from fastapi import APIRouter, Request, HTTPException, Body
from fastapi.responses import StreamingResponse
from fastapi.templating import Jinja2Templates
from pyrogram import Client
from beanie.operators import In
from app.db.models import FileSystemItem, User, SharedCollection
from app.core.config import settings
from app.routes.stream import telegram_stream_generator
from app.utils.file_utils import format_size, get_icon_for_mime
from app.routes.dashboard import get_current_user

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

@router.post("/share/bundle")
async def create_bundle(request: Request, item_ids: List[str] = Body(...)):
    user = await get_current_user(request)
    if not user: return {"error": "Unauthorized"}
    token = str(uuid.uuid4())
    bundle = SharedCollection(token=token, item_ids=item_ids, owner_phone=user.phone_number, name=f"Shared by {user.first_name or 'User'}")
    await bundle.insert()
    base_url = str(request.base_url).rstrip("/")
    return {"link": f"{base_url}/s/{token}"}

@router.get("/s/{token}")
async def public_view(request: Request, token: str):
    # Bundle Check
    collection = await SharedCollection.find_one(SharedCollection.token == token)
    if collection:
        items = await FileSystemItem.find(In(FileSystemItem.id, collection.item_ids)).to_list()
        for item in items:
            item.formatted_size = format_size(item.size)
            item.icon = "fa-folder" if item.is_folder else get_icon_for_mime(item.mime_type)
        return templates.TemplateResponse("shared_folder.html", {"request": request, "items": items, "bundle_name": collection.name})

    # Single File Check
    item = await FileSystemItem.find_one(FileSystemItem.share_token == token)
    if item:
        item.formatted_size = format_size(item.size)
        item.icon = get_icon_for_mime(item.mime_type)
        return templates.TemplateResponse("shared.html", {"request": request, "item": item, "stream_url": f"/s/stream/{token}"})

    raise HTTPException(404, "Link expired")

@router.get("/s/stream/file/{item_id}")
async def public_stream_by_id(item_id: str):
    item = await FileSystemItem.get(item_id)
    if not item: raise HTTPException(404)
    owner = await User.find_one(User.phone_number == item.owner_phone)
    
    client = Client("pub_stream", api_id=settings.API_ID, api_hash=settings.API_HASH, session_string=owner.session_string, in_memory=True)
    await client.connect()

    async def cleanup():
        try:
            msg_id = item.parts[0].message_id
            async for chunk in telegram_stream_generator(client, msg_id, 0):
                yield chunk
        finally:
            await client.disconnect()

    headers = {'Content-Disposition': f'inline; filename="{item.name}"', 'Content-Type': item.mime_type}
    return StreamingResponse(cleanup(), headers=headers, media_type=item.mime_type)

@router.get("/s/stream/{token}")
async def public_stream_token(token: str):
    item = await FileSystemItem.find_one(FileSystemItem.share_token == token)
    if not item: raise HTTPException(404)
    return await public_stream_by_id(str(item.id))