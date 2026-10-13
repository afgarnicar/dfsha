"""Gestión de permisos de archivos y directorios: chmod, grupo y ACL.

El ControlNode ya decide los accesos con el modelo de la especificación (ACL de
usuario, ACL de grupo, propietario, grupo, otros). Este servicio permite
*cambiar* esos permisos:

- ``chmod``: cambia los bits rwx de propietario, grupo y otros (por ejemplo 750).
- ``chgrp``: asocia el recurso a un grupo (o lo deja sin grupo). El propietario
  solo puede asignar grupos a los que pertenece, como en Unix.
- ACL: excepciones por usuario o por grupo sobre un recurso concreto.

Solo el propietario del recurso puede cambiar sus permisos. Un recurso es un
directorio o un archivo; se ubica por su ruta lógica.
"""

from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.access import AclEntry
from app.models.filesystem import Directory, File
from app.models.user import User
from app.repositories.acl_repository import (
    RESOURCE_DIRECTORY,
    RESOURCE_FILE,
    SUBJECT_GROUP,
    SUBJECT_USER,
    AclRepository,
)
from app.repositories.directory_repository import DirectoryRepository
from app.repositories.file_repository import FileRepository
from app.repositories.group_repository import GroupRepository
from app.repositories.user_repository import UserRepository
from app.services.path_service import PathNotFoundError, PathResolver, split_path
from shared.enums import FileStatus


class NotOwnerError(Exception):
    """Solo el propietario del recurso puede cambiar sus permisos."""


class SubjectNotFoundError(Exception):
    """El usuario o grupo indicado no existe."""


class NotGroupMemberError(Exception):
    """El propietario no pertenece al grupo que quiere asignar."""


class AclEntryNotFoundError(Exception):
    """No hay una entrada de ACL para ese sujeto en el recurso."""


@dataclass(frozen=True)
class AclView:
    """Entrada de ACL con el nombre del sujeto, para mostrarla."""

    subject_type: str
    subject: str
    can_read: bool
    can_write: bool
    can_execute: bool


@dataclass(frozen=True)
class ResourceInfo:
    """Datos de permisos de un archivo o directorio (como un ls -l)."""

    path: str
    resource_type: str
    owner: str
    group: str | None
    permissions: int
    acl: list[AclView]


def mode_string(permissions: int) -> str:
    """Convierte el modo en texto estilo Unix: 0o750 -> 'rwxr-x---'."""
    letters = "rwx"
    return "".join(
        letters[bit % 3] if permissions & (1 << (8 - bit)) else "-" for bit in range(9)
    )


class AccessControlService:
    """Consulta y cambia permisos, grupo y ACL de archivos y directorios."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._resolver = PathResolver(session)
        self._directories = DirectoryRepository(session)
        self._files = FileRepository(session)
        self._acl = AclRepository(session)
        self._users = UserRepository(session)
        self._groups = GroupRepository(session)

    # --- Ubicar el recurso ---------------------------------------------------

    def _locate(self, user: User, path: str) -> tuple[str, Directory | File]:
        """Devuelve el tipo y el recurso (directorio o archivo) de una ruta.

        Para llegar al recurso hay que poder atravesar los directorios del
        camino (permiso x), igual que en cualquier otra operación.
        """
        segments = split_path(path)
        if not segments:
            return RESOURCE_DIRECTORY, self._resolver.resolve(user, "/")

        parent = self._resolver.resolve(user, "/" + "/".join(segments[:-1]))
        directory = self._directories.get_child(parent.id, segments[-1])
        if directory is not None:
            return RESOURCE_DIRECTORY, directory
        file = self._files.get_in_directory(parent.id, segments[-1])
        if file is not None and file.status == FileStatus.AVAILABLE:
            return RESOURCE_FILE, file
        raise PathNotFoundError(path)

    def _locate_owned(self, user: User, path: str) -> tuple[str, Directory | File]:
        """Como ``_locate``, pero exige que el usuario sea el propietario."""
        resource_type, resource = self._locate(user, path)
        if resource.owner_id != user.id:
            raise NotOwnerError(path)
        return resource_type, resource

    def _subject_id(self, subject_type: str, name: str) -> int:
        """Id del usuario o grupo a partir de su nombre."""
        if subject_type == SUBJECT_USER:
            subject = self._users.get_by_username(name)
        else:
            subject = self._groups.get_by_name(name)
        if subject is None:
            raise SubjectNotFoundError(name)
        return subject.id

    def _subject_name(self, subject_type: str, subject_id: int) -> str:
        """Nombre del usuario o grupo a partir de su id."""
        if subject_type == SUBJECT_USER:
            subject = self._users.get_by_id(subject_id)
            return subject.username if subject else f"#{subject_id}"
        subject = self._groups.get_by_id(subject_id)
        return subject.name if subject else f"#{subject_id}"

    # --- Consultar -----------------------------------------------------------

    def info(self, user: User, path: str) -> ResourceInfo:
        """Propietario, grupo, permisos y ACL de un recurso."""
        resource_type, resource = self._locate(user, path)
        owner = self._users.get_by_id(resource.owner_id)
        group = self._groups.get_by_id(resource.group_id) if resource.group_id else None
        entries = self._acl.list_for_resource(resource_type, resource.id)
        return ResourceInfo(
            path=path,
            resource_type=resource_type,
            owner=owner.username if owner else f"#{resource.owner_id}",
            group=group.name if group else None,
            permissions=resource.permissions,
            acl=[self._view(entry) for entry in entries],
        )

    def _view(self, entry: AclEntry) -> AclView:
        return AclView(
            subject_type=entry.subject_type,
            subject=self._subject_name(entry.subject_type, entry.subject_id),
            can_read=entry.can_read,
            can_write=entry.can_write,
            can_execute=entry.can_execute,
        )

    # --- Cambiar -------------------------------------------------------------

    def chmod(self, user: User, path: str, permissions: int) -> ResourceInfo:
        """Cambia los bits rwx del recurso (solo el propietario)."""
        _, resource = self._locate_owned(user, path)
        resource.permissions = permissions
        self._session.commit()
        return self.info(user, path)

    def chgrp(self, user: User, path: str, group_name: str | None) -> ResourceInfo:
        """Asocia el recurso a un grupo, o lo deja sin grupo con ``None``.

        El propietario solo puede asignar un grupo del que sea miembro.
        """
        _, resource = self._locate_owned(user, path)
        if group_name is None:
            resource.group_id = None
        else:
            group_id = self._subject_id(SUBJECT_GROUP, group_name)
            if self._groups.get_membership(group_id, user.id) is None:
                raise NotGroupMemberError(group_name)
            resource.group_id = group_id
        self._session.commit()
        return self.info(user, path)

    def set_acl(
        self,
        user: User,
        path: str,
        subject_type: str,
        subject_name: str,
        can_read: bool,
        can_write: bool,
        can_execute: bool,
    ) -> ResourceInfo:
        """Crea o reemplaza la entrada de ACL de un usuario o grupo."""
        resource_type, resource = self._locate_owned(user, path)
        subject_id = self._subject_id(subject_type, subject_name)
        entry = self._acl.find_for_subject(resource_type, resource.id, subject_type, subject_id)
        if entry is None:
            self._acl.create(
                resource_type, resource.id, subject_type, subject_id,
                can_read, can_write, can_execute,
            )
        else:
            entry.can_read = can_read
            entry.can_write = can_write
            entry.can_execute = can_execute
        self._session.commit()
        return self.info(user, path)

    def remove_acl(
        self, user: User, path: str, subject_type: str, subject_name: str
    ) -> ResourceInfo:
        """Quita la entrada de ACL de un usuario o grupo."""
        resource_type, resource = self._locate_owned(user, path)
        subject_id = self._subject_id(subject_type, subject_name)
        entry = self._acl.find_for_subject(resource_type, resource.id, subject_type, subject_id)
        if entry is None:
            raise AclEntryNotFoundError(subject_name)
        self._acl.delete(entry)
        self._session.commit()
        return self.info(user, path)

    # --- Grupos por nombre ---------------------------------------------------

    def add_group_member(self, user: User, group_name: str, username: str) -> None:
        """Agrega un usuario a un grupo, ambos por nombre.

        Solo un miembro del grupo puede agregar a otros. El creador de un grupo
        queda como miembro al crearlo (ver ``create_group``).
        """
        group_id = self._subject_id(SUBJECT_GROUP, group_name)
        if self._groups.get_membership(group_id, user.id) is None:
            raise NotGroupMemberError(group_name)
        member_id = self._subject_id(SUBJECT_USER, username)
        if self._groups.get_membership(group_id, member_id) is None:
            self._groups.add_member(group_id, member_id)
            self._session.commit()

    def create_group(self, user: User, group_name: str) -> None:
        """Crea un grupo y deja al creador como su primer miembro."""
        if self._groups.get_by_name(group_name) is not None:
            raise GroupNameTakenError(group_name)
        try:
            group = self._groups.create(group_name)
            self._session.flush()
            self._groups.add_member(group.id, user.id)
            self._session.commit()
        except IntegrityError as error:
            # Otro usuario creó un grupo con el mismo nombre al mismo tiempo.
            self._session.rollback()
            raise GroupNameTakenError(group_name) from error


class GroupNameTakenError(Exception):
    """Ya existe un grupo con ese nombre."""
