from dataclasses import dataclass

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from config.settings import Settings


@dataclass
class _Defaults:
    POOL_SIZE: int = 5
    MAX_OVERFLOW: int = 0


def create_engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(
        settings.database_url,
        pool_size=_Defaults.POOL_SIZE,
        max_overflow=_Defaults.MAX_OVERFLOW,
        pool_pre_ping=True,
    )


def create_sessions(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)
