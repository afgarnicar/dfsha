"""Repositorio de acceso a las entradas de ACL.

Las ACL representan excepciones de permiso por usuario o grupo sobre un recurso
concreto (un archivo o un directorio).
"""

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.access import AclEntry

# Tipos de recurso admitidos en una entrada de ACL.
RESOURCE_FILE = "file"
RESOURCE_DIRECTORY = "directory"

# Tipos de sujeto admitidos en una entrada de ACL.
SUBJECT_USER = "user"
SUBJECT_GROUP = "group"


class AclRepository:
    """Operaciones de persistencia sobre las entradas de ACL."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def list_for_resource(
        self, resource_type: str, resource_id: int
    ) -> Sequence[AclEntry]:
        """Devuelve todas las entradas de ACL de un recurso."""
        statement = select(AclEntry).where(
            AclEntry.resource_type == resource_type,
            AclEntry.resource_id == resource_id,
        )
        return self._session.scalars(statement).all()

    def find_for_subject(
        self,
        resource_type: str,
        resource_id: int,
        subject_type: str,
        subject_id: int,
    ) -> AclEntry | None:
        """Busca la entrada de ACL de un sujeto concreto sobre un recurso."""
        statement = select(AclEntry).where(
            AclEntry.resource_type == resource_type,
            AclEntry.resource_id == resource_id,
            AclEntry.subject_type == subject_type,
            AclEntry.subject_id == subject_id,
        )
        return self._session.scalars(statement).first()

    def create(
        self,
        resource_type: str,
        resource_id: int,
        subject_type: str,
        subject_id: int,
        can_read: bool,
        can_write: bool,
        can_execute: bool,
    ) -> AclEntry:
        """Crea una entrada de ACL para un sujeto sobre un recurso."""
        entry = AclEntry(
            resource_type=resource_type,
            resource_id=resource_id,
            subject_type=subject_type,
            subject_id=subject_id,
            can_read=can_read,
            can_write=can_write,
            can_execute=can_execute,
        )
        self._session.add(entry)
        return entry
