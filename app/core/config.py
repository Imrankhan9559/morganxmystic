from typing import Optional # Add this import
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    API_ID: int
    API_HASH: str
    BOT_TOKEN: str
    SESSION_STRING: str = ""
    MONGO_URI: str
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    
    # CHANGED: Replaced ADMIN_EMAIL with ADMIN_PHONE
    ADMIN_PHONE: str 

    class Config:
        env_file = ".env"

settings = Settings()