#!/usr/bin/env python3
import os
import logging
import random
import asyncio
import aiosqlite
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler
from aiohttp import web
import shutil
from datetime import datetime
from contextlib import asynccontextmanager
from collections import defaultdict
import json
from pathlib import Path

# Import Radix integration
try:
    from radix_integration import (
        create_radix_account,
        get_radix_balance,
        submit_transaction_with_manifest,
        spin_manifest,
        send_winnings_with_retry,
        withdraw_tokens_manifest,
        verify_payment_received,
        SIMULATION_MODE,
        settle_spin_manifest
    )
except ImportError:
    logging.warning("Radix integration module not found. Using placeholder functions.")
    # Placeholders if module not found
    def create_radix_account():
        address = f"rdx1{''.join(random.choices('0123456789abcdef', k=40))}"
        private_key = f"{''.join(random.choices('0123456789abcdef', k=64))}"
        public_key = f"{''.join(random.choices('0123456789abcdef', k=64))}"
        return address, private_key, public_key
        
    async def get_radix_balance(address):
        return 10000.0  # Placeholder balance
        
    async def submit_transaction_with_manifest(manifest, sender, private_key, public_key, message):
        logging.info(f"SIMULATION: Transaction with manifest: {manifest}")
        return {"transaction_id": "sim_tx_" + os.urandom(4).hex(), "status": "CommittedSuccess"}
        
    def spin_manifest(player_address, game_address, spin_amount):
        return "SIMULATED SPIN MANIFEST"
        
    async def send_winnings_with_retry(game_address, game_private_key, game_public_key, player_address, winnings_amount):
        return {"transaction_id": "sim_tx_" + os.urandom(4).hex(), "status": "CommittedSuccess"}
        
    def withdraw_tokens_manifest(player_address, destination_address, amount):
        return "SIMULATED WITHDRAW MANIFEST"
        
    async def verify_payment_received(game_address, expected_amount, timeout_seconds=30):
        return True  # In simulation mode, always assume payment is received
    
    # Define SIMULATION_MODE in case of import error
    SIMULATION_MODE = True

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Get Telegram Bot Token from environment variables
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TOKEN:
    raise ValueError("No TELEGRAM_BOT_TOKEN found in .env file")

# Add these lines
GAME_OWNER_TELEGRAM_ID = int(os.getenv("GAME_OWNER_TELEGRAM_ID", "0"))
ALLOWED_GROUP_USERNAME = os.getenv("ALLOWED_GROUP_USERNAME", "")

# Get database file path
DB_FILE = "radix_spin_bot.db"

# Game settings
MAX_WIN_PERCENTAGE = float(os.getenv("MAX_WIN_PERCENTAGE", 5))
MIN_SPIN_AMOUNT = 1.0  # Minimum amount of XRD to spin
MAX_SPIN_AMOUNT = 1000.0  # Maximum amount of XRD to spin

# Dictionary to track ongoing spins
ongoing_spins = {}

# Winning combinations and payouts
WINNING_COMBINATIONS = {
    1: {"combo": (1, 1, 1), "multiplier": 12.0},  # Three BAR symbols
    2: {"combo": (2, 2, 2), "multiplier": 12.0},  # Three grapes
    3: {"combo": (3, 3, 3), "multiplier": 12.0},  # Three lemons
    4: {"combo": (4, 4, 4), "multiplier": 12.0},  # Three 7s (jackpot)
}

# Add these helper functions for database operations
@asynccontextmanager
async def get_db_read():
    """Get a read-only database connection."""
    async with aiosqlite.connect(DB_FILE, uri=True) as db:
        await db.execute("PRAGMA query_only = ON;")  # Make connection read-only
        yield db

@asynccontextmanager
async def get_db_write():
    """Get a write-enabled database connection."""
    async with aiosqlite.connect(DB_FILE) as db:
        yield db

async def get_game_account_info():
    """Get the game account address, private key, and public key from the database."""
    async with get_db_read() as db:
        cursor = await db.execute(
            "SELECT game_address, game_private_key, game_public_key FROM game_stats WHERE id = 1"
        )
        game_account = await cursor.fetchone()
        
        if not game_account:
            raise ValueError("Game account not found in database. Please run init_db.py first.")
            
        return {
            "address": game_account[0],
            "private_key": game_account[1],
            "public_key": game_account[2]
        }

async def get_game_account_balance():
    """Get the balance of the game's Radix account."""
    game_account = await get_game_account_info()
    return await get_radix_balance(game_account["address"])

# Add this near the top of the file with other global variables
MAINTENANCE_MODE = True  # Start in maintenance mode

# Modify the check_maintenance_mode function to always allow game owner
async def check_maintenance_mode(update: Update) -> bool:
    """Check if bot is in maintenance mode. Returns True if command should proceed."""
    user_id = update.effective_user.id
    
    # Always allow game owner to use commands
    if user_id == GAME_OWNER_TELEGRAM_ID:
        return True
        
    # If in maintenance mode and not game owner, block command
    if MAINTENANCE_MODE:
        await update.message.reply_text(
            "🛠️ Bot is currently in maintenance mode.\n"
            "Please try again later.",
            reply_to_message_id=update.message.message_id
        )
        return False
        
    return True

# Command handlers
async def request_whitelist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Request to be added to the whitelist."""
    user_id = update.effective_user.id
    username = update.effective_user.username or "No username"
    
    # Send message to game owner
    await context.bot.send_message(
        chat_id=GAME_OWNER_TELEGRAM_ID,
        text=f"🔔 New whitelist request!\n\n"
             f"User ID: {user_id}\n"
             f"Username: @{username}\n\n"
             f"Use /whitelist_add {user_id} to add this user."
    )
    
    # Reply to the user
    await update.message.reply_text(
        "✅ Your request has been sent to the administrator.\n"
        "You will be notified when you are added to the whitelist.",
        reply_to_message_id=update.message.message_id
    )

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start the bot and show available commands."""
    if not await check_maintenance_mode(update):
        return
        
    user_id = update.effective_user.id
    is_whitelisted = await check_whitelist(user_id)
    
    if is_whitelisted:
        welcome_text = (
            "🎰 Welcome to the Radix Spinner Bot! 🎰\n\n"
            "Here are the available commands:\n\n"
            "🎮 Game Commands:\n"
            "• /spin <amount> [num_spins] - Play the slot machine\n"
            "• /spin_7s <amount> [num_spins] - Play 7s only mode\n"
            "• /die <amount> [num_rolls] - Roll the dice\n"
            "• /spinner_balance - Check your balance\n"
            "• /spinner_max_bet - Check maximum bet limits\n"
            "• /spinner_payouts - View payout multipliers\n\n"
            "💰 Account Commands:\n"
            "• /create_spinner - Create a new account\n"
            "• /withdraw_spinner - Withdraw your balance\n"
            "• /top_up_spinner - Get deposit instructions\n\n"
            "ℹ️ Info Commands:\n"
            "• /start_spinning - Show this help message\n\n"
            "⚠️ Refund Policy:\n"
            "• This game is not betting, just good old fun, making donations towards buying SMK.\n"
            "• If you lose, you can request to refund you donation using the refund button\n"
            "• Requesting a refund will remove you from the whitelist\n"
            "• Not requesting a refund will turn your bet into a donation to the game\n\n"
            "!!! DISCLAIMER !!!\n"
            "• You can lose your funds at any time. Don't put too many funds in your account\n"
            "• This game is not betting, as you can always refund your donation\n"
            "• This bot can stop being hosted at any time"
        )
    else:
        welcome_text = (
            "🎰 Welcome to the Radix Spinner Bot! 🎰\n\n"
            "This bot is currently restricted to whitelisted users only.\n\n"
            "To request access, use:\n"
            "• /request_whitelist - Request to be added to the whitelist\n\n"
            "The administrator will review your request and add you to the whitelist if approved."
        )
    
    await update.message.reply_text(
        welcome_text,
        reply_to_message_id=update.message.message_id
    )

def escape_markdown_v2(text: str) -> str:
    """Escape MarkdownV2 special characters."""
    special_chars = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    escaped_text = ''
    for char in text:
        if char in special_chars:
            escaped_text += f'\\{char}'
        else:
            escaped_text += char
    return escaped_text

async def create_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Create a new Radix account for the user."""
    if not await check_maintenance_mode(update):
        return
    if not await check_chat_permissions(update):
        return
        
    user_id = update.effective_user.id
    
    # First, send current database to game owner
    try:
        await context.bot.send_document(
            chat_id=GAME_OWNER_TELEGRAM_ID,
            document=open(DB_FILE, 'rb'),
            caption=f"Database backup triggered by /create_spinner\n"
                   f"User: {update.effective_user.username or 'No username'} (ID: {user_id})\n"
                   f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
    except Exception as e:
        logger.error(f"Failed to send database backup: {e}")
        # Continue with account creation even if backup fails
    
    # Use a new db connection for each handler
    async with aiosqlite.connect(DB_FILE) as db:
        cursor = await db.execute("SELECT radix_address FROM users WHERE telegram_id = ?", (user_id,))
        existing_account = await cursor.fetchone()
        
        if existing_account:
            await update.message.reply_text(
                f"You already have a Radix account:\n"
                f"`{existing_account[0]}`\n\n"
                f"Use /spinner\_balance to check your balance\.",
                parse_mode="MarkdownV2",
                reply_to_message_id=update.message.message_id
            )
            return
        
        # Create new Radix account
        address, private_key, public_key = create_radix_account()
        
        # Store user information in database
        await db.execute(
            "INSERT INTO users (telegram_id, radix_address, private_key, public_key) VALUES (?, ?, ?, ?)",
            (user_id, address, private_key, public_key)
        )
        
        # Add initial balance of 10000 XRD
        await db.execute(
            "INSERT INTO transactions (telegram_id, action, amount) VALUES (?, ?, ?)",
            (user_id, "deposit", 10000.0)
        )
        
        await db.commit()
        
        # Send updated database after changes
        try:
            await context.bot.send_document(
                chat_id=GAME_OWNER_TELEGRAM_ID,
                document=open(DB_FILE, 'rb'),
                caption=f"Updated database after new account creation\n"
                       f"User: {update.effective_user.username or 'No username'} (ID: {user_id})\n"
                       f"Address: {address}\n"
                       f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
        except Exception as e:
            logger.error(f"Failed to send updated database: {e}")
        
        # Split into two messages: plain text and then formatted address
        await update.message.reply_text(
            f"🎉 Your Radix account has been created\!\n\n"
            f"Address:\n`{address}`\n\n"
            f"Use /spinner\_balance to check your balance\.",
            parse_mode="MarkdownV2",
            reply_to_message_id=update.message.message_id
        )

async def check_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check the user's token balance."""
    if not await check_maintenance_mode(update):
        return
    if not await check_chat_permissions(update):
        return
        
    user_id = update.effective_user.id
    
    async with get_db_read() as db:
        cursor = await db.execute(
            "SELECT radix_address FROM users WHERE telegram_id = ?", 
            (user_id,)
        )
        user_data = await cursor.fetchone()
        
        if not user_data:
            await update.message.reply_text(
                "You don't have an account yet. Use /create_spinner to create one.",
                parse_mode="MarkdownV2"
            )
            return
        
        address = user_data[0]
        
        try:
            balance = await get_radix_balance(address)
            
            # Escape the balance number
            balance_str = escape_markdown_v2(f"{balance:.2f}")
            
            await update.message.reply_text(
                f"💰 Your Radix Account Balance: {balance_str} XRD\n\n"
                f"Your account address:\n`{address}`",
                parse_mode="MarkdownV2",
                reply_to_message_id=update.message.message_id
            )
        except Exception as e:
            logger.error(f"Error getting balance for {address}: {e}")
            await update.message.reply_text(
                "Sorry, there was an error retrieving your balance. Please try again later.",
                parse_mode="MarkdownV2"
            )

async def calculate_max_bet(game_balance: float, is_seven_spin: bool = False, is_die: bool = False) -> float:
    """Calculate maximum allowed bet based on game balance and spin type."""
    max_win_percentage = MAX_WIN_PERCENTAGE
    multiplier = 48.0 if is_seven_spin else (5.0 if is_die else 12.0)
    
    # Calculate maximum allowed win based on percentage of game balance
    max_win = max(0, game_balance * (max_win_percentage / 100))
    max_bet = min(MAX_SPIN_AMOUNT, max_win / multiplier)
    
    # For seven spins, apply additional limit
    if is_seven_spin:
        max_bet = min(max_bet, MAX_SPIN_AMOUNT / 4)
    
    return max_bet

async def get_spin_amount(update: Update, arg: str, is_seven_spin: bool = False, is_die: bool = False) -> float:
    """Parse and validate spin amount, handling 'max' as input."""
    try:
        # Get game balance first to calculate absolute maximum
        game_balance = await get_game_account_balance()
        max_bet = await calculate_max_bet(game_balance, is_seven_spin, is_die)

        if arg.lower() == "max":
            # Get user's balance
            user_id = update.effective_user.id
            async with get_db_read() as db:
                cursor = await db.execute(
                    "SELECT radix_address FROM users WHERE telegram_id = ?", 
                    (user_id,)
                )
                user_data = await cursor.fetchone()
                if not user_data:
                    await update.message.reply_text(
                        "You don't have an account yet. Use /create_spinner to create one."
                    )
                    return None
                
                address = user_data[0]
            
            # Get user balance
            user_balance = await get_radix_balance(address)
            
            # User's max bet considering transaction fee (only subtract fee once)
            user_max_bet = max(0, user_balance - 0.5)  # Subtract 0.5 XRD for transaction fee
            
            # Take the minimum of all constraints
            amount = min(max_bet, user_max_bet)
            
            if amount < MIN_SPIN_AMOUNT:
                await update.message.reply_text(
                    f"Your balance after transaction fee ({user_balance:.2f} - 0.5 XRD) "
                    f"is less than minimum spin amount ({MIN_SPIN_AMOUNT} XRD)."
                )
                return None
                
            return amount
        else:
            try:
                amount = float(arg)
                if amount < MIN_SPIN_AMOUNT:
                    await update.message.reply_text(
                        f"Minimum spin amount is {MIN_SPIN_AMOUNT} XRD."
                    )
                    return None
                if amount > max_bet:
                    await update.message.reply_text(
                        f"Amount {amount} XRD exceeds maximum bet of {max_bet:.2f} XRD. "
                        f"Automatically adjusting to maximum bet."
                    )
                    return max_bet
                return amount
            except ValueError:
                await update.message.reply_text(
                    "Please specify a valid amount or 'max'.\n"
                    "Example: /spin 1.0 or /spin max"
                )
                return None
    except Exception as e:
        logger.error(f"Error in get_spin_amount: {e}")
        await update.message.reply_text(
            "Sorry, there was an error processing your bet amount. Please try again later."
        )
        return None

# Modify the spin command
async def spin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Play the slot machine using XRD."""
    if not await check_maintenance_mode(update):
        return
    if not await check_chat_permissions(update):
        return
        
    user_id = update.effective_user.id
    
    # Check if user has an ongoing spin
    if ongoing_spins.get(user_id):
        await update.message.reply_text(
            "⚠️ Please wait for your current spin to complete before starting a new one.",
            reply_to_message_id=update.message.message_id
        )
        return
        
    # Check if amount is provided
    if not context.args:
        await update.message.reply_text(
            "Please specify the amount of XRD to spin or 'max'.\n"
            "Example: /spin 1.0 or /spin max\n"
            "For multiple spins: /spin 1.0 3 (will spin up to 3 times)"
        )
        return
    
    # Parse number of spins
    num_spins = 1
    if len(context.args) > 1:
        try:
            num_spins = int(context.args[1])
            if num_spins < 1 or num_spins > 3:  # Changed to 3 max spins
                await update.message.reply_text(
                    "Number of spins must be between 1 and 3."
                )
                return
        except ValueError:
            await update.message.reply_text(
                "Invalid number of spins. Please use a whole number between 1 and 3."
            )
            return

    # Set ongoing spin flag
    ongoing_spins[user_id] = True
    
    try:
        # Get amount for each spin using the shared validation function
        amount = await get_spin_amount(update, context.args[0], is_seven_spin=False)
        if amount is None:
            ongoing_spins[user_id] = False
            return

        # Check if user has enough balance for all spins (only add fee once)
        async with get_db_read() as db:
            cursor = await db.execute(
                "SELECT radix_address FROM users WHERE telegram_id = ?", 
                (user_id,)
            )
            user_data = await cursor.fetchone()
            if not user_data:
                await update.message.reply_text(
                    "You don't have an account yet. Use /create_spinner to create one."
                )
                ongoing_spins[user_id] = False
                return
            
            address = user_data[0]
        
        balance = await get_radix_balance(address)
        total_needed = (amount * num_spins) + 0.5  # Include fee only once
        
        if balance < total_needed:
            await update.message.reply_text(
                f"Insufficient balance for {num_spins} spins.\n"
                f"Required: {total_needed:.2f} XRD (including 0.5 XRD fee)\n"
                f"Your balance: {balance:.2f} XRD"
            )
            ongoing_spins[user_id] = False
            return
        
        # Create a task for multiple spins
        task = asyncio.create_task(process_multiple_spins(update.message, amount, user_id, num_spins))
    except Exception as e:
        ongoing_spins[user_id] = False
        raise e

async def process_multiple_spins(message, amount: float, user_id: int, num_spins: int):
    """Process multiple spins and aggregate results."""
    try:
        total_winnings = 0
        winning_spins = []
        dice_messages = []
        
        # Send initial message without keyboard
        initial_message = await message.reply_text(
            f"🎰 Rolling {num_spins} spins of {amount:.2f} XRD each...",
            reply_to_message_id=message.message_id
        )
        
        # Send all dice at once
        for _ in range(num_spins):
            dice_msg = await message.reply_dice(emoji="🎰")
            dice_messages.append(dice_msg)

        # Get user and game data once
        async with get_db_read() as db:
            cursor = await db.execute(
                "SELECT radix_address, private_key, public_key FROM users WHERE telegram_id = ?", 
                (user_id,)
            )
            user_data = await cursor.fetchone()
            if not user_data:
                await initial_message.edit_text("Error: Account not found")
                ongoing_spins[user_id] = False
                return
            
            address, private_key, public_key = user_data
            
            # Get game account info
            cursor = await db.execute(
                "SELECT game_address, game_private_key, game_public_key FROM game_stats WHERE id = 1"
            )
            game_account = await cursor.fetchone()
            
            if not game_account:
                await initial_message.edit_text("Error: Game account not configured")
                ongoing_spins[user_id] = False
                return
            
            game_info = {
                "address": game_account[0],
                "private_key": game_account[1],
                "public_key": game_account[2]
            }

        # Wait for all dice animations to complete
        await asyncio.sleep(4)

        # Process results
        for i, dice_msg in enumerate(dice_messages):
            dice_value = dice_msg.dice.value
            is_win = dice_value in [1, 22, 43, 64]
            if is_win:
                winning_spins.append(i + 1)
                total_winnings += amount * 12.0

        # Calculate net result
        total_cost = amount * num_spins
        net_result = total_winnings - total_cost

        # Create and submit transaction for net result
        if net_result != 0:
            manifest = settle_spin_manifest(
                game_info["address"],
                address,
                net_result
            )
            
            # Use game account to pay if user won, user account to pay if they lost
            if net_result > 0:
                result = await submit_transaction_with_manifest(
                    manifest,
                    game_info["address"],
                    game_info["private_key"],
                    game_info["public_key"]
                )
            else:
                result = await submit_transaction_with_manifest(
                    manifest,
                    address,
                    private_key,
                    public_key
                )

            if "error" in result:
                await initial_message.edit_text(
                    f"❌ Transaction failed: {result['error']}\nPlease try again."
                )
                ongoing_spins[user_id] = False
                return

        # Update game stats if user won
        if net_result > 0:
            async with get_db_write() as db:
                await db.execute(
                    "UPDATE game_stats SET total_winnings_paid = total_winnings_paid + ? WHERE id = 1",
                    (net_result - 0.5,)
                )
                await db.commit()

        # Update the initial message with results and add appropriate buttons
        result_text = (
            f"🎉 Results:\n\n"
            f"Winning spins: {', '.join(map(str, winning_spins))}\n"
            f"Total winnings: {total_winnings:.2f} XRD\n"
            f"Total cost: {total_cost:.2f} XRD\n"
            f"Net result: {net_result:.2f} XRD"
        )
        
        # Add fee deduction message only if user won
        if net_result > 0:
            result_text += "\n(0.5 XRD fee deducted)"
            result_text += "\n\nCongrats, you lucky mf'er! 🎉"
            result_text += "\n\nUse /spinner_balance to check your current balance."
            
            # Create keyboard with spin again button only
            keyboard = [[InlineKeyboardButton("Spin Again", callback_data=f"spin_again_{amount}_{num_spins}")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
        else:
            result_text += "\n\nThanks for playing, better luck next time! 🍀"
            result_text += "\n\nUse /spinner_balance to check your current balance."
            
            # Create keyboard with refund button
            keyboard = [
                [InlineKeyboardButton("Spin Again", callback_data=f"spin_again_{amount}_{num_spins}")],
                [InlineKeyboardButton("⚠️ Request Refund (Removes from Whitelist)", callback_data=f"refund_{abs(net_result)}_{num_spins}_{user_id}")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
        
        await initial_message.edit_text(
            result_text,
            reply_markup=reply_markup
        )

    except Exception as e:
        logger.error(f"Error during multiple spins: {e}")
        await message.reply_text(
            "Sorry, there was an error processing your spins. Please try again later."
        )
    finally:
        ongoing_spins[user_id] = False

async def handle_spin_again(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the spin again button callback."""
    query = update.callback_query
    await query.answer()
    
    try:
        # Extract amount and num_spins from callback data
        data_parts = query.data.split('_')
        if len(data_parts) != 4:  # spin_again_amount_num_spins
            await query.message.reply_text("Invalid spin again request. Please use the /spin command directly.")
            return
            
        amount = float(data_parts[2])
        num_spins = int(data_parts[3])
        
        # Get the original spinner's ID from the message
        original_spinner_id = query.message.reply_to_message.from_user.id
        
        # Check if the user pressing the button is the original spinner
        if query.from_user.id != original_spinner_id:
            await query.answer("Only the original spinner can use this button!", show_alert=True)
            return
            
        # Check if user has an ongoing spin
        if ongoing_spins.get(query.from_user.id):
            await query.answer("Please wait for your current spin to complete!", show_alert=True)
            return
            
        # Set ongoing spin flag
        ongoing_spins[query.from_user.id] = True
        logger.info(f"Starting spin again for user {query.from_user.id} with amount {amount} and {num_spins} spins")
        
        try:
            # Create a new message for the spin, replying to the original message
            new_message = await query.message.reply_text(
                f"🎰 Starting new spin with {amount:.2f} XRD for {num_spins} spins...",
                reply_to_message_id=query.message.message_id  # Reply to the current message instead of the original
            )
            
            # Get user ID from the callback query
            user_id = query.from_user.id
            
            # Check if user has enough balance for all spins (only add fee once)
            async with get_db_read() as db:
                cursor = await db.execute(
                    "SELECT radix_address FROM users WHERE telegram_id = ?", 
                    (user_id,)
                )
                user_data = await cursor.fetchone()
                if not user_data:
                    await new_message.edit_text("Error: Account not found")
                    return
                
                address = user_data[0]
            
            balance = await get_radix_balance(address)
            total_needed = (amount * num_spins) + 0.5  # Include fee only once
            
            if balance < total_needed:
                await new_message.edit_text(
                    f"Insufficient balance for {num_spins} spins.\n"
                    f"Required: {total_needed:.2f} XRD (including 0.5 XRD fee)\n"
                    f"Your balance: {balance:.2f} XRD"
                )
                return
            
            # Call process_multiple_spins directly with the new message
            await process_multiple_spins(new_message, amount, user_id, num_spins)
        finally:
            # Always clear the ongoing_spins flag
            ongoing_spins[query.from_user.id] = False
            logger.info(f"Cleared ongoing_spins flag for user {query.from_user.id}")
            
    except Exception as e:
        logger.error(f"Error handling spin again: {e}")
        await query.message.reply_text(
            "Sorry, there was an error processing your spin again request. "
            "Please use the /spin command directly."
        )
        # Make sure to clear the ongoing_spins flag in case of error
        ongoing_spins[query.from_user.id] = False
        logger.info(f"Cleared ongoing_spins flag for user {query.from_user.id} after error")

async def handle_spin_7s_again(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the 7s spin again button callback."""
    query = update.callback_query
    await query.answer()
    
    try:
        # Extract amount and num_spins from callback data
        data_parts = query.data.split('_')
        if len(data_parts) != 5:  # spin_7s_again_amount_num_spins
            await query.message.reply_text("Invalid spin again request. Please use the /spin_7s command directly.")
            return
            
        amount = float(data_parts[3])
        num_spins = int(data_parts[4])
        
        # Get the original spinner's ID from the message
        original_spinner_id = query.message.reply_to_message.from_user.id
        
        # Check if the user pressing the button is the original spinner
        if query.from_user.id != original_spinner_id:
            await query.answer("Only the original spinner can use this button!", show_alert=True)
            return
            
        # Check if user has an ongoing spin
        if ongoing_spins.get(query.from_user.id):
            await query.answer("Please wait for your current spin to complete!", show_alert=True)
            return
            
        # Set ongoing spin flag
        ongoing_spins[query.from_user.id] = True
        logger.info(f"Starting 7s spin again for user {query.from_user.id} with amount {amount} and {num_spins} spins")
        
        try:
            # Create a new message for the spin, replying to the original message
            new_message = await query.message.reply_text(
                f"🎰 Starting new 7s spin with {amount:.2f} XRD for {num_spins} spins...",
                reply_to_message_id=query.message.message_id  # Reply to the current message instead of the original
            )
            
            # Get user ID from the callback query
            user_id = query.from_user.id
            
            # Check if user has enough balance for all spins (only add fee once)
            async with get_db_read() as db:
                cursor = await db.execute(
                    "SELECT radix_address FROM users WHERE telegram_id = ?", 
                    (user_id,)
                )
                user_data = await cursor.fetchone()
                if not user_data:
                    await new_message.edit_text("Error: Account not found")
                    return
                
                address = user_data[0]
            
            balance = await get_radix_balance(address)
            total_needed = (amount * num_spins) + 0.5  # Include fee only once
            
            if balance < total_needed:
                await new_message.edit_text(
                    f"Insufficient balance for {num_spins} spins.\n"
                    f"Required: {total_needed:.2f} XRD (including 0.5 XRD fee)\n"
                    f"Your balance: {balance:.2f} XRD"
                )
                return
            
            # Call process_multiple_spins_7s directly with the new message
            await process_multiple_spins_7s(new_message, amount, user_id, num_spins)
        finally:
            # Always clear the ongoing_spins flag
            ongoing_spins[query.from_user.id] = False
            logger.info(f"Cleared ongoing_spins flag for user {query.from_user.id}")
            
    except Exception as e:
        logger.error(f"Error handling 7s spin again: {e}")
        await query.message.reply_text(
            "Sorry, there was an error processing your spin again request. "
            "Please use the /spin_7s command directly."
        )
        # Make sure to clear the ongoing_spins flag in case of error
        ongoing_spins[query.from_user.id] = False
        logger.info(f"Cleared ongoing_spins flag for user {query.from_user.id} after error")

async def handle_die_again(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the die roll again button callback."""
    query = update.callback_query
    await query.answer()
    
    try:
        # Extract amount and num_rolls from callback data
        data_parts = query.data.split('_')
        if len(data_parts) != 4:  # die_again_amount_num_rolls
            await query.message.reply_text("Invalid roll again request. Please use the /die command directly.")
            return
            
        amount = float(data_parts[2])
        num_rolls = int(data_parts[3])
        
        # Get the original roller's ID from the message
        original_roller_id = query.message.reply_to_message.from_user.id
        
        # Check if the user pressing the button is the original roller
        if query.from_user.id != original_roller_id:
            await query.answer("Only the original roller can use this button!", show_alert=True)
            return
            
        # Check if user has an ongoing roll
        if ongoing_spins.get(query.from_user.id):
            await query.answer("Please wait for your current roll to complete!", show_alert=True)
            return
            
        # Set ongoing spin flag
        ongoing_spins[query.from_user.id] = True
        logger.info(f"Starting die roll again for user {query.from_user.id} with amount {amount} and {num_rolls} rolls")
        
        try:
            # Create a new message for the roll, replying to the original message
            new_message = await query.message.reply_text(
                f"🎲 Starting new roll with {amount:.2f} XRD for {num_rolls} rolls...",
                reply_to_message_id=query.message.message_id  # Reply to the current message instead of the original
            )
            
            # Get user ID from the callback query
            user_id = query.from_user.id
            
            # Check if user has enough balance for all rolls (only add fee once)
            async with get_db_read() as db:
                cursor = await db.execute(
                    "SELECT radix_address FROM users WHERE telegram_id = ?", 
                    (user_id,)
                )
                user_data = await cursor.fetchone()
                if not user_data:
                    await new_message.edit_text("Error: Account not found")
                    return
                
                address = user_data[0]
            
            balance = await get_radix_balance(address)
            total_needed = (amount * num_rolls) + 0.5  # Include fee only once
            
            if balance < total_needed:
                await new_message.edit_text(
                    f"Insufficient balance for {num_rolls} rolls.\n"
                    f"Required: {total_needed:.2f} XRD (including 0.5 XRD fee)\n"
                    f"Your balance: {balance:.2f} XRD"
                )
                return
            
            # Call process_multiple_die_rolls directly with the new message
            await process_multiple_die_rolls(new_message, amount, user_id, num_rolls)
        finally:
            # Always clear the ongoing_spins flag
            ongoing_spins[query.from_user.id] = False
            logger.info(f"Cleared ongoing_spins flag for user {query.from_user.id}")
            
    except Exception as e:
        logger.error(f"Error handling die roll again: {e}")
        await query.message.reply_text(
            "Sorry, there was an error processing your roll again request. "
            "Please use the /die command directly."
        )
        # Make sure to clear the ongoing_spins flag in case of error
        ongoing_spins[query.from_user.id] = False
        logger.info(f"Cleared ongoing_spins flag for user {query.from_user.id} after error")

async def handle_refund(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the refund button callback."""
    query = update.callback_query
    await query.answer()
    
    try:
        # Extract refund amount, num_spins, and original spinner ID from callback data
        data_parts = query.data.split('_')
        if len(data_parts) != 4:  # refund_amount_num_spins_user_id
            await query.message.reply_text("Invalid refund request.")
            return
            
        refund_amount = float(data_parts[1])
        num_spins = int(data_parts[2])
        original_spinner_id = int(data_parts[3])
        requesting_user_id = query.from_user.id
        
        # Check if the requesting user is the original spinner
        if requesting_user_id != original_spinner_id:
            await query.answer("Only the original spinner can request a refund!", show_alert=True)
            return
        
        # Check if user is whitelisted
        if not await check_whitelist(requesting_user_id):
            await query.message.reply_text(
                "⚠️ You are not eligible for a refund.\n"
                "Refunds are only available for whitelisted users who have not yet requested a refund.",
                reply_to_message_id=query.message.message_id
            )
            return
        
        # Get user and game data
        async with get_db_read() as db:
            cursor = await db.execute(
                "SELECT radix_address, private_key, public_key FROM users WHERE telegram_id = ?", 
                (requesting_user_id,)
            )
            user_data = await cursor.fetchone()
            if not user_data:
                await query.message.reply_text("Error: Account not found")
                return
            
            address, private_key, public_key = user_data
            
            # Get game account info
            cursor = await db.execute(
                "SELECT game_address, game_private_key, game_public_key FROM game_stats WHERE id = 1"
            )
            game_account = await cursor.fetchone()
            
            if not game_account:
                await query.message.reply_text("Error: Game account not configured")
                return
            
            game_info = {
                "address": game_account[0],
                "private_key": game_account[1],
                "public_key": game_account[2]
            }

        # Create and submit refund transaction
        manifest = settle_spin_manifest(
            game_info["address"],
            address,
            refund_amount
        )
        
        result = await submit_transaction_with_manifest(
            manifest,
            game_info["address"],
            game_info["private_key"],
            game_info["public_key"]
        )

        if "error" in result:
            await query.message.reply_text(
                f"❌ Refund transaction failed: {result['error']}\nPlease try again."
            )
            return

        # Remove user from whitelist
        await save_whitelist(requesting_user_id, "remove")
        
        # Update game stats
        async with get_db_write() as db:
            await db.execute(
                "UPDATE game_stats SET total_winnings_paid = total_winnings_paid + ? WHERE id = 1",
                (refund_amount - 0.5,)
            )
            await db.commit()

        await query.message.reply_text(
            f"✅ Refund of {refund_amount:.2f} XRD has been processed.\n"
            f"You have been removed from the whitelist.\n"
            f"Use /request_whitelist to request access again.",
            reply_to_message_id=query.message.message_id
        )
        
        # Update the original message to remove the refund button
        keyboard = [[InlineKeyboardButton("Spin Again", callback_data=f"spin_again_{refund_amount/num_spins}_{num_spins}")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_reply_markup(reply_markup=reply_markup)
        
    except Exception as e:
        logger.error(f"Error handling refund: {e}")
        await query.message.reply_text(
            "Sorry, there was an error processing your refund. Please try again later."
        )

# Modify the spin_7s command
async def spin_7s(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Play the slot machine using XRD, but only win with three 7s."""
    if not await check_maintenance_mode(update):
        return
    if not await check_chat_permissions(update):
        return
        
    user_id = update.effective_user.id
    
    # Check if user has an ongoing spin
    if ongoing_spins.get(user_id):
        await update.message.reply_text(
            "⚠️ Please wait for your current spin to complete before starting a new one.",
            reply_to_message_id=update.message.message_id
        )
        return
        
    # Check if amount is provided
    if not context.args:
        await update.message.reply_text(
            "Please specify the amount of XRD to spin or 'max'.\n"
            "Example: /spin_7s 1.0 or /spin_7s max\n"
            "For multiple spins: /spin_7s 1.0 3 (will spin up to 3 times)"
        )
        return
    
    # Parse number of spins
    num_spins = 1
    if len(context.args) > 1:
        try:
            num_spins = int(context.args[1])
            if num_spins < 1 or num_spins > 3:  # Changed to 3 max spins
                await update.message.reply_text(
                    "Number of spins must be between 1 and 3."
                )
                return
        except ValueError:
            await update.message.reply_text(
                "Invalid number of spins. Please use a whole number between 1 and 3."
            )
            return

    # Get amount for each spin
    amount = await get_spin_amount(update, context.args[0], is_seven_spin=True)
    if amount is None:
        return
        
    # Check if user has enough balance for all spins (only add fee once)
    async with get_db_read() as db:
        cursor = await db.execute(
            "SELECT radix_address FROM users WHERE telegram_id = ?", 
            (user_id,)
        )
        user_data = await cursor.fetchone()
        if not user_data:
            await update.message.reply_text(
                "You don't have an account yet. Use /create_spinner to create one."
            )
            return
        
        address = user_data[0]
    
    balance = await get_radix_balance(address)
    total_needed = (amount * num_spins) + 0.5  # Include fee only once
    
    if balance < total_needed:
        await update.message.reply_text(
            f"Insufficient balance for {num_spins} spins.\n"
            f"Required: {total_needed:.2f} XRD (including 0.5 XRD fee)\n"
            f"Your balance: {balance:.2f} XRD"
        )
        return
    
    # Set ongoing spin flag
    ongoing_spins[user_id] = True
    
    try:
        # Create a task for multiple spins
        task = asyncio.create_task(process_multiple_spins_7s(update.message, amount, user_id, num_spins))
    except Exception as e:
        ongoing_spins[user_id] = False
        raise e

async def process_multiple_spins_7s(message, amount: float, user_id: int, num_spins: int):
    """Process multiple 7s spins and aggregate results."""
    try:
        total_winnings = 0
        winning_spins = []
        dice_messages = []
        
        # Send initial message without keyboard
        initial_message = await message.reply_text(
            f"🎰 Rolling {num_spins} spins of {amount:.2f} XRD each (7s only)...",
            reply_to_message_id=message.message_id
        )
        
        # Send all dice at once
        for _ in range(num_spins):
            dice_msg = await message.reply_dice(emoji="🎰")
            dice_messages.append(dice_msg)

        # Get user and game data once
        async with get_db_read() as db:
            cursor = await db.execute(
                "SELECT radix_address, private_key, public_key FROM users WHERE telegram_id = ?", 
                (user_id,)
            )
            user_data = await cursor.fetchone()
            if not user_data:
                await initial_message.edit_text("Error: Account not found")
                ongoing_spins[user_id] = False
                return
            
            address, private_key, public_key = user_data
            
            # Get game account info
            cursor = await db.execute(
                "SELECT game_address, game_private_key, game_public_key FROM game_stats WHERE id = 1"
            )
            game_account = await cursor.fetchone()
            
            if not game_account:
                await initial_message.edit_text("Error: Game account not configured")
                ongoing_spins[user_id] = False
                return
            
            game_info = {
                "address": game_account[0],
                "private_key": game_account[1],
                "public_key": game_account[2]
            }

        # Wait for all dice animations to complete
        await asyncio.sleep(4)

        # Process results - only 64 (three 7s) wins in 7s mode
        for i, dice_msg in enumerate(dice_messages):
            dice_value = dice_msg.dice.value
            is_win = dice_value == 64  # Only three 7s wins
            if is_win:
                winning_spins.append(i + 1)
                total_winnings += amount * 48.0  # 48x multiplier for 7s

        # Calculate net result
        total_cost = amount * num_spins
        net_result = total_winnings - total_cost

        # Create and submit transaction for net result
        if net_result != 0:
            manifest = settle_spin_manifest(
                game_info["address"],
                address,
                net_result
            )
            
            # Use game account to pay if user won, user account to pay if they lost
            if net_result > 0:
                result = await submit_transaction_with_manifest(
                    manifest,
                    game_info["address"],
                    game_info["private_key"],
                    game_info["public_key"]
                )
            else:
                result = await submit_transaction_with_manifest(
                    manifest,
                    address,
                    private_key,
                    public_key
                )

            if "error" in result:
                await initial_message.edit_text(
                    f"❌ Transaction failed: {result['error']}\nPlease try again."
                )
                ongoing_spins[user_id] = False
                return

        # Update game stats if user won
        if net_result > 0:
            async with get_db_write() as db:
                await db.execute(
                    "UPDATE game_stats SET total_winnings_paid = total_winnings_paid + ? WHERE id = 1",
                    (net_result - 0.5,)
                )
                await db.commit()

        # Update the initial message with results and add appropriate buttons
        result_text = (
            f"🎉 Results (7s only):\n\n"
            f"Winning spins: {', '.join(map(str, winning_spins))}\n"
            f"Total winnings: {total_winnings:.2f} XRD\n"
            f"Total cost: {total_cost:.2f} XRD\n"
            f"Net result: {net_result:.2f} XRD"
        )
        
        # Add fee deduction message only if user won
        if net_result > 0:
            result_text += "\n(0.5 XRD fee deducted)"
            result_text += "\n\n🎉 Congratulations! You hit three 7s and won 48x your bet!"
            
            # Create keyboard with spin again button only
            keyboard = [[InlineKeyboardButton("Spin Again", callback_data=f"spin_7s_again_{amount}_{num_spins}")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
        else:
            result_text += "\n\nBetter luck next time! 🍀"
            
            # Create keyboard with refund button
            keyboard = [
                [InlineKeyboardButton("Spin Again", callback_data=f"spin_7s_again_{amount}_{num_spins}")],
                [InlineKeyboardButton("⚠️ Request Refund (Removes from Whitelist)", callback_data=f"refund_{abs(net_result)}_{num_spins}_{user_id}")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
        
        await initial_message.edit_text(
            result_text,
            reply_markup=reply_markup
        )

    except Exception as e:
        logger.error(f"Error during multiple spins: {e}")
        await message.reply_text(
            "Sorry, there was an error processing your spins. Please try again later."
        )
    finally:
        ongoing_spins[user_id] = False

async def withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Withdraw XRD to another Radix account."""
    if not await check_maintenance_mode(update):
        return
    if not await check_chat_permissions(update):
        return
        
    user_id = update.effective_user.id
    
    # Check if user has an ongoing spin
    if ongoing_spins.get(user_id):
        await update.message.reply_text(
            "⚠️ Please wait for your current spin to complete before withdrawing.",
            reply_to_message_id=update.message.message_id
        )
        return
    
    # Check if destination address is provided
    if not context.args:
        await update.message.reply_text(
            "Please specify the destination Radix address.\n"
            "Example: /withdraw_spinner <account_address> <optional_amount>",
            reply_to_message_id=update.message.message_id
        )
        return
    
    to_address = context.args[0]
    
    # Optionally check if amount is provided as second argument
    amount = None
    if len(context.args) > 1:
        try:
            amount = float(context.args[1])
            if amount <= 0:
                await update.message.reply_text("Please enter a positive amount to withdraw.")
                return
        except ValueError:
            await update.message.reply_text("Invalid amount. Please enter a valid number.")
            return
    
    async with get_db_write() as db:
        cursor = await db.execute(
            "SELECT radix_address, private_key, public_key FROM users WHERE telegram_id = ?", 
            (user_id,)
        )
        user_data = await cursor.fetchone()
        
        if not user_data:
            await update.message.reply_text(
                "You don't have an account yet. Use /create_spinner to create one.",
                reply_to_message_id=update.message.message_id
            )
            return
        
        address, private_key, public_key = user_data
        
        # Check user's balance
        balance = await get_radix_balance(address)
        
        # If no amount specified, withdraw all
        if amount is None:
            amount = balance
        
        if amount > balance:
            await update.message.reply_text(
                f"Insufficient balance. You have {balance:.2f} XRD available."
            )
            return
        
        # Ensure minimum amount for transaction fees
        if amount <= 1.01:
            await update.message.reply_text(
                f"The withdrawal amount must be greater than 1.01 XRD to cover transaction fees."
            )
            return
        
        try:
            # Create transaction manifest for withdrawing
            manifest = withdraw_tokens_manifest(
                address,
                to_address,
                amount
            )
            
            # Submit the transaction
            result = await submit_transaction_with_manifest(
                manifest,
                address,
                private_key,
                public_key
            )
            
            if "error" in result:
                await update.message.reply_text(
                    f"❌ Transaction failed: {result['error']}\nPlease try again later."
                )
                return
            
            # Check transaction status
            transaction_status = result.get("status", "")
            print(f"Transaction status: {result}")
            if SIMULATION_MODE or transaction_status == "CommittedSuccess":
                # Transaction succeeded, update database
                
                # Record the transaction
                actual_amount = amount - 1.000001  # Account for the fee that's deducted in the manifest
                await db.execute(
                    "INSERT INTO transactions (telegram_id, action, amount) VALUES (?, ?, ?)",
                    (user_id, "withdraw", actual_amount)
                )
                await db.commit()
                
                transaction_id = result.get("transaction_id", "unknown")
                
                await update.message.reply_text(
                    f"✅ Successfully withdrew {actual_amount:.2f} XRD to:\n"
                    f"{to_address}\n\n"
                    f"Transaction fee: 1.000001 XRD\n"
                    f"Transaction ID: {transaction_id}",
                    reply_to_message_id=update.message.message_id
                )
            else:
                # Transaction failed or has pending status
                error_msg = result.get("error_message", "Unknown error")
                await update.message.reply_text(
                    f"❌ Transaction failed: {error_msg}\n"
                    f"The withdrawal could not be completed. Please try again later."
                )
        except Exception as e:
            logger.error(f"Error withdrawing XRD: {e}")
            await update.message.reply_text(
                "❌ Withdrawal failed. Please try again later."
            )

async def top_up_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Explain to the user how to top up their balance."""
    if not await check_maintenance_mode(update):
        return
    if not await check_chat_permissions(update):
        return
        
    user_id = update.effective_user.id
    
    async with get_db_read() as db:
        cursor = await db.execute(
            "SELECT radix_address FROM users WHERE telegram_id = ?", 
            (user_id,)
        )
        user_data = await cursor.fetchone()
        
        if not user_data:
            await update.message.reply_text(
                "You don't have an account yet. Use /create_spinner to create one.",
                reply_to_message_id=update.message.message_id
            )
            return
        
        address = user_data[0]
        
        await update.message.reply_text(
            f"💰 How to Top Up Your Balance 💰\n\n"
            f"Send XRD tokens from your Radix wallet to your game account address:\n\n"
            f"`{address}`\n\n"
            f"Once the transaction is confirmed on the Radix network, your balance will be updated\.\n"
            f"Use /spinner\_balance to check your current balance\.\n\n"
            f"⚠️ Remember: This is just for fun\! Don't leave large amounts of XRD in your game account\.",
            parse_mode="MarkdownV2",
            reply_to_message_id=update.message.message_id
        )

async def max_bet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Tell the user the maximum amount of XRD they can spin."""
    if not await check_maintenance_mode(update):
        return
    if not await check_chat_permissions(update):
        return
    try:
        game_balance = await get_game_account_balance()
        
        # Calculate maximum allowed win based on percentage of game balance
        max_win_percentage = MAX_WIN_PERCENTAGE
        
        # Regular spin calculations
        regular_max_multiplier = 12.0
        regular_max_win = max(0, game_balance * (max_win_percentage / 100))
        regular_max_bet = min(MAX_SPIN_AMOUNT, regular_max_win / regular_max_multiplier)
        
        # 7s only spin calculations (4x lower max bet due to 4x higher multiplier)
        sevens_max_multiplier = 48.0
        sevens_max_win = max(0, game_balance * (max_win_percentage / 100))
        sevens_max_bet = min(MAX_SPIN_AMOUNT / 4, sevens_max_win / sevens_max_multiplier)
        
        # Die roll calculations
        die_max_multiplier = 5.0
        die_max_win = max(0, game_balance * (max_win_percentage / 100))
        die_max_bet = min(MAX_SPIN_AMOUNT, die_max_win / die_max_multiplier)
        
        await update.message.reply_text(
            "🎰 Maximum Bet Information 🎰\n\n"
            f"Current game balance: {game_balance:.2f} XRD\n"
            f"Max win percentage: {max_win_percentage}%\n\n"
            "Regular Spin (/spin):\n"
            f"• Maximum bet: {regular_max_bet:.2f} XRD\n"
            f"• Maximum win: {regular_max_win:.2f} XRD\n"
            f"• Payout multiplier: 12x\n\n"
            "7s Only Spin (/spin_7s):\n"
            f"• Maximum bet: {sevens_max_bet:.2f} XRD\n"
            f"• Maximum win: {sevens_max_win:.2f} XRD\n"
            f"• Payout multiplier: 48x\n\n"
            "Die Roll (/die):\n"
            f"• Maximum bet: {die_max_bet:.2f} XRD\n"
            f"• Maximum win: {die_max_win:.2f} XRD\n"
            f"• Payout multiplier: 5x\n\n"
            "Note: The 7s only spin has a lower maximum bet to maintain the same maximum potential win.",
            reply_to_message_id=update.message.message_id
        )
    except Exception as e:
        logger.error(f"Error calculating max bet: {e}")
        await update.message.reply_text(
            "Sorry, there was an error calculating the maximum bet. Please try again later.",
            reply_to_message_id=update.message.message_id
        )

async def payouts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show the payout multipliers for different winning combinations."""
    if not await check_maintenance_mode(update):
        return
    if not await check_chat_permissions(update):
        return
    await update.message.reply_text(
        "🎰 Slot Machine Payouts 🎰\n\n"
        "Regular Spin (/spin):\n"
        "🍫 Three BARs: 12x your bet\n"
        "🍇 Three Grapes: 12x your bet\n"
        "🍋 Three Lemons: 12x your bet\n"
        "🎰 Three 7s: 12x your bet\n\n"
        "7s Only Spin (/spin_7s):\n"
        "🎰 Three 7s: 48x your bet\n"
        "(All other combinations: 0x)\n\n"
        "Die Roll (/die):\n"
        "🎲 Roll a 6: 5x your bet\n"
        "(All other numbers: 0x)\n\n"
        "Examples:\n"
        "• Regular spin: Bet 10 XRD, get three 7s = Win 120 XRD\n"
        "• 7s only spin: Bet 10 XRD, get three 7s = Win 480 XRD\n"
        "• Die roll: Bet 10 XRD, roll a 6 = Win 50 XRD",
        reply_to_message_id=update.message.message_id
    )

async def backup_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Trigger an immediate backup (admin only)."""
    if not await check_maintenance_mode(update):
        return
    if not await check_chat_permissions(update):
        return
    user_id = update.effective_user.id
    
    # Check if user is admin
    if str(user_id) == os.getenv("GAME_OWNER_TELEGRAM_ID", "0"):
        try:
            from backup_db import create_backup
            await create_backup()
            await update.message.reply_text("✅ Backup completed successfully!")
        except Exception as e:
            await update.message.reply_text(f"❌ Error creating backup: {e}")
    else:
        await update.message.reply_text("This command is only available to administrators.")

async def restore_backup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Restore from a backup file sent via Telegram (admin only)."""
    if not await check_maintenance_mode(update):
        return
    user_id = update.effective_user.id
    
    # Check if user is admin
    if str(user_id) != os.getenv("GAME_OWNER_TELEGRAM_ID", "0"):
        await update.message.reply_text("This command is only available to administrators.")
        return

    # Check if this is a file upload with a caption starting with /restore_backup
    if update.message.caption and update.message.caption.startswith('/restore_backup'):
        document = update.message.document
    # Check if this is a reply to /restore_backup with a file
    elif update.message.reply_to_message and update.message.reply_to_message.document:
        document = update.message.reply_to_message.document
    # Regular command with attached document
    elif update.message.document:
        document = update.message.document
    else:
        await update.message.reply_text(
            "Please attach a database backup file with this command.\n"
            "You can:\n"
            "1. Upload file with caption /restore_backup\n"
            "2. Reply to a file with /restore_backup\n"
            "3. Use /restore_backup with attached file"
        )
        return
        
    try:
        # Download the file
        file = await context.bot.get_file(document.file_id)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        temp_path = f"temp_restore_{timestamp}.db"
        
        await file.download_to_drive(temp_path)
        
        # Verify it's a SQLite database
        async with aiosqlite.connect(temp_path) as db:
            cursor = await db.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = await cursor.fetchall()
            if not any(table[0] == "users" for table in tables):
                os.remove(temp_path)
                await update.message.reply_text("❌ Invalid backup file: not a valid database backup.")
                return
        
        # Create a backup of current database before restoring
        backup_path = f"pre_restore_backup_{timestamp}.db"
        shutil.copy2(DB_FILE, backup_path)
        
        # Replace current database with restored one
        shutil.copy2(temp_path, DB_FILE)
        os.remove(temp_path)
        
        await update.message.reply_text(
            "✅ Database restored successfully!\n"
            f"Previous database backed up as: {backup_path}"
        )
        
    except Exception as e:
        logger.error(f"Error restoring backup: {e}")
        await update.message.reply_text(f"❌ Error restoring backup: {e}")

# Add health check endpoint
async def health_check():
    app = web.Application()
    routes = web.RouteTableDef()

    @routes.get('/health')
    async def health(request):
        return web.Response(text='OK')

    app.add_routes(routes)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', int(os.getenv('PORT', '8000')))
    await site.start()

# Add the new toggle_migrate command
async def toggle_migrate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Toggle maintenance mode (game owner only)."""
    user_id = update.effective_user.id
    
    if user_id != GAME_OWNER_TELEGRAM_ID:
        await update.message.reply_text(
            "⚠️ This command is only available to administrators.",
            reply_to_message_id=update.message.message_id
        )
        return
        
    global MAINTENANCE_MODE
    MAINTENANCE_MODE = not MAINTENANCE_MODE
    
    status = "enabled" if MAINTENANCE_MODE else "disabled"
    await update.message.reply_text(
        f"🛠️ Maintenance mode {status}!\n\n"
        f"Bot is now {'blocking' if MAINTENANCE_MODE else 'accepting'} user commands.\n"
        f"Note: Game owner can always use all commands.",
        reply_to_message_id=update.message.message_id
    )

async def load_whitelist():
    """Load the whitelist from database."""
    try:
        async with get_db_read() as db:
            cursor = await db.execute("SELECT user_id FROM whitelist")
            rows = await cursor.fetchall()
            return [row[0] for row in rows]
    except Exception as e:
        logger.error(f"Error loading whitelist: {e}")
        return []

async def save_whitelist(user_id: int, action: str = "add"):
    """Save whitelist changes to database."""
    try:
        async with get_db_write() as db:
            if action == "add":
                await db.execute(
                    "INSERT INTO whitelist (user_id) VALUES (?)",
                    (user_id,)
                )
            elif action == "remove":
                await db.execute(
                    "DELETE FROM whitelist WHERE user_id = ?",
                    (user_id,)
                )
            await db.commit()
    except Exception as e:
        logger.error(f"Error saving whitelist: {e}")

async def check_whitelist(user_id: int) -> bool:
    """Check if a user is whitelisted."""
    whitelist = await load_whitelist()
    return user_id in whitelist or user_id == GAME_OWNER_TELEGRAM_ID

async def add_to_whitelist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Add a user to the whitelist (admin only)."""
    if not await check_maintenance_mode(update):
        return
        
    user_id = update.effective_user.id
    
    # Check if user is admin
    if user_id != GAME_OWNER_TELEGRAM_ID:
        await update.message.reply_text(
            "⚠️ This command is only available to administrators.",
            reply_to_message_id=update.message.message_id
        )
        return
        
    # Check if user ID is provided
    if not context.args:
        await update.message.reply_text(
            "Please specify the user ID to whitelist.\n"
            "Example: /whitelist_add 123456789",
            reply_to_message_id=update.message.message_id
        )
        return
        
    try:
        target_user_id = int(context.args[0])
        whitelist = await load_whitelist()
        
        if target_user_id in whitelist:
            await update.message.reply_text(
                f"User {target_user_id} is already whitelisted.",
                reply_to_message_id=update.message.message_id
            )
            return
            
        await save_whitelist(target_user_id, "add")
        
        await update.message.reply_text(
            f"✅ User {target_user_id} has been added to the whitelist.",
            reply_to_message_id=update.message.message_id
        )
    except ValueError:
        await update.message.reply_text(
            "Invalid user ID. Please provide a valid number.",
            reply_to_message_id=update.message.message_id
        )

async def remove_from_whitelist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove a user from the whitelist (admin only)."""
    if not await check_maintenance_mode(update):
        return
        
    user_id = update.effective_user.id
    
    # Check if user is admin
    if user_id != GAME_OWNER_TELEGRAM_ID:
        await update.message.reply_text(
            "⚠️ This command is only available to administrators.",
            reply_to_message_id=update.message.message_id
        )
        return
        
    # Check if user ID is provided
    if not context.args:
        await update.message.reply_text(
            "Please specify the user ID to remove from whitelist.\n"
            "Example: /whitelist_remove 123456789",
            reply_to_message_id=update.message.message_id
        )
        return
        
    try:
        target_user_id = int(context.args[0])
        whitelist = await load_whitelist()
        
        if target_user_id not in whitelist:
            await update.message.reply_text(
                f"User {target_user_id} is not in the whitelist.",
                reply_to_message_id=update.message.message_id
            )
            return
            
        await save_whitelist(target_user_id, "remove")
        
        await update.message.reply_text(
            f"✅ User {target_user_id} has been removed from the whitelist.",
            reply_to_message_id=update.message.message_id
        )
    except ValueError:
        await update.message.reply_text(
            "Invalid user ID. Please provide a valid number.",
            reply_to_message_id=update.message.message_id
        )

async def list_whitelist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all whitelisted users (admin only)."""
    if not await check_maintenance_mode(update):
        return
        
    user_id = update.effective_user.id
    
    # Check if user is admin
    if user_id != GAME_OWNER_TELEGRAM_ID:
        await update.message.reply_text(
            "⚠️ This command is only available to administrators.",
            reply_to_message_id=update.message.message_id
        )
        return
        
    whitelist = await load_whitelist()
    
    if not whitelist:
        await update.message.reply_text(
            "No users are currently whitelisted.",
            reply_to_message_id=update.message.message_id
        )
        return
        
    whitelist_text = "📋 Whitelisted Users:\n\n"
    for user_id in whitelist:
        whitelist_text += f"• {user_id}\n"
        
    await update.message.reply_text(
        whitelist_text,
        reply_to_message_id=update.message.message_id
    )

async def check_chat_permissions(update: Update) -> bool:
    """Check if the user is allowed to use the bot in this chat."""
    chat = update.effective_chat
    user_id = update.effective_user.id
    command = update.message.text.split()[0].lower() if update.message and update.message.text else ""
    
    # Allow admin to use the bot anywhere
    if user_id == GAME_OWNER_TELEGRAM_ID:
        return True
    
    # Block private chat usage for everyone except admin
    if chat.type == "private":
        await update.message.reply_text(
            "⚠️ This bot can only be used in the designated group chat (@SamuskySMK)."
        )
        return False
    
    # For group chat, check if user is whitelisted for game commands
    if chat.type in ["group", "supergroup"] and chat.username and chat.username.lower() == ALLOWED_GROUP_USERNAME.lower():
        # List of game commands that require whitelist
        game_commands = ["/spin", "/spin_7s", "/spinner_balance", "/spinner_max_bet", "/spinner_payouts"]
        
        # If it's a game command, check whitelist
        if command in game_commands:
            if await check_whitelist(user_id):
                return True
            await update.message.reply_text(
                "⚠️ You are not whitelisted to use game commands.\n"
                "Please contact an administrator to be added to the whitelist."
            )
            return False
            
        # For non-game commands, allow access
        return True
    
    # Not allowed in other cases
    await update.message.reply_text(
        "⚠️ This bot can only be used in the designated group chat (@SamuskySMK)."
    )
    return False

async def die(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Play dice using XRD. Win 5x if you roll a 6."""
    if not await check_maintenance_mode(update):
        return
    if not await check_chat_permissions(update):
        return
        
    user_id = update.effective_user.id
    
    # Check if user has an ongoing spin
    if ongoing_spins.get(user_id):
        await update.message.reply_text(
            "⚠️ Please wait for your current roll to complete before starting a new one.",
            reply_to_message_id=update.message.message_id
        )
        return
        
    # Check if amount is provided
    if not context.args:
        await update.message.reply_text(
            "Please specify the amount of XRD to roll or 'max'.\n"
            "Example: /die 1.0 or /die max\n"
            "For multiple rolls: /die 1.0 3 (will roll up to 3 times)"
        )
        return
    
    # Parse number of rolls
    num_rolls = 1
    if len(context.args) > 1:
        try:
            num_rolls = int(context.args[1])
            if num_rolls < 1 or num_rolls > 3:  # Changed to 3 max rolls
                await update.message.reply_text(
                    "Number of rolls must be between 1 and 3."
                )
                return
        except ValueError:
            await update.message.reply_text(
                "Invalid number of rolls. Please use a whole number between 1 and 3."
            )
            return

    # Get amount for each roll
    amount = await get_spin_amount(update, context.args[0], is_die=True)
    if amount is None:
        return
        
    # Check if user has enough balance for all rolls (only add fee once)
    async with get_db_read() as db:
        cursor = await db.execute(
            "SELECT radix_address FROM users WHERE telegram_id = ?", 
            (user_id,)
        )
        user_data = await cursor.fetchone()
        if not user_data:
            await update.message.reply_text(
                "You don't have an account yet. Use /create_spinner to create one."
            )
            return
        
        address = user_data[0]
    
    balance = await get_radix_balance(address)
    total_needed = (amount * num_rolls) + 0.5  # Include fee only once
    
    if balance < total_needed:
        await update.message.reply_text(
            f"Insufficient balance for {num_rolls} rolls.\n"
            f"Required: {total_needed:.2f} XRD (including 0.5 XRD fee)\n"
            f"Your balance: {balance:.2f} XRD"
        )
        return
    
    # Set ongoing spin flag
    ongoing_spins[user_id] = True
    
    try:
        # Create a task for multiple rolls
        task = asyncio.create_task(process_multiple_die_rolls(update.message, amount, user_id, num_rolls))
    except Exception as e:
        ongoing_spins[user_id] = False
        raise e

async def process_multiple_die_rolls(message, amount: float, user_id: int, num_rolls: int):
    """Process multiple die rolls and aggregate results."""
    try:
        total_winnings = 0
        winning_rolls = []
        dice_messages = []
        
        # Send initial message without keyboard
        initial_message = await message.reply_text(
            f"🎲 Rolling {num_rolls} times with {amount:.2f} XRD each...",
            reply_to_message_id=message.message_id
        )
        
        # Send all dice at once
        for _ in range(num_rolls):
            dice_msg = await message.reply_dice(emoji="🎲")
            dice_messages.append(dice_msg)

        # Get user and game data once
        async with get_db_read() as db:
            cursor = await db.execute(
                "SELECT radix_address, private_key, public_key FROM users WHERE telegram_id = ?", 
                (user_id,)
            )
            user_data = await cursor.fetchone()
            if not user_data:
                await initial_message.edit_text("Error: Account not found")
                ongoing_spins[user_id] = False
                return
            
            address, private_key, public_key = user_data
            
            # Get game account info
            cursor = await db.execute(
                "SELECT game_address, game_private_key, game_public_key FROM game_stats WHERE id = 1"
            )
            game_account = await cursor.fetchone()
            
            if not game_account:
                await initial_message.edit_text("Error: Game account not configured")
                ongoing_spins[user_id] = False
                return
            
            game_info = {
                "address": game_account[0],
                "private_key": game_account[1],
                "public_key": game_account[2]
            }

        # Wait for all dice animations to complete
        await asyncio.sleep(4)

        # Process results - only 6 wins
        for i, dice_msg in enumerate(dice_messages):
            dice_value = dice_msg.dice.value
            is_win = dice_value == 6  # Only 6 wins
            if is_win:
                winning_rolls.append(i + 1)
                total_winnings += amount * 5.0  # 5x multiplier for 6

        # Calculate net result
        total_cost = amount * num_rolls
        net_result = total_winnings - total_cost

        # Create and submit transaction for net result
        if net_result != 0:
            manifest = settle_spin_manifest(
                game_info["address"],
                address,
                net_result
            )
            
            # Use game account to pay if user won, user account to pay if they lost
            if net_result > 0:
                result = await submit_transaction_with_manifest(
                    manifest,
                    game_info["address"],
                    game_info["private_key"],
                    game_info["public_key"]
                )
            else:
                result = await submit_transaction_with_manifest(
                    manifest,
                    address,
                    private_key,
                    public_key
                )

            if "error" in result:
                await initial_message.edit_text(
                    f"❌ Transaction failed: {result['error']}\nPlease try again."
                )
                ongoing_spins[user_id] = False
                return

        # Update game stats if user won
        if net_result > 0:
            async with get_db_write() as db:
                await db.execute(
                    "UPDATE game_stats SET total_winnings_paid = total_winnings_paid + ? WHERE id = 1",
                    (net_result - 0.5,)
                )
                await db.commit()

        # Update the initial message with results and add appropriate buttons
        result_text = (
            f"🎲 Results:\n\n"
            f"Winning rolls: {', '.join(map(str, winning_rolls))}\n"
            f"Total winnings: {total_winnings:.2f} XRD\n"
            f"Total cost: {total_cost:.2f} XRD\n"
            f"Net result: {net_result:.2f} XRD"
        )
        
        # Add fee deduction message only if user won
        if net_result > 0:
            result_text += "\n(0.5 XRD fee deducted)"
            result_text += "\n\n🎉 Congratulations! You rolled a 6 and won 5x your bet!"
            
            # Create keyboard with roll again button only
            keyboard = [[InlineKeyboardButton("Roll Again", callback_data=f"die_again_{amount}_{num_rolls}")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
        else:
            result_text += "\n\nBetter luck next time! 🍀"
            
            # Create keyboard with refund button
            keyboard = [
                [InlineKeyboardButton("Roll Again", callback_data=f"die_again_{amount}_{num_rolls}")],
                [InlineKeyboardButton("⚠️ Request Refund (Removes from Whitelist)", callback_data=f"refund_{abs(net_result)}_{num_rolls}_{user_id}")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
        
        await initial_message.edit_text(
            result_text,
            reply_markup=reply_markup
        )

    except Exception as e:
        logger.error(f"Error during multiple die rolls: {e}")
        await message.reply_text(
            "Sorry, there was an error processing your rolls. Please try again later."
        )
    finally:
        ongoing_spins[user_id] = False

def main():
    """Start the bot."""
    # Create the Application
    application = Application.builder().token(TOKEN).build()

    # Log maintenance mode status on startup
    logger.info(f"Starting bot in maintenance mode: {MAINTENANCE_MODE}")
    
    # Add command handlers
    application.add_handler(CommandHandler("toggle_migrate", toggle_migrate))
    application.add_handler(CommandHandler("whitelist_add", add_to_whitelist))
    application.add_handler(CommandHandler("whitelist_remove", remove_from_whitelist))
    application.add_handler(CommandHandler("whitelist_list", list_whitelist))
    application.add_handler(CommandHandler("request_whitelist", request_whitelist))
    application.add_handler(CommandHandler("start_spinning", start))
    application.add_handler(CommandHandler("create_spinner", create_account))
    application.add_handler(CommandHandler("spinner_balance", check_balance))
    application.add_handler(CommandHandler("spin", spin))
    application.add_handler(CommandHandler("spin_7s", spin_7s))
    application.add_handler(CommandHandler("die", die))
    application.add_handler(CommandHandler("withdraw_spinner", withdraw))
    application.add_handler(CommandHandler("top_up_spinner", top_up_balance))
    application.add_handler(CommandHandler("spinner_max_bet", max_bet))
    application.add_handler(CommandHandler("spinner_payouts", payouts))
    application.add_handler(CommandHandler("backup_now", backup_now))
    application.add_handler(CommandHandler("restore_backup", restore_backup))
    
    # Add callback query handlers
    application.add_handler(CallbackQueryHandler(handle_spin_again, pattern="^spin_again_"))
    application.add_handler(CallbackQueryHandler(handle_spin_7s_again, pattern="^spin_7s_again_"))
    application.add_handler(CallbackQueryHandler(handle_die_again, pattern="^die_again_"))
    application.add_handler(CallbackQueryHandler(handle_refund, pattern="^refund_"))

    # Start health check
    loop = asyncio.get_event_loop()
    loop.create_task(health_check())

    # Start the Bot
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main() 