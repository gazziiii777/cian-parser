from collections import defaultdict
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from cian_watch.db.tables import UserRow, WatchlistRow


class WatchlistRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def toggle(self, telegram_chat_id: int, offer_id: int, now: datetime) -> bool:
        """Включает слежение, если его не было, иначе снимает. Возвращает состояние после."""
        async with self._sessions.begin() as session:
            removed = await session.execute(
                delete(WatchlistRow)
                .where(WatchlistRow.telegram_chat_id == telegram_chat_id, WatchlistRow.offer_id == offer_id)
                .returning(WatchlistRow.id)
            )
            if removed.first() is not None:
                return False
            session.add(WatchlistRow(telegram_chat_id=telegram_chat_id, offer_id=offer_id, created_at=now))
            return True

    async def watchers(self, offer_ids: list[int]) -> dict[int, list[int]]:
        """chat_id активных пользователей по каждому объявлению из списка; объявлений без
        наблюдателей в ответе нет."""
        if not offer_ids:
            return {}
        async with self._sessions() as session:
            rows = await session.execute(
                select(WatchlistRow.offer_id, WatchlistRow.telegram_chat_id)
                .join(UserRow, UserRow.telegram_chat_id == WatchlistRow.telegram_chat_id)
                .where(WatchlistRow.offer_id.in_(offer_ids), UserRow.is_active.is_(True))
            )
            result: dict[int, list[int]] = defaultdict(list)
            for offer_id, chat_id in rows.all():
                result[offer_id].append(chat_id)
            return dict(result)
