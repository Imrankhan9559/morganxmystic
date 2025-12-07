import os
import shutil
import tempfile
import traceback
import mimetypes 
import uuid
import zipfile
import asyncio
from typing import Optional, Dict, List

from fastapi import APIRouter, Request, UploadFile, File, Form, BackgroundTasks, Body
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse, JSONResponse, FileResponse
from pyrogram import Client
from beanie.operators import Or, In
from app.db.models import FileSystemItem, FilePart, User, SharedCollection
from app.core.config import settings
from app.utils.file_utils import format_size, get_icon_for_mime
from starlette.background import BackgroundTask

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
mimetypes.init()

# --- IN-MEMORY JOB TRACKER ---
upload_jobs: Dict[str, dict] = {}

async def get_current_user(request: Request):
    phone = request.cookies.get("user_phone")
    if not phone: return None
    return await User.find_one(User.phone_number == phone)

# --- HELPER 1: Recursively Create Folder Structure (Uploads) ---
async def get_or_create_folder_path(user_phone: str, start_parent_id: Optional[str], path_parts: list) -> Optional[str]:
    current_parent_id = start_parent_id
    for folder_name in path_parts:
        existing = await FileSystemItem.find_one(
            FileSystemItem.owner_phone == user_phone,
            FileSystemItem.parent_id == current_parent_id,
            FileSystemItem.name == folder_name,
            FileSystemItem.is_folder == True
        )
        if existing:
            current_parent_id = str(existing.id)
        else:
            new_folder = FileSystemItem(name=folder_name, is_folder=True, parent_id=current_parent_id, owner_phone=user_phone)
            await new_folder.insert()
            current_parent_id = str(new_folder.id)
    return current_parent_id

# --- HELPER 2: Recursive Download for Zip (Downloads) ---
async def download_item_recursive(client, item, base_path):
    """
    Downloads a file OR recursively downloads a folder contents to the base_path.
    """
    try:
        if item.is_folder:
            # 1. Create the folder locally
            new_folder_path = os.path.join(base_path, item.name)
            os.makedirs(new_folder_path, exist_ok=True)
            
            # 2. Find children
            children = await FileSystemItem.find(FileSystemItem.parent_id == str(item.id)).to_list()
            
            # 3. Recurse for each child
            for child in children:
                await download_item_recursive(client, child, new_folder_path)
        else:
            # It's a file, download it
            # Refresh file ref by getting message again
            try:
                msg = await client.get_messages("me", message_ids=item.parts[0].message_id)
                
                file_id = None
                if msg.document: file_id = msg.document.file_id
                elif msg.video: file_id = msg.video.file_id
                elif msg.audio: file_id = msg.audio.file_id
                elif msg.photo: file_id = msg.photo.file_id
                
                if file_id:
                    # Save to: base_path/filename
                    await client.download_media(file_id, file_name=os.path.join(base_path, item.name))
            except Exception as inner_e:
                print(f"Failed to refresh/download {item.name}: {inner_e}")
                
    except Exception as e:
        print(f"Error processing {item.name}: {e}")

# --- BACKGROUND UPLOAD TASK ---
async def process_telegram_upload(job_id: str, file_path: str, filename: str, mime_type: str, parent_id: Optional[str], user_phone: str, session_string: str):
    try:
        upload_jobs[job_id]["status"] = "uploading"
        async with Client("uploader", api_id=settings.API_ID, api_hash=settings.API_HASH, session_string=session_string, in_memory=True) as app:
            async def progress(current, total):
                percent = (current / total) * 100
                upload_jobs[job_id]["progress"] = round(percent, 2)

            msg = await app.send_document(
                chat_id="me", 
                document=file_path,
                file_name=filename,
                caption="Uploaded via MorganXMystic",
                force_document=True,
                progress=progress
            )
            
            new_file = FileSystemItem(
                name=filename,
                is_folder=False,
                parent_id=parent_id,
                owner_phone=user_phone,
                size=msg.document.file_size,
                mime_type=mime_type,
                parts=[FilePart(telegram_file_id=msg.document.file_id, message_id=msg.id, part_number=1, size=msg.document.file_size)]
            )
            await new_file.insert()
            upload_jobs[job_id]["status"] = "completed"
            upload_jobs[job_id]["progress"] = 100
    except Exception as e:
        print(f"Upload Failed: {e}")
        upload_jobs[job_id]["status"] = "failed"
        upload_jobs[job_id]["error"] = str(e)
    finally:
        if os.path.exists(file_path):
            try: os.remove(file_path)
            except: pass

@router.get("/")
async def root(): return RedirectResponse(url="/dashboard")

# --- DASHBOARD ---
@router.get("/dashboard")
async def dashboard(request: Request, folder_id: Optional[str] = None):
    user = await get_current_user(request)
    if not user: return RedirectResponse("/login")
    if folder_id == "None" or folder_id == "": folder_id = None

    if folder_id:
        items = await FileSystemItem.find(FileSystemItem.parent_id == folder_id).to_list()
    else:
        items = await FileSystemItem.find(
            Or(FileSystemItem.owner_phone == user.phone_number, FileSystemItem.collaborators == user.phone_number),
            FileSystemItem.parent_id == None
        ).to_list()

    current_folder = await FileSystemItem.get(folder_id) if folder_id else None
    visible_items = []
    
    for item in items:
        if item.owner_phone == user.phone_number or user.phone_number in item.collaborators or folder_id:
            item.formatted_size = format_size(item.size)
            item.icon = "fa-folder" if item.is_folder else get_icon_for_mime(item.mime_type)
            if not item.share_token: item.share_token = ""
            visible_items.append(item)

    return templates.TemplateResponse("dashboard.html", {
        "request": request, "items": visible_items, "current_folder": current_folder, "user": user
    })

# --- UPLOAD ROUTES ---
@router.get("/upload_zone")
async def upload_page(request: Request, folder_id: Optional[str] = None):
    user = await get_current_user(request)
    if not user: return RedirectResponse("/login")
    user_jobs = {k: v for k, v in upload_jobs.items() if v.get("owner") == user.phone_number}
    return templates.TemplateResponse("upload.html", {"request": request, "folder_id": folder_id, "user": user, "jobs": user_jobs})

@router.post("/upload")
async def upload_file(request: Request, background_tasks: BackgroundTasks, file: UploadFile = File(...), parent_id: str = Form(""), relative_path: str = Form("")):
    user = await get_current_user(request)
    if not user: return JSONResponse({"error": "Unauthorized"}, 401)

    try:
        # Clean Filename (No paths)
        original_name = file.filename or "unknown_file"
        safe_filename = os.path.basename(file.filename or "unknown_file")
        mime_type, _ = mimetypes.guess_type(safe_filename)
        if not mime_type: mime_type = file.content_type or "application/octet-stream"
        
        final_parent_id = parent_id if parent_id and parent_id != "None" else None

        # Folder Logic
        if relative_path and "/" in relative_path:
            path_parts = relative_path.split("/")[:-1]
            if path_parts:
                final_parent_id = await get_or_create_folder_path(user.phone_number, final_parent_id, path_parts)

        job_id = str(uuid.uuid4())
        fd, tmp_path = tempfile.mkstemp()
        os.close(fd)
        with open(tmp_path, "wb") as buffer: shutil.copyfileobj(file.file, buffer)

        upload_jobs[job_id] = {"id": job_id, "filename": safe_filename, "status": "queued", "progress": 0, "owner": user.phone_number}
        background_tasks.add_task(process_telegram_upload, job_id, tmp_path, safe_filename, mime_type, final_parent_id, user.phone_number, user.session_string)
        return JSONResponse({"status": "queued", "job_id": job_id})
    except Exception as e: return JSONResponse({"error": str(e)}, 500)

@router.get("/upload/status")
async def get_upload_status(request: Request):
    user = await get_current_user(request)
    if not user: return JSONResponse({})
    user_jobs = {k: v for k, v in upload_jobs.items() if v.get("owner") == user.phone_number}
    return JSONResponse(user_jobs)

# --- BULK DOWNLOAD (ZIP) ---
@router.post("/download/zip")
async def download_zip(request: Request, item_ids: List[str] = Body(...)):
    user = await get_current_user(request)
    if not user: return JSONResponse({"error": "Unauthorized"}, 401)

    items = await FileSystemItem.find(In(FileSystemItem.id, item_ids)).to_list()
    if not items: return JSONResponse({"error": "No items found"}, 404)

    temp_dir = tempfile.mkdtemp()
    zip_filename = f"MorganCloud_Bundle_{uuid.uuid4().hex[:6]}.zip"
    zip_path = os.path.join(tempfile.gettempdir(), zip_filename)

    try:
        async with Client("downloader", api_id=settings.API_ID, api_hash=settings.API_HASH, session_string=user.session_string, in_memory=True) as app:
            for item in items:
                # Use recursive downloader to handle folders
                await download_item_recursive(app, item, temp_dir)

        shutil.make_archive(zip_path.replace('.zip', ''), 'zip', temp_dir)
        shutil.rmtree(temp_dir)

        return FileResponse(zip_path, filename=zip_filename, background=BackgroundTask(lambda: os.remove(zip_path)))

    except Exception as e:
        if os.path.exists(temp_dir): shutil.rmtree(temp_dir)
        traceback.print_exc()
        return JSONResponse({"error": str(e)}, 500)

# --- BULK DELETE ---
@router.post("/delete/bundle")
async def delete_bundle(request: Request, item_ids: List[str] = Body(...)):
    user = await get_current_user(request)
    if not user: return JSONResponse({"error": "Unauthorized"}, 401)
    await FileSystemItem.find(In(FileSystemItem.id, item_ids), FileSystemItem.owner_phone == user.phone_number).delete()
    return JSONResponse({"status": "success"})

# --- STANDARD ACTIONS ---
@router.post("/delete/{item_id}")
async def delete_item(request: Request, item_id: str):
    user = await get_current_user(request)
    if not user: return RedirectResponse("/login")
    item = await FileSystemItem.get(item_id)
    if item and (item.owner_phone == user.phone_number or user.phone_number in item.collaborators):
        await item.delete()
    return RedirectResponse(f"/dashboard?folder_id={item.parent_id if item and item.parent_id else ''}", 303)

@router.post("/share/{item_id}")
async def share_item(request: Request, item_id: str):
    user = await get_current_user(request)
    if not user: return JSONResponse({"error": "Auth required"}, 401)
    item = await FileSystemItem.get(item_id)
    if not item: return JSONResponse({"error": "Not found"}, 404)
    if not item.share_token:
        item.share_token = str(uuid.uuid4())
        await item.save()
    base_url = str(request.base_url).rstrip("/")
    return JSONResponse({"link": f"{base_url}/s/{item.share_token}"})

@router.post("/create_folder")
async def create_folder(request: Request, folder_name: str = Form(...), parent_id: str = Form("")):
    user = await get_current_user(request)
    if not user: return RedirectResponse("/login")
    final_parent_id = parent_id if parent_id and parent_id != "None" else None
    await FileSystemItem(name=folder_name, is_folder=True, parent_id=final_parent_id, owner_phone=user.phone_number).insert()
    return RedirectResponse(url=f"/dashboard?folder_id={final_parent_id}" if final_parent_id else "/dashboard", status_code=303)

# --- COLLAB ROUTES ---
@router.get("/folder/team/{folder_id}")
async def get_folder_team(request: Request, folder_id: str):
    user = await get_current_user(request)
    if not user: return JSONResponse({"error": "Auth required"}, 401)
    folder = await FileSystemItem.get(folder_id)
    if not folder: return JSONResponse({"error": "Not found"}, 404)
    if folder.owner_phone != user.phone_number and user.phone_number not in folder.collaborators:
        return JSONResponse({"error": "Unauthorized"}, 403)
    return JSONResponse({"collaborators": folder.collaborators, "owner": folder.owner_phone})

@router.post("/folder/add_collaborator")
async def add_collaborator(request: Request, folder_id: str = Form(...), phone: str = Form(...)):
    user = await get_current_user(request)
    if not user: return JSONResponse({"error": "Auth required"}, 401)
    folder = await FileSystemItem.get(folder_id)
    if not folder or folder.owner_phone != user.phone_number: return JSONResponse({"error": "Owner only"}, 403)
    if phone not in folder.collaborators:
        folder.collaborators.append(phone)
        await folder.save()
    return JSONResponse({"status": "success"})

@router.post("/folder/remove_collaborator")
async def remove_collaborator(request: Request, folder_id: str = Form(...), phone: str = Form(...)):
    user = await get_current_user(request)
    if not user: return JSONResponse({"error": "Auth required"}, 401)
    folder = await FileSystemItem.get(folder_id)
    if not folder or folder.owner_phone != user.phone_number: return JSONResponse({"error": "Owner only"}, 403)
    if phone in folder.collaborators:
        folder.collaborators.remove(phone)
        await folder.save()
        return JSONResponse({"status": "success"})
    return JSONResponse({"error": "User not found"}, 404)

@router.post("/share/bundle")
async def create_bundle(request: Request, item_ids: List[str] = Body(...)):
    user = await get_current_user(request)
    if not user: return {"error": "Unauthorized"}
    token = str(uuid.uuid4())
    bundle = SharedCollection(token=token, item_ids=item_ids, owner_phone=user.phone_number, name=f"Shared by {user.first_name or 'User'}")
    await bundle.insert()
    base_url = str(request.base_url).rstrip("/")
    return {"link": f"{base_url}/s/{token}"}

@router.get("/profile")
async def profile_page(request: Request):
    user = await get_current_user(request)
    if not user: return RedirectResponse("/login")
    total_files = await FileSystemItem.find(FileSystemItem.owner_phone == user.phone_number, FileSystemItem.is_folder == False).count()
    all_files = await FileSystemItem.find(FileSystemItem.owner_phone == user.phone_number, FileSystemItem.is_folder == False).sort("-created_at").to_list()
    for item in all_files:
        item.formatted_size = format_size(item.size)
        item.icon = get_icon_for_mime(item.mime_type)
    return templates.TemplateResponse("profile.html", {"request": request, "user": user, "total_files": total_files, "files": all_files})