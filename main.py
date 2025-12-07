import uvicorn
from fastapi import FastAPI, Response
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager

from app.core.config import settings
from app.core.telegram_bot import start_telegram, stop_telegram
from app.db.models import init_db
from app.routes import auth, dashboard, stream, admin, share

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Connect to DB and Start Telegram Client
    await init_db()
    await start_telegram()
    yield
    # Shutdown: Stop Telegram Client
    await stop_telegram()

app = FastAPI(title="MORGANXMYSTIC Storage", lifespan=lifespan)

# Mount Static Files (CSS/JS)
# Ensure the folder 'app/static' exists
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Fix for annoying 404 Favicon errors in browser console
@app.get('/favicon.ico', include_in_schema=False)
async def favicon():
    return Response(status_code=204)

# Include all Routes
app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(stream.router)
app.include_router(admin.router)
app.include_router(share.router)

if __name__ == "__main__":
    # Reload=True allows the server to restart when you edit code
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)