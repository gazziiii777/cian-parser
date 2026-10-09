import argparse
import asyncio
from datetime import UTC, datetime

from cian_watch.db.engine import create_engine, create_sessions
from cian_watch.db.users import UsersRepository
from config.settings import Settings


def run() -> None:
    parser = argparse.ArgumentParser(prog="cian-watch-users", description="Доступ к уведомлениям в Telegram")
    commands = parser.add_subparsers(dest="command", required=True)
    add = commands.add_parser("add", help="выдать доступ по chat_id (узнаётся из getUpdates бота)")
    add.add_argument("chat_id", type=int)
    add.add_argument("name")
    remove = commands.add_parser("remove", help="отозвать доступ, строка остаётся")
    remove.add_argument("chat_id", type=int)
    commands.add_parser("list", help="показать всех")
    args = parser.parse_args()
    asyncio.run(_execute(args))


async def _execute(args: argparse.Namespace) -> None:
    engine = create_engine(settings=Settings())  # type: ignore[call-arg]  # values come from CIAN_* env
    users = UsersRepository(sessions=create_sessions(engine=engine))
    try:
        if args.command == "add":
            await users.grant(telegram_chat_id=args.chat_id, name=args.name, created_at=datetime.now(UTC))
            print(f"access granted: {args.name} ({args.chat_id})")
        elif args.command == "remove":
            revoked = await users.revoke(telegram_chat_id=args.chat_id)
            print("access revoked" if revoked else "no such user")
        else:
            for user in await users.list_all():
                state = "active" if user.is_active else "revoked"
                print(f"{user.telegram_chat_id}\t{user.name}\t{state}\t{user.created_at:%Y-%m-%d}")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    run()
