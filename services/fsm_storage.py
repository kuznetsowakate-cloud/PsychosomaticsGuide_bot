"""Персистентное FSM-хранилище поверх Supabase (таблица fsm_states)."""

import asyncio
from typing import Any, Dict, Optional

from aiogram.fsm.storage.base import BaseStorage, StorageKey, StateType
from supabase import create_client

from config.settings import SUPABASE_URL, SUPABASE_KEY

_supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


class SupabaseStorage(BaseStorage):
    """FSM storage, переживающий рестарт бота."""

    @staticmethod
    def _key(key: StorageKey) -> str:
        return f"{key.bot_id}:{key.chat_id}:{key.user_id}:{key.destiny}"

    @staticmethod
    def _state_str(state: StateType) -> Optional[str]:
        if state is None:
            return None
        if isinstance(state, str):
            return state
        return state.state  # aiogram State object

    async def set_state(self, key: StorageKey, state: StateType = None) -> None:
        k = self._key(key)
        state_str = self._state_str(state)

        def _sync():
            existing = _supabase.table("fsm_states").select(
                "key").eq("key", k).execute()
            if existing.data:
                _supabase.table("fsm_states").update(
                    {"state": state_str}
                ).eq("key", k).execute()
            else:
                _supabase.table("fsm_states").insert(
                    {"key": k, "state": state_str, "data": {}}
                ).execute()

        await asyncio.to_thread(_sync)

    async def get_state(self, key: StorageKey) -> Optional[str]:
        k = self._key(key)
        result = await asyncio.to_thread(
            lambda: _supabase.table("fsm_states").select(
                "state").eq("key", k).execute()
        )
        return result.data[0]["state"] if result.data else None

    async def set_data(self, key: StorageKey, data: Dict[str, Any]) -> None:
        k = self._key(key)

        def _sync():
            existing = _supabase.table("fsm_states").select(
                "key").eq("key", k).execute()
            if existing.data:
                _supabase.table("fsm_states").update(
                    {"data": data}
                ).eq("key", k).execute()
            else:
                _supabase.table("fsm_states").insert(
                    {"key": k, "state": None, "data": data}
                ).execute()

        await asyncio.to_thread(_sync)

    async def get_data(self, key: StorageKey) -> Dict[str, Any]:
        k = self._key(key)
        result = await asyncio.to_thread(
            lambda: _supabase.table("fsm_states").select(
                "data").eq("key", k).execute()
        )
        return result.data[0]["data"] or {} if result.data else {}

    async def close(self) -> None:
        pass
