"""
Настройка тестового окружения.
Устанавливает фиктивные env-переменные до импорта модулей,
чтобы клиенты (Supabase, OpenAI, Anthropic) создавались без реального соединения.
"""
import os

os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("BOT_TOKEN", "1234567890:AAAAAAAbbbbbbbbb")
os.environ.setdefault("ADMIN_IDS", "123")
