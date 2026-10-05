"""Enumeraciones de estado compartidas por el ControlNode, los DataNodes y el cliente.

Centralizar los estados aquí evita que cada componente defina sus propias cadenas
mágicas y garantiza que todos interpreten los mismos valores de la base de datos.
"""

from enum import Enum


class FileStatus(str, Enum):
    """Estados posibles de un archivo durante su ciclo de vida."""

    # El archivo se está subiendo; aún no es visible para los usuarios.
    UPLOADING = "UPLOADING"
    # El archivo está completo y disponible para operaciones de lectura.
    AVAILABLE = "AVAILABLE"
    # La subida falló; el archivo no es utilizable y debe limpiarse.
    FAILED = "FAILED"
    # El archivo está marcado para eliminación y ya no es visible.
    DELETING = "DELETING"


class BlockStatus(str, Enum):
    """Estados posibles de un bloque individual de un archivo."""

    # El bloque fue creado en metadatos pero todavía no se almacenó.
    PENDING = "PENDING"
    # El bloque se está replicando entre los DataNodes.
    REPLICATING = "REPLICATING"
    # El bloque tiene sus dos réplicas válidas (factor de replicación cumplido).
    COMMITTED = "COMMITTED"
    # El bloque no pudo almacenarse con el factor de replicación requerido.
    FAILED = "FAILED"


class NodeStatus(str, Enum):
    """Estados de disponibilidad de un DataNode según el monitor de nodos."""

    # El nodo responde correctamente a las verificaciones de salud.
    ONLINE = "ONLINE"
    # El nodo falló alguna verificación pero aún no supera el umbral de fallos.
    SUSPECT = "SUSPECT"
    # El nodo superó el umbral de fallos y se considera fuera de servicio.
    OFFLINE = "OFFLINE"


class ReplicaRole(str, Enum):
    """Rol de una réplica de bloque dentro de un DataNode."""

    # Réplica usada normalmente para las lecturas.
    PRIMARY = "PRIMARY"
    # Réplica de respaldo, usada ante la caída de la primaria.
    BACKUP = "BACKUP"


class LockType(str, Enum):
    """Tipo de bloqueo aplicado sobre un archivo.

    Por ahora el sistema solo implementa bloqueos de escritura exclusivos.
    """

    # Bloqueo exclusivo de escritura sobre el archivo completo.
    WRITE = "WRITE"


class FileHandleMode(str, Enum):
    """Modo de apertura de un handle de archivo."""

    # Handle abierto solo para lectura.
    READ = "READ"
    # Handle abierto para escritura (requiere bloqueo de escritura).
    WRITE = "WRITE"
