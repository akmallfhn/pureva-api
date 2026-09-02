from typing import Any

from pydantic import BaseModel, Field


class WAWebhookChange(BaseModel):
    field: str
    value: dict[str, Any] = Field(default_factory=dict)


class WAWebhookEntry(BaseModel):
    id: str
    changes: list[WAWebhookChange] = Field(default_factory=list)


class WAWebhookBody(BaseModel):
    object: str
    entry: list[WAWebhookEntry] = Field(default_factory=list)
