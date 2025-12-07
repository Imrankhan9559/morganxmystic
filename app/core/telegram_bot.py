import logging
from pyrogram import Client
from app.core.config import settings

# Configure Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize Pyrogram Client
# If SESSION_STRING is present, it uses that (UserBot), otherwise Bot Token
if settings.SESSION_STRING:
    tg_client = Client(
        "morganxmystic_user",
        api_id=settings.API_ID,
        api_hash=settings.API_HASH,
        session_string=settings.SESSION_STRING
    )
else:
    tg_client = Client(
        "morganxmystic_bot",
        api_id=settings.API_ID,
        api_hash=settings.API_HASH,
        bot_token=settings.BOT_TOKEN
    )

async def start_telegram():
    logger.info("Connecting to Telegram...")
    await tg_client.start()
    me = await tg_client.get_me()
    logger.info(f"Connected as {me.first_name} (@{me.username})")

async def stop_telegram():
    logger.info("Stopping Telegram Client...")
    await tg_client.stop()