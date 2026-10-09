"""Consulta del espacio libre en el disco donde el DataNode guarda los bloques."""

import os


def get_free_space_bytes(path: str) -> int:
    """Devuelve los bytes libres disponibles en el sistema de archivos de ``path``.

    Si la carpeta todavía no existe (por ejemplo, el volumen no se montó), se
    devuelve 0 para que el ControlNode no considere al nodo como elegible.
    """
    try:
        stat = os.statvfs(path)
    except FileNotFoundError:
        return 0
    # f_bavail: bloques libres para usuarios no root; f_frsize: tamaño de bloque del FS.
    return stat.f_bavail * stat.f_frsize
