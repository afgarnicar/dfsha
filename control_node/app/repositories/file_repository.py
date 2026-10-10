"""Repositorio de acceso a datos de los archivos lógicos.

Por ahora solo expone las consultas necesarias para listar el contenido de un
directorio. La creación de archivos se implementa junto con la operación de
subida (PUT).
"""

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.filesystem import File
from shared.enums import FileStatus


class FileRepository:
    """Operaciones de persistencia sobre los archivos lógicos."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def list_in_directory(self, directory_id: int) -> Sequence[File]:
        """Devuelve los archivos de un directorio, ordenados por nombre."""
        statement = (
            select(File)
            .where(File.directory_id == directory_id)
            .order_by(File.name)
        )
        return self._session.scalars(statement).all()

    def get_in_directory(self, directory_id: int, name: str) -> File | None:
        """Busca un archivo por nombre dentro de un directorio."""
        statement = select(File).where(
            File.directory_id == directory_id, File.name == name
        )
        return self._session.scalars(statement).first()

    def create(
        self,
        name: str,
        directory_id: int,
        owner_id: int,
        permissions: int,
    ) -> File:
        """Crea un archivo nuevo en estado UPLOADING (sin bloques todavía)."""
        file = File(
            name=name,
            directory_id=directory_id,
            owner_id=owner_id,
            group_id=None,
            permissions=permissions,
            size_bytes=0,
            status=FileStatus.UPLOADING,
            current_version=1,
        )
        self._session.add(file)
        return file

    def delete(self, file: File) -> None:
        """Elimina un archivo (y en cascada sus bloques y ubicaciones)."""
        self._session.delete(file)
