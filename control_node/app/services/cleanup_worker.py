"""Tarea periódica que termina los borrados pendientes.

Cuando un rm no pudo borrar algunas réplicas (por ejemplo, porque un DataNode
estaba OFFLINE), el archivo queda en DELETING. Esta tarea lo reintenta cada
cierto tiempo hasta que todas las réplicas desaparezcan. También limpia los
bloques de las subidas que terminaron en FAILED.
"""

import asyncio
import logging
from collections.abc import Callable

from sqlalchemy.orm import Session

from app.services.delete_service import DeleteService

logger = logging.getLogger(__name__)


class CleanupWorker:
    """Reintenta periódicamente la limpieza de archivos pendientes."""

    def __init__(self, session_factory: Callable[[], Session], interval_seconds: int) -> None:
        self._session_factory = session_factory
        self._interval_seconds = interval_seconds

    async def run_forever(self) -> None:
        """Corre en ciclo hasta que la tarea sea cancelada."""
        logger.info("Limpieza de borrados pendientes iniciada (cada %ds).", self._interval_seconds)
        while True:
            await asyncio.sleep(self._interval_seconds)
            try:
                completed = await asyncio.to_thread(self.run_once)
                if completed:
                    logger.info("Limpieza: %d archivo(s) terminaron de borrarse.", completed)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Error inesperado en la limpieza de borrados pendientes.")

    def run_once(self) -> int:
        """Hace una pasada de limpieza. Devuelve cuántos archivos se completaron."""
        session = self._session_factory()
        try:
            return DeleteService(session).purge_pending()
        finally:
            session.close()
