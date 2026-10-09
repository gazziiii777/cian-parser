from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from cian_watch.models.offer import Offer


class SerpConfigEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")

    key: str = Field(description="Имя секции конфига frontend-serp, например initialState")
    value: Any = Field(description="Содержимое секции, схема у каждой своя, разбираем только initialState")


class SearchResults(BaseModel):
    model_config = ConfigDict(extra="ignore")

    offers: list[Offer] = Field(description="Объявления первой страницы выдачи в порядке сортировки")
    json_query: dict[str, Any] = Field(
        alias="jsonQuery",
        description="Поисковый запрос под URL выдачи в формате внутреннего API, схема его, отдаём как есть",
    )


class InitialState(BaseModel):
    model_config = ConfigDict(extra="ignore")

    results: SearchResults
