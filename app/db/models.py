from typing import Optional, List
from beanie import Document, init_beanie
from pydantic import BaseModel, Field, ConfigDict
from motor.motor_asyncio import AsyncIOMotorClient
from datetime import datetime
from app.core.config import settings

class User(Document):
    phone_number: str = Field(unique=True)
    session_string: str
    first_name: Optional[str] = None
    created_at: datetime = datetime.now()
    model_config = ConfigDict(extra='allow')
    class Settings:
        name = "users"

class FilePart(BaseModel):
    telegram_file_id: str
    message_id: int  # <--- CRITICAL: Stores the message ID to refresh the link later
    part_number: int
    size: int

class FileSystemItem(Document):
    name: str
    is_folder: bool
    parent_id: Optional[str] = None
    owner_phone: str 
    created_at: datetime = datetime.now()
    
    share_token: Optional[str] = None
    collaborators: List[str] = [] 
    
    size: int = 0
    mime_type: Optional[str] = None
    parts: List[FilePart] = [] 
    
    model_config = ConfigDict(extra='allow')
    class Settings:
        name = "filesystem"

class SharedCollection(Document):
    token: str = Field(unique=True)
    item_ids: List[str]
    owner_phone: str
    name: Optional[str] = "Shared Bundle"
    created_at: datetime = datetime.now()
    class Settings:
        name = "shared_collections"

async def init_db():
    client = AsyncIOMotorClient(settings.MONGO_URI)
    await init_beanie(database=client.morgan_db, document_models=[User, FileSystemItem, SharedCollection])