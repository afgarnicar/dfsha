"""Monitor periódico de los DataNodes.

Cada cierto intervalo consulta el /health de todos los DataNodes registrados,
aplica la regla de transición de estados (ONLINE, SUSPECT, OFFLINE) y guarda el
resultado en la base de datos.

Cuando un nodo pasa a OFFLINE se llama a ``on_node_offline``. Por ahora solo
deja un registro en el log; la re-replicación automática se conectará ahí.
"""

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.repositories.datanode_repository import DataNodeRepository
from app.services.datanode_health_client import (
    DataNodeHealthClient,
    HealthCheckResult,
    HealthCheckTarget,
)
from app.services.node_health_policy import NodeHealthState, next_health_state
from shared.enums import NodeStatus

logger = logging.getLogger(__name__)

# Firma del aviso que se dispara cuando un nodo pasa a OFFLINE: (id, nombre).
OfflineCallback = Callable[[int, str], None]


def _log_node_offline(datanode_id: int, name: str) -> None:
    """Aviso por defecto cuando un nodo cae: solo lo registra en el log."""
    logger.warning("DataNode %s (id=%d) pasó a OFFLINE.", name, datanode_id)


class NodeMonitorService:
    """Verifica periódicamente la salud de los DataNodes y actualiza su estado."""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        health_client: DataNodeHealthClient,
        interval_seconds: int,
        failure_threshold: int,
        on_node_offline: OfflineCallback = _log_node_offline,
    ) -> None:
        self._session_factory = session_factory
        self._health_client = health_client
        self._interval_seconds = interval_seconds
        self._failure_threshold = failure_threshold
        self._on_node_offline = on_node_offline

    async def run_forever(self) -> None:
        """Ejecuta verificaciones en ciclo hasta que la tarea sea cancelada.

        Un error en una ronda se registra y no detiene el monitor: la siguiente
        ronda vuelve a intentarlo.
        """
        logger.info(
            "Monitor de DataNodes iniciado (cada %ds, umbral de %d fallos).",
            self._interval_seconds,
            self._failure_threshold,
        )
        while True:
            try:
                await self.check_all_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Error inesperado en la ronda del monitor de DataNodes.")
            await asyncio.sleep(self._interval_seconds)

    async def check_all_once(self) -> None:
        """Hace una ronda completa: consultar todos los nodos y guardar resultados."""
        # El acceso a la base de datos es bloqueante, por eso va en un hilo aparte
        # y no frena el resto del ControlNode mientras tanto.
        targets = await asyncio.to_thread(self._load_targets)
        if not targets:
            return

        results = await self._health_client.check_many(targets)
        nodes_gone_offline = await asyncio.to_thread(self._save_results, results)

        for datanode_id, name in nodes_gone_offline:
            self._on_node_offline(datanode_id, name)

    def _load_targets(self) -> list[HealthCheckTarget]:
        """Lee de la base de datos la lista de nodos a verificar."""
        session = self._session_factory()
        try:
            return [
                HealthCheckTarget(
                    datanode_id=node.id, name=node.name, host=node.host, port=node.port
                )
                for node in DataNodeRepository(session).list_all()
            ]
        finally:
            session.close()

    def _save_results(self, results: list[HealthCheckResult]) -> list[tuple[int, str]]:
        """Aplica la regla de transición a cada nodo y guarda los cambios.

        Devuelve los nodos que acaban de pasar a OFFLINE en esta ronda.
        """
        session = self._session_factory()
        repository = DataNodeRepository(session)
        checked_at = datetime.now(timezone.utc)
        gone_offline: list[tuple[int, str]] = []

        try:
            for result in results:
                node = repository.get_by_id(result.datanode_id)
                if node is None:
                    # El nodo se eliminó mientras se verificaba; se ignora.
                    continue

                previous = NodeHealthState(
                    status=node.status, failure_count=node.failure_count
                )
                new = next_health_state(previous, result.ok, self._failure_threshold)

                repository.update_health(
                    node,
                    status=new.status,
                    failure_count=new.failure_count,
                    checked_at=checked_at,
                    free_space_bytes=result.free_space_bytes,
                )

                if new.status != previous.status:
                    logger.info(
                        "DataNode %s: %s -> %s (fallos consecutivos: %d)%s",
                        node.name,
                        previous.status.value,
                        new.status.value,
                        new.failure_count,
                        f" — {result.error}" if result.error else "",
                    )
                    if new.status == NodeStatus.OFFLINE:
                        gone_offline.append((node.id, node.name))

            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

        return gone_offline
