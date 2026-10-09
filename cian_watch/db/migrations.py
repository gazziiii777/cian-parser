from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Connection
from sqlalchemy.ext.asyncio import AsyncEngine

ALEMBIC_DIR = Path(__file__).resolve().parents[2] / "alembic"


async def apply_migrations(engine: AsyncEngine) -> None:
    """Накатывает все миграции до head на соединении приложения, чтобы alembic не поднимал
    свой engine и не требовал alembic.ini с URL. Тот же env.py работает и из CLI."""
    config = Config()
    config.set_main_option("script_location", str(ALEMBIC_DIR))
    async with engine.begin() as connection:
        await connection.run_sync(_upgrade_to_head, config)


def _upgrade_to_head(connection: Connection, config: Config) -> None:
    config.attributes["connection"] = connection
    command.upgrade(config, "head")
