import os
from dotenv import load_dotenv

load_dotenv()

# Telegram
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_IDS = [
    int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()
]

# OpenAI
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
EMBEDDING_MODEL = "text-embedding-3-small"   # 1536 dims, дешевле

# Anthropic (Claude) — для генерации ответов
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = "claude-sonnet-4-6"

# Supabase
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

# Лимиты по тарифам (запросов в день)
PLAN_LIMITS = {
    "free":  3,
    "basic": 999999,
    "pro":   999999,
}

# Цены на тарифы (Telegram Stars)
PLAN_PRICES = {
    "basic": 150,   # Stars
    "pro":   300,   # Stars
}

PLAN_DAYS = {
    "basic": 30,
    "pro":   30,
}
