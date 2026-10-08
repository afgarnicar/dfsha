"""Esquemas de entrada y salida de la API de autenticación."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from shared.constants import MAX_NAME_LENGTH


class RegisterRequest(BaseModel):
    """Datos para registrar un usuario nuevo."""

    username: str = Field(min_length=3, max_length=MAX_NAME_LENGTH)
    password: str = Field(min_length=8)


class LoginRequest(BaseModel):
    """Credenciales para iniciar sesión."""

    username: str
    password: str


class TokenResponse(BaseModel):
    """Token de acceso emitido tras un login exitoso."""

    access_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    """Representación pública de un usuario, sin datos sensibles."""

    # Permite construir el esquema desde el modelo de SQLAlchemy.
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    is_active: bool
    created_at: datetime
