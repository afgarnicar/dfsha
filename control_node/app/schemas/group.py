"""Esquemas de entrada y salida de la API de grupos."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from shared.constants import MAX_NAME_LENGTH


class CreateGroupRequest(BaseModel):
    """Datos para crear un grupo nuevo."""

    name: str = Field(min_length=1, max_length=MAX_NAME_LENGTH)


class AddMemberRequest(BaseModel):
    """Datos para agregar un usuario a un grupo."""

    user_id: int


class GroupResponse(BaseModel):
    """Representación pública de un grupo."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    created_at: datetime
