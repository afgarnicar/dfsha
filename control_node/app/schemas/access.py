"""Esquemas de la API de permisos: chmod, grupo, ACL y grupos por nombre."""

from typing import Literal

from pydantic import BaseModel, Field

from shared.constants import MAX_NAME_LENGTH

SubjectType = Literal["user", "group"]


class ChmodRequest(BaseModel):
    """Nuevos permisos en notación octal de tres dígitos, por ejemplo "750"."""

    path: str = Field(min_length=1)
    mode: str = Field(pattern=r"^[0-7]{3}$")


class ChgrpRequest(BaseModel):
    """Grupo a asociar al recurso; ``null`` lo deja sin grupo."""

    path: str = Field(min_length=1)
    group: str | None = Field(default=None, max_length=MAX_NAME_LENGTH)


class AclSetRequest(BaseModel):
    """Entrada de ACL a crear o reemplazar."""

    path: str = Field(min_length=1)
    subject_type: SubjectType
    subject: str = Field(min_length=1, max_length=MAX_NAME_LENGTH)
    read: bool = False
    write: bool = False
    execute: bool = False


class AclRemoveRequest(BaseModel):
    """Entrada de ACL a quitar."""

    path: str = Field(min_length=1)
    subject_type: SubjectType
    subject: str = Field(min_length=1, max_length=MAX_NAME_LENGTH)


class AclEntryResponse(BaseModel):
    """Una entrada de ACL, con el nombre del usuario o grupo."""

    subject_type: str
    subject: str
    read: bool
    write: bool
    execute: bool


class ResourceInfoResponse(BaseModel):
    """Permisos de un archivo o directorio."""

    path: str
    type: str
    owner: str
    group: str | None
    mode: str  # octal, por ejemplo "750"
    mode_string: str  # por ejemplo "rwxr-x---"
    acl: list[AclEntryResponse]


class GroupCreateRequest(BaseModel):
    """Grupo a crear."""

    name: str = Field(min_length=1, max_length=MAX_NAME_LENGTH)


class GroupMemberRequest(BaseModel):
    """Usuario a agregar a un grupo, por nombre."""

    username: str = Field(min_length=1, max_length=MAX_NAME_LENGTH)
