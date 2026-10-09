import asyncio

from alembic import context
from sqlalchemy import Connection, pool
from sqlalchemy.ext.asyncio import create_async_engine

from cian_watch.db.tables import Base
from config.settings import Settings

target_metadata = Base.metadata


def run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_with_own_engine() -> None:
    engine = create_async_engine(Settings().database_url, poolclass=pool.NullPool)  # type: ignore[call-arg]
    async with engine.connect() as connection:
        await connection.run_sync(run_migrations)
    await engine.dispose()


def run_migrations_online() -> None:
    """Из приложения приходит готовое соединение в config.attributes, из CLI (alembic upgrade)
    его нет, и engine собирается из тех же настроек, что и у приложения."""
    connection = context.config.attributes.get("connection", None)
    if connection is None:
        asyncio.run(run_with_own_engine())
    else:
        run_migrations(connection)


run_migrations_online()
