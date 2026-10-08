"""Repositorio de acceso a datos de los archivos lógicos.

Por ahora solo expone las consultas necesarias para listar el contenido de un
directorio. La creación de archivos se implementa junto con la operación de
subida (PUT).
"""

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.filesystem import File


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
