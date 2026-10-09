from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from cian_watch.models.offer import Offer


class SearchApiData(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="ignore")

    offers_serialized: list[Offer] = Field(description="Объявления запрошенной страницы выдачи")
    json_query: dict[str, Any] = Field(description="Запрос, как его понял Циан; эхо для отладки, схема его")


class SearchApiResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    data: SearchApiData


class PageFetchResult(BaseModel):
    status: int = Field(description="HTTP-статус ответа API, 200 при успехе, 403 на антибот")
    text: str = Field(description="Тело ответа как есть: JSON при 200, HTML антибота иначе")
