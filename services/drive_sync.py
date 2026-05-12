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
  5. Указать путь к JSON в .env: GOOGLE_SERVICE_ACCOUNT_JSON=credentials.json
  6. ID папки взять из URL: drive.google.com/drive/folders/ВОТ_ЭТО_ID
"""

import asyncio
import json
import logging
import os
import tempfile
from datetime import datetime, timezone

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from supabase import create_client

from config.settings import SUPABASE_URL, SUPABASE_KEY, ADMIN_IDS
from services.ingest import ingest_pdf

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

DRIVE_SYNC_INTERVAL_MINUTES = int(
    os.getenv("DRIVE_SYNC_INTERVAL_MINUTES", "60"))
GOOGLE_SERVICE_ACCOUNT_JSON = os.getenv(
    "GOOGLE_SERVICE_ACCOUNT_JSON", "credentials.json")
# На Railway передаётся содержимое JSON целиком
GOOGLE_SERVICE_ACCOUNT_JSON_CONTENT = os.getenv(
    "GOOGLE_SERVICE_ACCOUNT_JSON_CONTENT", "")
DRIVE_FOLDER_ID = os.getenv("DRIVE_FOLDER_ID", "")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


# ── Google Drive клиент ────────────────────────────────────────────────────

def get_drive_service():
    """Создаём клиент Google Drive API через Service Account.

    Поддерживает два способа передачи credentials:
    - GOOGLE_SERVICE_ACCOUNT_JSON_CONTENT — содержимое JSON (для Railway/cloud)
    - GOOGLE_SERVICE_ACCOUNT_JSON — путь к файлу (для локальной разработки)
    """
    if GOOGLE_SERVICE_ACCOUNT_JSON_CONTENT:
        info = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON_CONTENT)
        credentials = service_account.Credentials.from_service_account_info(
            info, scopes=SCOPES)
    elif os.path.exists(GOOGLE_SERVICE_ACCOUNT_JSON):
        credentials = service_account.Credentials.from_service_account_file(
            GOOGLE_SERVICE_ACCOUNT_JSON, scopes=SCOPES)
    else:
        raise FileNotFoundError(
            "Google credentials не найдены. Укажите "
            "GOOGLE_SERVICE_ACCOUNT_JSON_CONTENT (содержимое JSON) "
            "или GOOGLE_SERVICE_ACCOUNT_JSON (путь к файлу) в .env"
        )
    return build("drive", "v3", credentials=credentials)


# ── Работа с файлами ───────────────────────────────────────────────────────

def list_pdfs_in_folder(service, folder_id: str) -> list[dict]:
    """
    Возвращает список PDF файлов в папке Google Drive.
    Каждый элемент: {'id': str, 'name': str, 'modifiedTime': str}
    """
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
    """Получаем google_drive_id всех уже загруженных источников."""
    result = supabase.table("sources").select(
        "google_drive_id").not_.is_("google_drive_id", "null").execute()
    return {row["google_drive_id"] for row in result.data}


def mark_source_synced(source_id: int, drive_file_id: str,
                       drive_file_name: str) -> None:
    """Записываем google_drive_id в sources для отслеживания."""
    supabase.table("sources").update({
        "google_drive_id": drive_file_id,
        "filename": drive_file_name,
    }).eq("id", source_id).execute()


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
    """
    Один проход синхронизации.
    Возвращает {'new': int, 'errors': list[str]}
    """
    if not DRIVE_FOLDER_ID:
        logger.warning("DRIVE_FOLDER_ID не указан — синхронизация пропущена")
        return {"new": 0, "errors": ["DRIVE_FOLDER_ID не указан в .env"]}

    results = {"new": 0, "errors": []}

    try:
        service = get_drive_service()
    except FileNotFoundError as e:
        logger.error("Drive sync: %s", e)
        return {"new": 0, "errors": [str(e)]}

    # Список PDF на Drive
    drive_files = list_pdfs_in_folder(service, DRIVE_FOLDER_ID)
    logger.info("Drive sync: найдено %d PDF в папке", len(drive_files))

    if not drive_files:
        return results

    # Уже загруженные
    synced_ids = get_already_synced_ids()
    new_files = [f for f in drive_files if f["id"] not in synced_ids]

    if not new_files:
        logger.info("Drive sync: новых файлов нет")
        return results

    logger.info("Drive sync: %d новых файлов для загрузки", len(new_files))

    # Обрабатываем каждый новый файл
    for drive_file in new_files:
        file_id = drive_file["id"]
        file_name = drive_file["name"]
        title = file_name.replace(".pdf", "").replace("_", " ")

        logger.info("Drive sync: загружаю '%s'...", file_name)

        with tempfile.TemporaryDirectory() as tmpdir:
            dest_path = os.path.join(tmpdir, file_name)
            try:
                # Скачиваем
                download_pdf(service, file_id, dest_path)
                logger.info("Drive sync: скачан '%s'", file_name)

                # Вставляем источник (ingest_pdf создаст его внутри)
                # Нам нужно получить source_id после загрузки
                # Поэтому вставляем источник заранее с google_drive_id
                source_result = supabase.table("sources").insert({
                    "title": title,
                    "author": "",
                    "filename": file_name,
                    "google_drive_id": file_id,
                    "tags": [],
                    "is_active": True,
                }).execute()
                source_id = source_result.data[0]["id"]

                # Загружаем чанки напрямую, минуя создание источника
                from services.ingest import (
                    extract_text_from_pdf, split_into_chunks, upload_chunks
                )
                pages = extract_text_from_pdf(dest_path)
                chunks = split_into_chunks(pages)
                await upload_chunks(chunks, source_id, [])

                results["new"] += 1
                logger.info(
                    "Drive sync: '%s' успешно загружен (source_id=%d, чанков=%d)",
                    file_name, source_id, len(chunks),
                )

                # Уведомляем администратора
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

                # Удаляем источник если загрузка провалилась
                try:
                    supabase.table("sources").delete().eq(
                        "google_drive_id", file_id).execute()
                except Exception:
                    pass

    return results


async def drive_sync_loop(bot=None) -> None:
    """
    Бесконечный цикл синхронизации.
    Запускается как asyncio фоновая задача в bot.py.
    """
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


# ── Ручной запуск для тестирования ────────────────────────────────────────

if __name__ == "__main__":
    import sys
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
