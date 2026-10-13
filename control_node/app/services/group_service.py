"""Servicio de gestión de grupos y membresías.

Reglas (las mismas de ``/access/groups``):

- Quien crea un grupo queda como su primer miembro.
- Solo un miembro del grupo puede agregar a otros. Si cualquiera pudiera
  agregarse a cualquier grupo, obtendría los permisos de ese grupo sobre
  archivos ajenos.
"""

from collections.abc import Sequence

from sqlalchemy.exc import IntegrityError
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


class NotGroupMemberError(Exception):
    """Quien intenta agregar miembros no pertenece al grupo."""


class GroupService:
    """Casos de uso de creación de grupos y gestión de miembros."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._groups = GroupRepository(session)
        self._users = UserRepository(session)

    def create_group(self, name: str, creator_id: int) -> Group:
        """Crea un grupo nuevo con su creador como miembro.

        Lanza error si el nombre ya está en uso.
        """
        if self._groups.get_by_name(name) is not None:
            raise GroupAlreadyExistsError(name)
        try:
            group = self._groups.create(name)
            self._session.flush()
            self._groups.add_member(group.id, creator_id)
            self._session.commit()
        except IntegrityError as error:
            # Otro usuario creó un grupo con el mismo nombre al mismo tiempo.
            self._session.rollback()
            raise GroupAlreadyExistsError(name) from error
        self._session.refresh(group)
        return group

    def add_member(self, group_id: int, user_id: int, requester_id: int) -> None:
        """Agrega un usuario a un grupo (operación idempotente).

        Solo un miembro del grupo (``requester_id``) puede agregar a otros.
        """
        if self._groups.get_by_id(group_id) is None:
            raise GroupNotFoundError(group_id)
        if self._groups.get_membership(group_id, requester_id) is None:
            raise NotGroupMemberError(group_id)
        if self._users.get_by_id(user_id) is None:
            raise MemberUserNotFoundError(user_id)

        # Si ya es miembro, no se hace nada (evita duplicar la membresía).
        if self._groups.get_membership(group_id, user_id) is None:
            self._groups.add_member(group_id, user_id)
            self._session.commit()

    def list_groups(self) -> Sequence[Group]:
        """Devuelve todos los grupos registrados."""
        return self._groups.list_all()
