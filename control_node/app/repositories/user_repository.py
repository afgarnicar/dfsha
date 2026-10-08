"""Repositorio de acceso a datos de los usuarios.

Encapsula las consultas y escrituras sobre la tabla ``users``, manteniendo la
lógica de negocio independiente de SQLAlchemy.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.user import User


class UserRepository:
    """Operaciones de persistencia sobre los usuarios."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_username(self, username: str) -> User | None:
        """Devuelve el usuario con el nombre dado, o None si no existe."""
        statement = select(User).where(User.username == username)
        return self._session.scalars(statement).first()

    def get_by_id(self, user_id: int) -> User | None:
        """Devuelve el usuario con el id dado, o None si no existe."""
        return self._session.get(User, user_id)

    def list_all(self) -> list[User]:
        """Devuelve todos los usuarios, ordenados por nombre."""
        statement = select(User).order_by(User.username)
        return list(self._session.scalars(statement).all())

    def create(self, username: str, password_hash: str) -> User:
        """Crea un usuario nuevo y activo con el hash de contraseña dado."""
        user = User(username=username, password_hash=password_hash, is_active=True)
        self._session.add(user)
        return user
