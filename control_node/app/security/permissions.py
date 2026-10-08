"""Semántica de permisos estilo Unix sobre el entero ``permissions``.

Los recursos (directorios y archivos) guardan sus permisos en un entero con el
mismo formato que el modo Unix: tres grupos de bits rwx para propietario, grupo
y otros. Este módulo aísla la manipulación de esos bits del resto del sistema.

Significado de los bits según el tipo de recurso:
- Directorios: r = listar, w = crear/eliminar, x = atravesar/entrar.
- Archivos:    r = leer,   w = modificar.
"""

from enum import Enum

# Valores de los bits de permiso, iguales a los de Unix.
_READ = 0b100
_WRITE = 0b010
_EXECUTE = 0b001


class Action(str, Enum):
    """Acción que se desea autorizar sobre un recurso."""

    READ = "read"
    WRITE = "write"
    EXECUTE = "execute"


class Scope(str, Enum):
    """Ámbito del permiso dentro del modo Unix: propietario, grupo u otros."""

    OWNER = "owner"
    GROUP = "group"
    OTHERS = "others"


# Desplazamiento de bits de cada ámbito dentro del entero de permisos.
_SCOPE_SHIFT = {
    Scope.OWNER: 6,
    Scope.GROUP: 3,
    Scope.OTHERS: 0,
}

# Máscara de bits correspondiente a cada acción.
_ACTION_MASK = {
    Action.READ: _READ,
    Action.WRITE: _WRITE,
    Action.EXECUTE: _EXECUTE,
}


def has_permission(permissions: int, scope: Scope, action: Action) -> bool:
    """Indica si el modo concede la acción al ámbito dado.

    Por ejemplo, con permissions=0o750 el propietario tiene rwx, el grupo r-x y
    otros ningún permiso.
    """
    scope_bits = (permissions >> _SCOPE_SHIFT[scope]) & 0b111
    return bool(scope_bits & _ACTION_MASK[action])
