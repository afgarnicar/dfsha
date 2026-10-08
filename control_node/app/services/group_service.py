"""Servicio de gestión de grupos y membresías."""

from collections.abc import Sequence

from sqlalchemy.orm import Session

from app.models.user import Group
from app.repositories.group_repository import GroupRepository
from app.repositories.user_repository import UserRepository


class GroupAlreadyExistsError(Exception):
    """Se lanza al intentar crear un grupo cuyo nombre ya existe."""


class GroupNotFoundError(Exception):
    """Se lanza al operar sobre un grupo inexistente."""


class MemberUserNotFoundError(Exception):
    """Se lanza al agregar como miembro a un usuario inexistente."""


class GroupService:
    """Casos de uso de creación de grupos y gestión de miembros."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._groups = GroupRepository(session)
        self._users = UserRepository(session)

    def create_group(self, name: str) -> Group:
        """Crea un grupo nuevo. Lanza error si el nombre ya está en uso."""
        if self._groups.get_by_name(name) is not None:
            raise GroupAlreadyExistsError(name)
        group = self._groups.create(name)
        self._session.commit()
        self._session.refresh(group)
        return group

    def add_member(self, group_id: int, user_id: int) -> None:
        """Agrega un usuario a un grupo (operación idempotente)."""
        if self._groups.get_by_id(group_id) is None:
            raise GroupNotFoundError(group_id)
        if self._users.get_by_id(user_id) is None:
            raise MemberUserNotFoundError(user_id)

        # Si ya es miembro, no se hace nada (evita duplicar la membresía).
        if self._groups.get_membership(group_id, user_id) is None:
            self._groups.add_member(group_id, user_id)
            self._session.commit()

    def list_groups(self) -> Sequence[Group]:
        """Devuelve todos los grupos registrados."""
        return self._groups.list_all()
