"""Almacenamiento temporal para reconstruir archivos durante un GET.

Cada descarga trabaja en su propia carpeta ``{temp_root}/{operation_id}/``,
donde se guardan los bloques mientras se descargan y validan. Así el archivo
nunca se carga completo en memoria.

Como varios GET pueden correr al mismo tiempo, el espacio se RESERVA antes de
empezar: si dos descargas grandes llegan juntas, la segunda ve el espacio que
la primera ya apartó y no sobreasigna el disco. La reserva se libera siempre al
terminar, con éxito o con error.

La reserva vive en memoria del proceso. Es suficiente porque el ControlNode
corre como un solo proceso.
"""

import shutil
import threading
import uuid
from pathlib import Path


class InsufficientTempSpaceError(Exception):
    """No hay espacio temporal suficiente para la descarga pedida."""

    def __init__(self, required: int, available: int) -> None:
        super().__init__(
            f"se necesitan {required} bytes temporales y hay {available} disponibles"
        )
        self.required = required
        self.available = available


class TempWorkspace:
    """Carpeta temporal de una descarga y su reserva de espacio."""

    def __init__(self, service: "TemporaryStorageService", path: Path, reserved: int) -> None:
        self._service = service
        self.path = path
        self.reserved_bytes = reserved
        self._released = False

    def part_path(self, block_index: int, suffix: str = "tmp") -> Path:
        """Ruta del archivo temporal de un bloque: part001.tmp, part002.tmp, ..."""
        return self.path / f"part{block_index + 1:03d}.{suffix}"

    def release(self) -> None:
        """Borra la carpeta y libera la reserva. Se puede llamar varias veces."""
        if self._released:
            return
        self._released = True
        shutil.rmtree(self.path, ignore_errors=True)
        self._service._release(self.reserved_bytes)


class TemporaryStorageService:
    """Reserva espacio temporal y crea carpetas de trabajo para los GET."""

    def __init__(self, temp_root: str, margin_percent: int) -> None:
        self._root = Path(temp_root)
        self._margin_percent = margin_percent
        self._reserved = 0
        self._lock = threading.Lock()

    def required_with_margin(self, payload_bytes: int) -> int:
        """Bytes a reservar: lo que se va a escribir más el margen configurado."""
        return payload_bytes + (payload_bytes * self._margin_percent) // 100

    def reserve(self, payload_bytes: int) -> TempWorkspace:
        """Reserva espacio y crea la carpeta de trabajo de una descarga.

        Lanza InsufficientTempSpaceError si el espacio libre del disco, menos lo
        que ya reservaron otras descargas, no alcanza.
        """
        required = self.required_with_margin(payload_bytes)
        self._root.mkdir(parents=True, exist_ok=True)

        with self._lock:
            available = shutil.disk_usage(self._root).free - self._reserved
            if required > available:
                raise InsufficientTempSpaceError(required, max(available, 0))
            self._reserved += required

        path = self._root / uuid.uuid4().hex
        try:
            path.mkdir()
        except OSError:
            self._release(required)
            raise
        return TempWorkspace(self, path, required)

    @property
    def reserved_bytes(self) -> int:
        """Total reservado en este momento por las descargas en curso."""
        with self._lock:
            return self._reserved

    def _release(self, amount: int) -> None:
        with self._lock:
            self._reserved = max(self._reserved - amount, 0)
