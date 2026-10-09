from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field


class DomclickSuggestion(BaseModel):
    model_config = ConfigDict(extra="ignore")

    display_name: str = Field(description="Адрес в написании Домклика")
    guid: str | None = Field(
        default=None,
        description="GUID дома, ключ для оценки; у подсказок уровня улицы или района его нет",
    )


class DomclickSuggestResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    answer: list[DomclickSuggestion] = Field(description="Подходящие дома, первый самый релевантный")
    success: bool


class DomclickPrediction(BaseModel):
    model_config = ConfigDict(extra="ignore")

    market_price: float = Field(description="Рыночная цена в миллионах рублей")
    market_price_m2: float = Field(description="Цена квадратного метра в миллионах рублей")
    min_market_price: float = Field(description="Нижняя граница диапазона в миллионах рублей")
    max_market_price: float = Field(description="Верхняя граница диапазона в миллионах рублей")


class DomclickPredictResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    answer: DomclickPrediction | None = Field(
        default=None, description="Нет, когда модель Домклика не может оценить дом (success=false)"
    )
    success: bool


@dataclass
class DomclickEstimate:
    offer_id: int
    house_guid: str
    address: str
    market_price: int
    price_m2: int
    min_price: int
    max_price: int
    url: str
