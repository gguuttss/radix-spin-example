#!/usr/bin/env python3
import os
import aiosqlite
import asyncio
from dotenv import load_dotenv

# Import Radix integration
try:
    from radix_integration import create_radix_account
except ImportError:
    # Mock for testing without radix_integration module
    def create_radix_account():
        return (f"sim_address_{os.urandom(4).hex()}", f"sim_privatekey_{os.urandom(8).hex()}", f"sim_publickey_{os.urandom(8).hex()}")

# Load environment variables
load_dotenv()

# Get database file path
DB_FILE = "radix_spin_bot.db"

# Get game owner Telegram ID
GAME_OWNER_TELEGRAM_ID = int(os.getenv("GAME_OWNER_TELEGRAM_ID", "0"))

async def init_db():
    """Initialize the database with required tables."""
    async with aiosqlite.connect(DB_FILE) as db:
        # Create users table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                telegram_id INTEGER PRIMARY KEY,
                radix_address TEXT UNIQUE,
                private_key TEXT,
                public_key TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Create transactions table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER,
                action TEXT,
                amount REAL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (telegram_id) REFERENCES users (telegram_id)
            )
        """)
        
        # Create game_stats table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS game_stats (
                id INTEGER PRIMARY KEY,
                game_address TEXT UNIQUE,
                game_private_key TEXT,
                game_public_key TEXT,
                game_balance REAL DEFAULT 0,
                total_spins INTEGER DEFAULT 0,
                total_winnings_paid REAL DEFAULT 0,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Create whitelist table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS whitelist (
                user_id INTEGER PRIMARY KEY,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Insert default game stats if not exists
        await db.execute("""
            INSERT OR IGNORE INTO game_stats (id, game_balance, total_spins, total_winnings_paid)
            VALUES (1, 0, 0, 0)
        """)
        
        await db.commit()
        print("Database initialized successfully!")

if __name__ == "__main__":
    asyncio.run(init_db()) 