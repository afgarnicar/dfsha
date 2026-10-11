"""Tarea periódica de limpieza.

En cada pasada:

- reintenta los rm que quedaron pendientes (archivos en DELETING) y limpia los
  bloques de las subidas que terminaron en FAILED;
- borra las versiones viejas de archivos modificados con write (y las de un
  write que falló);
- cierra los handles vencidos y borra los locks vencidos, por si el cliente
  desapareció sin hacer close.
"""

import asyncio
import logging
from collections.abc import Callable

from sqlalchemy.orm import Session

from app.config import get_settings
from app.services.delete_service import DeleteService
from app.services.handle_service import HandleService

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
        settings = get_settings()
        session = self._session_factory()
        try:
            handles, locks = HandleService(
                session, settings.lock_timeout_seconds, settings.handle_timeout_seconds
            ).sweep_expired()
            if handles or locks:
                logger.info("Limpieza: %d handle(s) y %d lock(s) vencidos liberados.", handles, locks)
            deletes = DeleteService(session, settings.lock_timeout_seconds)
            stale = deletes.purge_stale_versions()
            if stale:
                logger.info("Limpieza: %d bloque(s) de versiones viejas borrados.", stale)
            return deletes.purge_pending()
        finally:
            session.close()
