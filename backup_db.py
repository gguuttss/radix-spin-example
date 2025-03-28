#!/usr/bin/env python3
import os
import shutil
import asyncio
import aiosqlite
from datetime import datetime
import logging
from telegram.ext import Application
from telegram import Bot

# Configure logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Get environment variables
DB_FILE = os.getenv("DATABASE_FILE", "radix_spin_bot.db")
BACKUP_INTERVAL = int(os.getenv("BACKUP_INTERVAL_HOURS", "6")) * 3600  # Convert hours to seconds
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_CHAT_ID = os.getenv("GAME_OWNER_TELEGRAM_ID")
BACKUP_DIR = "database_backups"

async def create_backup():
    """Create a backup of the database and send it to admin via Telegram."""
    try:
        # Create backup directory if it doesn't exist
        if not os.path.exists(BACKUP_DIR):
            os.makedirs(BACKUP_DIR)

        # Create backup filename with timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_filename = f"backup_{timestamp}.db"
        backup_path = os.path.join(BACKUP_DIR, backup_filename)
        
        # Create a copy of the database
        async with aiosqlite.connect(DB_FILE) as db:
            # Wait for any write operations to complete
            await db.execute("PRAGMA wal_checkpoint(FULL)")
        
        # Copy the database file
        shutil.copy2(DB_FILE, backup_path)
        
        # Send backup to admin via Telegram
        if TELEGRAM_BOT_TOKEN and ADMIN_CHAT_ID:
            bot = Bot(token=TELEGRAM_BOT_TOKEN)
            with open(backup_path, 'rb') as backup_file:
                await bot.send_document(
                    chat_id=ADMIN_CHAT_ID,
                    document=backup_file,
                    caption=f"Database backup {timestamp}"
                )
        
        # Keep only last 5 backups locally
        cleanup_old_backups()
        
        logger.info(f"Successfully created backup: {backup_filename}")
        
    except Exception as e:
        logger.error(f"Error creating backup: {e}")
        if TELEGRAM_BOT_TOKEN and ADMIN_CHAT_ID:
            bot = Bot(token=TELEGRAM_BOT_TOKEN)
            await bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=f"❌ Error creating backup: {e}"
            )

def cleanup_old_backups():
    """Keep only the 5 most recent backups."""
    try:
        # List all backup files
        backups = [f for f in os.listdir(BACKUP_DIR) if f.startswith("backup_") and f.endswith(".db")]
        backups.sort(reverse=True)  # Sort by name (which includes timestamp)
        
        # Remove older backups
        for backup in backups[5:]:  # Keep only 5 most recent
            os.remove(os.path.join(BACKUP_DIR, backup))
            
    except Exception as e:
        logger.error(f"Error cleaning up old backups: {e}")

async def backup_loop():
    """Run the backup process periodically."""
    while True:
        await create_backup()
        await asyncio.sleep(BACKUP_INTERVAL)

if __name__ == "__main__":
    asyncio.run(backup_loop()) 