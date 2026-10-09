from html import escape

ADDRESS_HIDDEN = "адрес скрыт"
ADDRESS_PREFIXES = frozenset({"Россия", "Москва"})


def rubles(price: int) -> str:
    return f"{price:,}".replace(",", " ") + " ₽"


def millions(price: int) -> str:
    return f"{price / 1_000_000:.1f}".replace(".", ",")


def short_address(address: str | None) -> str:
    """Адрес без «Россия, Москва», уже экранированный под HTML Telegram."""
    if not address:
        return ADDRESS_HIDDEN
    parts = [part.strip() for part in address.split(",")]
    return escape(", ".join(part for part in parts if part not in ADDRESS_PREFIXES))
