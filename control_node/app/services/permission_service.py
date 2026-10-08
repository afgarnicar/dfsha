"""Servicio de autorización.

Decide si un usuario puede ejecutar una acción (leer, escribir, atravesar)
sobre un recurso protegido. Sigue el orden de resolución de la especificación:

    1. Si existe una ACL específica para el usuario, se usa.
    2. Si no, pero existe una ACL para alguno de sus grupos, se usa.
    3. Si no hay ACL, se aplica el modo Unix: propietario, luego grupo, luego otros.

El servicio no depende de los modelos concretos (directorios o archivos): recibe
una abstracción ``ProtectedResource`` para poder autorizar cualquier recurso.
"""

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models.user import User
from app.repositories.acl_repository import (
    AclRepository,
    SUBJECT_GROUP,
    SUBJECT_USER,
)
from app.repositories.group_repository import GroupRepository
from app.security.permissions import Action, Scope, has_permission


@dataclass(frozen=True)
class ProtectedResource:
    """Datos mínimos de un recurso necesarios para autorizar una acción."""

    resource_type: str
    resource_id: int
    owner_id: int
    group_id: int | None
    permissions: int


class PermissionService:
    """Resuelve decisiones de autorización sobre recursos protegidos."""

    def __init__(self, session: Session) -> None:
        self._acl = AclRepository(session)
        self._groups = GroupRepository(session)

    def can(self, user: User, resource: ProtectedResource, action: Action) -> bool:
        """Indica si el usuario puede ejecutar la acción sobre el recurso."""
        user_group_ids = self._groups.list_group_ids_for_user(user.id)

        # 1. ACL específica del usuario (tiene prioridad sobre todo lo demás).
        user_acl = self._acl.find_for_subject(
            resource.resource_type, resource.resource_id, SUBJECT_USER, user.id
        )
        if user_acl is not None:
            return self._acl_allows(user_acl, action)

        # 2. ACL de alguno de los grupos del usuario.
        for group_id in user_group_ids:
            group_acl = self._acl.find_for_subject(
                resource.resource_type, resource.resource_id, SUBJECT_GROUP, group_id
            )
            if group_acl is not None:
                return self._acl_allows(group_acl, action)

        # 3. Modo Unix: propietario -> grupo -> otros.
        if resource.owner_id == user.id:
            return has_permission(resource.permissions, Scope.OWNER, action)

        if resource.group_id is not None and resource.group_id in user_group_ids:
            return has_permission(resource.permissions, Scope.GROUP, action)

        return has_permission(resource.permissions, Scope.OTHERS, action)

    @staticmethod
    def _acl_allows(entry, action: Action) -> bool:
        """Traduce una entrada de ACL a la decisión para la acción pedida."""
        if action is Action.READ:
            return entry.can_read
        if action is Action.WRITE:
            return entry.can_write
        return entry.can_execute
