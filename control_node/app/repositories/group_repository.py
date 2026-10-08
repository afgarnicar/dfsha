"""Repositorio de acceso a datos de grupos y membresías."""

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.user import Group, GroupMember


class GroupRepository:
    """Operaciones de persistencia sobre grupos y sus miembros."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_id(self, group_id: int) -> Group | None:
        """Devuelve el grupo con el id dado, o None si no existe."""
        return self._session.get(Group, group_id)

    def get_by_name(self, name: str) -> Group | None:
        """Devuelve el grupo con el nombre dado, o None si no existe."""
        statement = select(Group).where(Group.name == name)
        return self._session.scalars(statement).first()

    def list_all(self) -> Sequence[Group]:
        """Devuelve todos los grupos, ordenados por nombre."""
        statement = select(Group).order_by(Group.name)
        return self._session.scalars(statement).all()

    def create(self, name: str) -> Group:
        """Crea un grupo nuevo."""
        group = Group(name=name)
        self._session.add(group)
        return group

    def get_membership(self, group_id: int, user_id: int) -> GroupMember | None:
        """Devuelve la membresía usuario-grupo, o None si no existe."""
        return self._session.get(GroupMember, {"group_id": group_id, "user_id": user_id})

    def add_member(self, group_id: int, user_id: int) -> GroupMember:
        """Agrega un usuario a un grupo."""
        membership = GroupMember(group_id=group_id, user_id=user_id)
        self._session.add(membership)
        return membership

    def list_group_ids_for_user(self, user_id: int) -> set[int]:
        """Devuelve los ids de los grupos a los que pertenece un usuario."""
        statement = select(GroupMember.group_id).where(GroupMember.user_id == user_id)
        return set(self._session.scalars(statement).all())
