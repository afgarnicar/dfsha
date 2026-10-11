"""Esquemas de la API de acceso a archivos: open, read, write y close."""

from datetime import datetime

from pydantic import BaseModel, Field

from shared.enums import FileHandleMode


class OpenRequest(BaseModel):
    """Archivo a abrir y modo de apertura."""

    path: str = Field(min_length=1)
    mode: FileHandleMode


class OpenResponse(BaseModel):
    """Handle entregado al abrir un archivo."""

    handle: str
    mode: FileHandleMode
    name: str
    size_bytes: int
    expires_at: datetime


class ReadRequest(BaseModel):
    """Rango a leer de un archivo abierto."""

    handle: str = Field(min_length=1)
    offset: int = Field(ge=0)
    length: int = Field(ge=0)


class WriteResponse(BaseModel):
    """Resultado de una escritura."""

    size_bytes: int
    version: int
    block_count: int
    expires_at: datetime


class CloseRequest(BaseModel):
    """Handle a cerrar."""

    handle: str = Field(min_length=1)
