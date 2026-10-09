from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from cian_watch.db.tables import UserRow


class UsersRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def active_chat_ids(self) -> list[int]:
        async with self._sessions() as session:
            rows = await session.execute(select(UserRow.telegram_chat_id).where(UserRow.is_active.is_(True)))
            return list(rows.scalars())

    async def grant(self, telegram_chat_id: int, name: str, created_at: datetime) -> None:
        """Выдаёт доступ: новый пользователь вставляется, существующий снова становится активным."""
        statement = pg_insert(UserRow).values(
            telegram_chat_id=telegram_chat_id, name=name, is_active=True, created_at=created_at
        )
        statement = statement.on_conflict_do_update(
            index_elements=[UserRow.telegram_chat_id], set_={"is_active": True, "name": name}
        )
        async with self._sessions.begin() as session:
            await session.execute(statement)

    async def revoke(self, telegram_chat_id: int) -> bool:
        async with self._sessions.begin() as session:
            result = await session.execute(
                update(UserRow)
                .where(UserRow.telegram_chat_id == telegram_chat_id)
                .values(is_active=False)
                .returning(UserRow.id)
            )
            return result.first() is not None

    async def list_all(self) -> list[UserRow]:
        async with self._sessions() as session:
            rows = await session.execute(select(UserRow).order_by(UserRow.created_at))
            return list(rows.scalars())
