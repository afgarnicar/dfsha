"""Esquemas de entrada y salida de la API del sistema de archivos lógico."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class CreateDirectoryRequest(BaseModel):
    """Datos para crear un directorio."""

    # Ruta lógica absoluta del directorio a crear, por ejemplo /documentos/u.
    path: str = Field(min_length=1)
    # Permisos en modo Unix (0 a 0o777). Por defecto 0o750.
    permissions: int = Field(default=0o750, ge=0, le=0o777)


class RemoveDirectoryRequest(BaseModel):
    """Datos para eliminar un directorio."""

    path: str = Field(min_length=1)


class DirectoryResponse(BaseModel):
    """Representación pública de un directorio."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    parent_id: int | None
    owner_id: int
    group_id: int | None
    permissions: int
    created_at: datetime


class FileSummary(BaseModel):
    """Resumen de un archivo dentro de un listado de directorio."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    size_bytes: int
    status: str


class DirectoryListingResponse(BaseModel):
    """Contenido de un directorio: subdirectorios y archivos."""

    path: str
    directory_id: int
    subdirectories: list[DirectoryResponse]
    files: list[FileSummary]
