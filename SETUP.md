# PsychosomaticsGuide Bot — Инструкция по запуску

## 1. Установка зависимостей

```bash
cd PsychosomaticsGuide_bot
python -m venv venv
venv\Scripts\activate        # Windows
pip install -r requirements.txt
```

## 2. Настройка переменных окружения

```bash
cp .env.example .env
# Открыть .env и заполнить все значения
```

## 3. Настройка Supabase

1. Создать проект на [supabase.com](https://supabase.com)
2. Зайти в **SQL Editor**
3. Вставить и выполнить содержимое файла `database/schema.sql`
4. Скопировать **Project URL** и **anon key** в `.env`

## 4. Загрузка PDF в базу

### Через командную строку:
```bash
# Один файл
python -m services.ingest --file pdfs/луиза_хей.pdf --title "Луиза Хей" --author "Луиза Хей" --tags "луиза хей,органы"

# Папка с PDF (интерактивный режим)
python -m services.ingest --folder pdfs/
```

### Через Telegram (удобнее):
1. Запустить бота
2. Отправить PDF файл боту с вашего аккаунта администратора
3. Бот спросит название, автора, теги — ввести и подтвердить

## 5. Запуск бота

```bash
python bot.py
```

## 6. Деплой на Railway

1. Создать проект на [railway.app](https://railway.app)
2. Подключить GitHub репозиторий или задеплоить через CLI
3. Добавить переменные окружения из `.env`
4. Добавить `Procfile`:
   ```
   worker: python bot.py
   ```

---

## Команды бота

| Команда | Описание |
|---------|----------|
| `/start` | Главное меню |
| `/help` | Как пользоваться |
| `/subscribe` | Оформить подписку |
| `/admin` | Панель администратора (только для ADMIN_IDS) |
| `/stats` | Статистика базы (admin) |
| `/sources` | Список источников (admin) |

## Структура проекта

```
PsychosomaticsGuide_bot/
├── bot.py                  # точка входа
├── requirements.txt
├── .env.example
├── config/
│   └── settings.py         # конфиг из .env
├── database/
│   └── schema.sql          # SQL для Supabase
├── handlers/
│   ├── user.py             # хендлеры пользователей
│   └── admin.py            # хендлеры администратора
├── services/
│   ├── rag.py              # RAG поиск + агрегация
│   ├── ingest.py           # загрузка PDF
│   └── users.py            # работа с пользователями
├── keyboards/
│   └── inline.py           # клавиатуры
├── texts/
│   └── messages.py         # тексты сообщений
└── pdfs/                   # папка для PDF (локально)
```

## Стоимость API (примерно)

| Операция | Модель | Цена |
|----------|--------|------|
| Векторизация PDF (1 книга ~300 стр) | text-embedding-3-small | ~$0.02 |
| Один поисковый запрос (embed) | text-embedding-3-small | ~$0.00002 |
| Генерация ответа | claude-sonnet-4-6 | ~$0.01–0.03 |
