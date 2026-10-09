import json
from dataclasses import dataclass

from pydantic import TypeAdapter, ValidationError

from cian_watch.exceptions import SerpParseError
from cian_watch.models import InitialState, Offer, SearchApiResponse, SerpConfigEntry
from cian_watch.patterns import SERP_CONFIG_RE


@dataclass
class _Defaults:
    INITIAL_STATE_KEY: str = "initialState"


_SERP_CONFIG_ADAPTER = TypeAdapter(list[SerpConfigEntry])


def parse_initial_state(html: str) -> InitialState:
    """Вытаскивает состояние выдачи (объявления и jsonQuery) из JSON, который Циан вшивает
    в HTML под window._cianConfig. Если вёрстка поменялась и блока нет или схема не сошлась,
    бросает SerpParseError."""
    match = SERP_CONFIG_RE.search(html)
    if match is None:
        raise SerpParseError("frontend-serp config block not found in page html")
    try:
        raw, _ = json.JSONDecoder().raw_decode(html, match.end())
        entries = _SERP_CONFIG_ADAPTER.validate_python(raw)
        for entry in entries:
            if entry.key == _Defaults.INITIAL_STATE_KEY:
                return InitialState.model_validate(entry.value)
    except (json.JSONDecodeError, ValidationError) as exc:
        raise SerpParseError(f"frontend-serp config has unexpected shape: {exc}") from exc
    raise SerpParseError("initialState section missing in frontend-serp config")


def parse_api_offers(body: str) -> list[Offer]:
    try:
        return SearchApiResponse.model_validate_json(body).data.offers_serialized
    except ValidationError as exc:
        raise SerpParseError(f"search api response has unexpected shape: {exc}") from exc
