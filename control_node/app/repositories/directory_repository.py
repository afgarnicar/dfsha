"""Repositorio de acceso a datos de los directorios lógicos."""

from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.filesystem import Directory, File


class DirectoryRepository:
    """Operaciones de persistencia sobre los directorios lógicos."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_id(self, directory_id: int) -> Directory | None:
        """Devuelve el directorio con el id dado, o None si no existe."""
        return self._session.get(Directory, directory_id)

    def get_root(self) -> Directory | None:
        """Devuelve el directorio raíz (el que no tiene padre), o None."""
        statement = select(Directory).where(Directory.parent_id.is_(None))
        return self._session.scalars(statement).first()

    def get_child(self, parent_id: int, name: str) -> Directory | None:
        """Busca un subdirectorio por nombre dentro de un directorio padre."""
        statement = select(Directory).where(
            Directory.parent_id == parent_id, Directory.name == name
        )
        return self._session.scalars(statement).first()

    def list_children(self, parent_id: int) -> Sequence[Directory]:
        """Devuelve los subdirectorios directos de un directorio, por nombre."""
        statement = (
            select(Directory)
            .where(Directory.parent_id == parent_id)
            .order_by(Directory.name)
        )
        return self._session.scalars(statement).all()

    def count_children(self, directory_id: int) -> int:
        """Cuenta los subdirectorios directos de un directorio."""
        statement = (
            select(func.count())
            .select_from(Directory)
            .where(Directory.parent_id == directory_id)
        )
        return self._session.scalar(statement) or 0

    def count_files(self, directory_id: int) -> int:
        """Cuenta los archivos contenidos directamente en un directorio."""
        statement = (
            select(func.count())
            .select_from(File)
            .where(File.directory_id == directory_id)
        )
        return self._session.scalar(statement) or 0

    def create(
        self,
        name: str,
        parent_id: int | None,
        owner_id: int,
        group_id: int | None,
        permissions: int,
    ) -> Directory:
        """Crea un directorio nuevo."""
        directory = Directory(
            name=name,
            parent_id=parent_id,
            owner_id=owner_id,
            group_id=group_id,
            permissions=permissions,
        )
        self._session.add(directory)
        return directory

    def delete(self, directory: Directory) -> None:
        """Elimina un directorio."""
        self._session.delete(directory)
