from pydantic import BaseModel, ConfigDict, Field


class TelegramChat(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int = Field(description="chat_id, он же ключ в users и watchlist")


class TelegramMessage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    message_id: int
    chat: TelegramChat
    text: str | None = Field(default=None, description="Текст, у сообщений без текста (фото, стикер) нет")


class TelegramCallbackQuery(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = Field(description="Нужен для answerCallbackQuery, иначе у пользователя висит часики")
    data: str | None = Field(default=None, description="callback_data кнопки, у игровых кнопок нет")
    message: TelegramMessage | None = Field(
        default=None, description="Сообщение с кнопкой; нет, если оно слишком старое"
    )


class TelegramUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    update_id: int
    message: TelegramMessage | None = None
    callback_query: TelegramCallbackQuery | None = None


class TelegramUpdatesResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ok: bool
    result: list[TelegramUpdate] = Field(default_factory=list)
