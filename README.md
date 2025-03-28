ask ai to write a more detailed readme, this is almost completely vibe coded anyways

run this shit on railway or sum to test, initialize db: python init_db.py, then start the bot: python bot_fixed.py.

set up env variables:

# Telegram Bot Token (required)
TELEGRAM_BOT_TOKEN=ur_token

# Database file path (optional, defaults to radix_spin_bot.db)
DATABASE_FILE=/radix_spin_bot.db

# Game settings
MAX_WIN_PERCENTAGE=10

# Network ID (0x01 for mainnet, 0x02 for stokenet)
NETWORK_ID=0x01

# Radix Gateway API URL
RADIX_GATEWAY_API_URL=https://mainnet.radixdlt.com

# XRD resource address
XRD_ADDRESS=resource_rdx1tknxxxxxxxxxradxrdxxxxxxxxx009923554798xxxxxxxxxradxrd

# Game Owner Telegram ID (used to link the game account to your Telegram account)
GAME_OWNER_TELEGRAM_ID=your_tg_id

ALLOWED_GROUP_USERNAME=your_prefered_group_id

keep your db secure, and put it in persistent storage or sum, or back it up and keep it in your repo like a madman. (or move it to a /data folder).

anyways, this code is basically shit, but works. much of it is useless by now, but haven't deleted any.

glhf, don't use in prod, this is just an example, not safe to use at all.

and please, don't get people to gamble with this thing, educational only here.
