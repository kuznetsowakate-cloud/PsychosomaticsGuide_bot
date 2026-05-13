"""
drive_sync.py — автоматическая синхронизация PDF с Google Drive.

Принцип работы:
1. Каждые N минут (DRIVE_SYNC_INTERVAL_MINUTES) смотрит в папку Google Drive
2. Получает список PDF файлов в папке
3. Сравнивает с таблицей sources в Supabase (по google_drive_id)
4. Скачивает и загружает новые файлы через ingest_pdf()
5. Уведомляет администратора в Telegram о результате

Настройка Google:
  1. Google Cloud Console → создать проект
  2. Включить Google Drive API
  3. Создать Service Account → скачать JSON ключ
  4. Папку на Google Drive расшарить для email сервис-аккаунта (роль: Viewer)
  5. В .env указать одно из:
     - GOOGLE_SERVICE_ACCOUNT_JSON_CONTENT = содержимое JSON (для Railway)
     - GOOGLE_SERVICE_ACCOUNT_JSON = {"type":...} или путь к файлу (локально)
  6. ID папки взять из URL: drive.google.com/drive/folders/ВОТ_ЭТО_ID
"""

import asyncio
import json
import logging
import os
import tempfile

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from supabase import create_client

from config.settings import SUPABASE_URL, SUPABASE_KEY, ADMIN_IDS

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

DRIVE_SYNC_INTERVAL_MINUTES = int(
    os.getenv("DRIVE_SYNC_INTERVAL_MINUTES", "60"))
DRIVE_FOLDER_ID = os.getenv("DRIVE_FOLDER_ID", "")

# Credentials: GOOGLE_SERVICE_ACCOUNT_JSON_CONTENT (отдельная переменная)
# или GOOGLE_SERVICE_ACCOUNT_JSON (может быть JSON-строкой или путём к файлу)
_CREDS_CONTENT = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON_CONTENT", "")
_CREDS_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "credentials.json")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

_drive_service = None


# ── Google Drive клиент ────────────────────────────────────────────────────

def get_drive_service():
    """Возвращает клиент Google Drive API (создаётся один раз при первом вызове)."""
    global _drive_service
    if _drive_service is not None:
        return _drive_service

    raw = _CREDS_CONTENT or _CREDS_JSON
    if raw.strip().startswith("{"):
        info = json.loads(raw)
        credentials = service_account.Credentials.from_service_account_info(
            info, scopes=SCOPES)
    elif os.path.exists(raw):
        credentials = service_account.Credentials.from_service_account_file(
            raw, scopes=SCOPES)
    else:
        raise FileNotFoundError(
            "Google credentials не найдены. Укажите "
            "GOOGLE_SERVICE_ACCOUNT_JSON_CONTENT (содержимое JSON) "
            "или GOOGLE_SERVICE_ACCOUNT_JSON (путь к файлу) в .env"
        )
    _drive_service = build("drive", "v3", credentials=credentials)
    return _drive_service


# ── Работа с файлами ───────────────────────────────────────────────────────

def list_pdfs_in_folder(service, folder_id: str) -> list[dict]:
    """Возвращает список PDF файлов в папке Google Drive."""
    query = (
        f"'{folder_id}' in parents "
        f"and mimeType='application/pdf' "
        f"and trashed=false"
    )
    result = service.files().list(
        q=query,
        fields="files(id, name, modifiedTime, size)",
        orderBy="createdTime",
    ).execute()
    return result.get("files", [])


def get_already_synced_ids() -> set[str]:
    """Получаем google_drive_id успешно загруженных источников (is_active=True).
    Записи с is_active=False — незавершённый ingest, они будут ретраиться."""
    result = supabase.table("sources").select(
        "google_drive_id"
    ).eq("is_active", True).not_.is_("google_drive_id", "null").execute()
    return {row["google_drive_id"] for row in result.data}


def download_pdf(service, file_id: str, dest_path: str) -> None:
    """Скачиваем PDF файл из Google Drive."""
    request = service.files().get_media(fileId=file_id)
    with open(dest_path, "wb") as f:
        downloader = MediaIoBaseDownload(f, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()


# ── Основной цикл синхронизации ────────────────────────────────────────────

async def sync_once(bot=None) -> dict:
    """Один проход синхронизации. Возвращает {'new': int, 'errors': list[str]}"""
    if not DRIVE_FOLDER_ID:
        logger.warning("DRIVE_FOLDER_ID не указан — синхронизация пропущена")
        return {"new": 0, "errors": ["DRIVE_FOLDER_ID не указан в .env"]}

    results = {"new": 0, "errors": []}

    try:
        service = get_drive_service()
    except Exception as e:
        logger.error("Drive sync: ошибка создания Drive клиента: %s", e)
        return {"new": 0, "errors": [str(e)]}

    drive_files = list_pdfs_in_folder(service, DRIVE_FOLDER_ID)
    logger.info("Drive sync: найдено %d PDF в папке", len(drive_files))

    if not drive_files:
        return results

    synced_ids = get_already_synced_ids()
    new_files = [f for f in drive_files if f["id"] not in synced_ids]

    if not new_files:
        logger.info("Drive sync: новых файлов нет")
        return results

    logger.info("Drive sync: %d новых файлов для загрузки", len(new_files))

    for drive_file in new_files:
        file_id = drive_file["id"]
        file_name = drive_file["name"]
        title = file_name.replace(".pdf", "").replace("_", " ")

        logger.info("Drive sync: загружаю '%s'...", file_name)

        with tempfile.TemporaryDirectory() as tmpdir:
            dest_path = os.path.join(tmpdir, file_name)
            try:
                download_pdf(service, file_id, dest_path)
                logger.info("Drive sync: скачан '%s'", file_name)

                source_result = supabase.table("sources").insert({
                    "title": title,
                    "author": "",
                    "filename": file_name,
                    "google_drive_id": file_id,
                    "tags": [],
                    "is_active": False,
                }).execute()
                source_id = source_result.data[0]["id"]

                from services.ingest import (
                    extract_text_from_pdf, split_into_chunks, upload_chunks,
                )
                pages = extract_text_from_pdf(dest_path)
                chunks = split_into_chunks(pages)
                await upload_chunks(chunks, source_id, [])

                supabase.table("sources").update(
                    {"is_active": True}
                ).eq("id", source_id).execute()

                results["new"] += 1
                logger.info(
                    "Drive sync: '%s' загружен (source_id=%d, чанков=%d)",
                    file_name, source_id, len(chunks),
                )

                if bot and ADMIN_IDS:
                    for admin_id in ADMIN_IDS:
                        try:
                            await bot.send_message(
                                admin_id,
                                f"✅ <b>Google Drive синхронизация</b>\n\n"
                                f"Новый файл загружен:\n"
                                f"📄 {file_name}\n"
                                f"🧩 Чанков: {len(chunks)}"
                            )
                        except Exception:
                            pass

            except Exception as e:
                error_msg = f"Ошибка загрузки '{file_name}': {e}"
                logger.error("Drive sync: %s", error_msg)
                results["errors"].append(error_msg)
                try:
                    supabase.table("sources").delete().eq(
                        "google_drive_id", file_id).execute()
                except Exception:
                    pass

    return results


async def drive_sync_loop(bot=None) -> None:
    """Бесконечный цикл синхронизации как asyncio фоновая задача."""
    interval_seconds = DRIVE_SYNC_INTERVAL_MINUTES * 60
    logger.info(
        "Drive sync: запущен, интервал %d мин", DRIVE_SYNC_INTERVAL_MINUTES)

    while True:
        try:
            logger.info("Drive sync: начинаю проверку...")
            result = await sync_once(bot=bot)
            if result["new"] > 0:
                logger.info(
                    "Drive sync: загружено новых файлов: %d", result["new"])
            if result["errors"]:
                for err in result["errors"]:
                    logger.error("Drive sync error: %s", err)
        except Exception as e:
            logger.error("Drive sync: критическая ошибка: %s", e)

        await asyncio.sleep(interval_seconds)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")

    async def run():
        print("Запуск разовой синхронизации с Google Drive...")
        result = await sync_once(bot=None)
        print(f"Новых файлов загружено: {result['new']}")
        if result["errors"]:
            print("Ошибки:")
            for e in result["errors"]:
                print(f"  - {e}")

    asyncio.run(run())
